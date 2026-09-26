"""
services/azure/azure_profile_service.py

CRUD + verify + sync for named Azure profiles, ported from
ND3X-public/src/services/azure_profile_service.py (single-tenant: no
org_id/project_id scoping). Secrets are Fernet-encrypted at rest and only
decrypted at the moment of a verify or a sync.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.azure_profile import AZURE_PROFILE_KINDS, AzureProfile
from schemas.azure_profile import AzureProfileCreate, AzureProfileUpdate
from utils.crypto import decrypt, encrypt

log = get_logger(__name__)

AZ_BUNDLE_FILES = ("msal_token_cache.json", "azureProfile.json", "service_principal_entries.json")

# De client-id van de Azure CLI zelf. Een msal_token_cache uit `az login` bevat
# refresh tokens die op déze applicatie zijn uitgegeven; wisselen kan alleen met
# dezelfde client_id.
AZ_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
# offline_access is wat de token-endpoint een NIEUW refresh token laat
# teruggeven — zonder die scope krijg je alleen een access token en verloopt het
# profiel alsnog.
AZ_REFRESH_SCOPE = "https://management.azure.com/.default offline_access openid profile"


def _actieve_sleutel(payload: Dict[str, Any]) -> Dict[str, str]:
    """De sleutel waarmee we NU tekenen.

    Er kunnen er meer zijn: bij een rotatie staat de nieuwe alvast in de JWKS
    naast de oude, zodat er geen moment is waarop niets werkt."""
    sleutels = payload.get("sleutels") or []
    if not sleutels:
        raise HTTPException(status_code=400, detail=(
            "Dit profiel heeft geen sleutel. Maak hem opnieuw aan."))
    kid = payload.get("actieve_kid")
    return next((s for s in sleutels if s.get("kid") == kid), sleutels[-1])


def _geheimnaam(profielnaam: str) -> str:
    """De naam waarmee de agent dit token aanhaalt.

    Uit de profielnaam en niet zelf verzonnen: `{{secret:fabric-swinkels}}`
    leest als wat het is, en wie twee klanten naast elkaar heeft, ziet aan de
    verwijzing welke hij pakt."""
    schoon = "".join(c if c.isalnum() or c in "-_" else "-"
                     for c in (profielnaam or "").strip().lower())
    while "--" in schoon:
        schoon = schoon.replace("--", "-")
    return schoon.strip("-")[:64]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_jwt_claims(token: str) -> Dict[str, Any]:
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not decode token: {exc}"}


class AzureProfileService:
    def __init__(self, db: Session):
        self.db = db

    def list(self) -> List[AzureProfile]:
        return self.db.query(AzureProfile).order_by(AzureProfile.name.asc()).all()

    def get_or_404(self, profile_id: int) -> AzureProfile:
        row = self.db.get(AzureProfile, profile_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Azure-profiel niet gevonden")
        return row

    def to_dict(self, p: AzureProfile) -> Dict[str, Any]:
        identity = None
        if p.identity_json:
            try:
                identity = json.loads(p.identity_json)
            except Exception:  # noqa: BLE001
                identity = None
        return {
            "id": p.id, "name": p.name, "kind": p.kind, "description": p.description,
            "has_secret": bool(p.secret_encrypted), "identity": identity,
            # Voor entra_app is "er staat iets versleuteld" niet hetzelfde als
            # "je bent ingelogd": het profiel bestaat al zodra de app-gegevens
            # erin staan. Het scherm moet dat verschil kunnen tonen.
            **self._inlogstatus(p),
            "created_at": p.created_at, "updated_at": p.updated_at,
        }

    def _inlogstatus(self, p: AzureProfile) -> Dict[str, Any]:
        if p.kind != "entra_app":
            return {}
        try:
            payload = json.loads(decrypt(p.secret_encrypted)) if p.secret_encrypted else {}
        except Exception:  # noqa: BLE001
            return {"logged_in": False}
        return {"logged_in": bool(payload.get("refresh_token")),
                "tenant_id": payload.get("tenant_id"),
                "client_id": payload.get("client_id"),
                "scopes": payload.get("scopes") or []}

    def _payload_from_input(self, kind: str, data: Any) -> Dict[str, Any]:
        if kind == "msal_bundle":
            files = {k: v for k, v in (data.files or {}).items() if k in AZ_BUNDLE_FILES and v}
            if not files.get("msal_token_cache.json") or not files.get("azureProfile.json"):
                raise HTTPException(status_code=400, detail=(
                    "msal_bundle vereist minimaal msal_token_cache.json en azureProfile.json."))
            return files
        if kind == "service_principal":
            if not (data.tenant_id and data.client_id and data.client_secret):
                raise HTTPException(status_code=400, detail=(
                    "service_principal vereist tenant_id, client_id en client_secret."))
            return {"tenant_id": data.tenant_id, "client_id": data.client_id, "client_secret": data.client_secret}
        if kind == "bearer":
            if not data.token:
                raise HTTPException(status_code=400, detail="bearer vereist een token.")
            return {"token": data.token}
        if kind == "entra_app":
            # Alleen de app-gegevens; het inloggen is een APARTE stap. Zo kun
            # je een profiel aanmaken voordat de beheerder klaar is met
            # toestemming geven, en later inloggen zonder alles opnieuw te
            # typen. Geen client_secret: een device-code-login is een public
            # client, en een secret zou hier alleen maar meeliften.
            if not (data.tenant_id and data.client_id):
                raise HTTPException(status_code=400, detail=(
                    "entra_app vereist tenant_id en client_id van je eigen app-registratie."))
            return {"tenant_id": data.tenant_id, "client_id": data.client_id,
                    "scopes": [s for s in (data.scopes or []) if s]}
        if kind == "uami_federated":
            from services.azure import uami_federated as uf
            config = uf.normaliseer(data)
            fouten = uf.controleer(config)
            if fouten:
                raise HTTPException(status_code=400, detail=" ".join(fouten))
            # De sleutel maakt LabX zelf; die hoort niet in een formulier en
            # mag de server nooit verlaten. De publieke helft gaat straks in
            # het bestand dat je publiceert.
            sleutel = uf.nieuwe_sleutel()
            config["sleutels"] = [sleutel]
            config["actieve_kid"] = sleutel["kid"]
            return config
        raise HTTPException(status_code=400, detail=f"kind moet een van {AZURE_PROFILE_KINDS} zijn")


    # ── device-code-login voor een eigen app-registratie ────────────────────

    async def device_login_start(self, profile_id: int,
                                 extra_scopes: Optional[List[str]] = None) -> Dict[str, Any]:
        """Stap 1: de code die de gebruiker in de browser invoert."""
        from services.azure import entra_app_login

        row = self.get_or_404(profile_id)
        if row.kind != "entra_app":
            raise HTTPException(status_code=400,
                                detail="Device-code-login werkt alleen voor een entra_app-profiel.")
        payload = self._decrypt(row)
        scopes = list(payload.get("scopes") or []) + list(extra_scopes or [])
        try:
            return await entra_app_login.start_device_login(
                tenant_id=str(payload.get("tenant_id") or ""),
                client_id=str(payload.get("client_id") or ""), scopes=scopes)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    async def device_login_poll(self, profile_id: int, device_code: str) -> Dict[str, Any]:
        """Stap 2: wachten tot de gebruiker klaar is, en dan bewaren.

        Het verversingstoken is het enige dat er echt toe doet: daarmee kan
        LabX later zelf tokens halen voor elke API waar deze app toestemming
        voor heeft, zonder de gebruiker opnieuw lastig te vallen.
        """
        from services.azure import entra_app_login

        row = self.get_or_404(profile_id)
        payload = self._decrypt(row)
        try:
            uit = await entra_app_login.poll_device_login(
                tenant_id=str(payload.get("tenant_id") or ""),
                client_id=str(payload.get("client_id") or ""), device_code=device_code)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if uit.get("status") != "klaar":
            return {"status": "wacht"}

        payload["refresh_token"] = uit.get("refresh_token")
        payload["id_token"] = uit.get("id_token")
        payload["tokens"] = {}          # verse inlog, oude tokencache vervalt
        row.secret_encrypted = encrypt(json.dumps(payload))
        identiteit = entra_app_login.identiteit_uit(payload)
        row.identity_json = json.dumps(identiteit) if identiteit else None
        row.updated_at = _now_iso()
        self.db.commit()
        self.db.refresh(row)
        return {"status": "klaar", "identity": identiteit}

    async def token_for(self, profile_id_or_row: Any, scope: str) -> str:
        """Een geldig token voor deze scope, en de verversing meteen bewaard.

        Entra geeft bij elke verversing een NIEUW verversingstoken uit en trekt
        het oude na verloop van tijd in. Wie dat niet terugschrijft, is na een
        paar weken stil uitgelogd — en dat is precies het soort storing dat je
        pas merkt als je het nodig hebt.
        """
        from services.azure import entra_app_login

        row = (profile_id_or_row if isinstance(profile_id_or_row, AzureProfile)
               else self.get_or_404(int(profile_id_or_row)))
        payload = self._decrypt(row)
        if row.kind == "uami_federated":
            # Hier valt niets te verversen: elke aanvraag tekent een verse
            # assertie en wisselt die in.
            from services.azure import uami_federated as uf
            res = await uf.haal_token(payload, _actieve_sleutel(payload), scope=scope)
            return res["token"]
        token, nieuw = await entra_app_login.token_voor_scope(payload, scope)
        if nieuw != payload:
            row.secret_encrypted = encrypt(json.dumps(nieuw))
            row.updated_at = _now_iso()
            self.db.commit()
        return token

    def _decrypt(self, p: AzureProfile) -> Dict[str, Any]:
        if not p.secret_encrypted:
            raise HTTPException(status_code=400, detail="Dit profiel heeft geen opgeslagen secret.")
        try:
            return json.loads(decrypt(p.secret_encrypted))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Kon profiel-secret niet ontsleutelen: {exc}")

    def create(self, data: AzureProfileCreate) -> AzureProfile:
        kind = (data.kind or "msal_bundle").strip().lower()
        if kind not in AZURE_PROFILE_KINDS:
            raise HTTPException(status_code=400, detail=f"kind moet een van {AZURE_PROFILE_KINDS} zijn")
        payload = self._payload_from_input(kind, data)
        now = _now_iso()
        row = AzureProfile(name=data.name.strip(), kind=kind, description=data.description,
                           secret_encrypted=encrypt(json.dumps(payload)),
                           created_at=now, updated_at=now)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, profile_id: int, data: AzureProfileUpdate) -> AzureProfile:
        row = self.get_or_404(profile_id)
        if data.name is not None:
            row.name = data.name.strip()
        if data.description is not None:
            row.description = data.description
        if any(v is not None for v in (data.files, data.tenant_id, data.client_id,
                                       data.client_secret, data.token, data.scopes)):
            payload = self._payload_from_input(row.kind, data)
            if row.kind == "entra_app" and row.secret_encrypted:
                # De inlog niet weggooien bij het bijwerken van een naam of een
                # scope: opnieuw moeten inloggen omdat je een scope toevoegde,
                # is precies het soort straf waar niemand op zit te wachten.
                oud = self._decrypt(row)
                for sleutel in ("refresh_token", "id_token", "tokens"):
                    if oud.get(sleutel) is not None:
                        payload[sleutel] = oud[sleutel]
            row.secret_encrypted = encrypt(json.dumps(payload))
            row.identity_json = None
        row.updated_at = _now_iso()
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, profile_id: int) -> None:
        row = self.get_or_404(profile_id)
        self.db.delete(row)
        self.db.commit()

    @staticmethod
    def _read_host_bundle() -> Dict[str, str]:
        import os
        from pathlib import Path
        host_dir = Path(os.environ.get("AZURE_CONFIG_DIR") or (Path.home() / ".azure"))
        files: Dict[str, str] = {}
        for fname in AZ_BUNDLE_FILES:
            fp = host_dir / fname
            if fp.exists():
                try:
                    files[fname] = fp.read_text(encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
        if not files.get("msal_token_cache.json") or not files.get("azureProfile.json"):
            raise HTTPException(status_code=400, detail="Geen host az-sessie gevonden. Log eerst in met 'az login'.")
        return files

    async def _read_lab_bundle(self, lab_id: str, az_dir: str = "/root/.azure") -> Dict[str, str]:
        """De az-bestanden uit een LAB halen.

        Dit is de andere kant van de sync: normaal duwen we een profiel een lab
        in, maar bij een interactieve login gebeurt het omgekeerde — je logt in
        de browser van het lab in (of via een tunnel naar je eigen browser), en
        de sessie ontstaat daar. Zonder deze stap blijft die login in dat ene
        lab hangen en weet LabX er niets van."""
        from services.lab.lab_service import LabService

        svc = LabService(self.db)
        files: Dict[str, str] = {}
        for fname in AZ_BUNDLE_FILES:
            inhoud = await svc.read_lab_file_raw(lab_id, f"{az_dir}/{fname}")
            if inhoud:
                files[fname] = inhoud
        if not files.get("msal_token_cache.json") or not files.get("azureProfile.json"):
            raise HTTPException(status_code=400, detail=(
                "Geen az-sessie in dit lab gevonden. Log eerst in het lab in — via het "
                "Browser-tabblad, of met 'az login --use-device-code' in de shell."))
        return files

    async def capture_from_lab(self, *, lab_id: str, name: str,
                               description: Optional[str] = None) -> AzureProfile:
        data = AzureProfileCreate(name=name, kind="msal_bundle", description=description,
                                  files=await self._read_lab_bundle(lab_id))
        return self.create(data)

    async def recapture_from_lab(self, profile_id: int, *, lab_id: str) -> AzureProfile:
        """"Ik heb net in dit lab opnieuw ingelogd" — dezelfde knop als
        recapture_from_host, maar dan met het lab als bron."""
        row = self.get_or_404(profile_id)
        if row.kind != "msal_bundle":
            raise HTTPException(status_code=400, detail=(
                "Alleen een 'msal_bundle'-profiel bestaat uit az-bestanden."))
        files = await self._read_lab_bundle(lab_id)
        row.secret_encrypted = encrypt(json.dumps(files))
        row.identity_json = None
        row.updated_at = _now_iso()
        self.db.commit()
        self.db.refresh(row)
        return row

    def capture_from_host(self, *, name: str, description: Optional[str] = None) -> AzureProfile:
        data = AzureProfileCreate(name=name, kind="msal_bundle", description=description,
                                  files=self._read_host_bundle())
        return self.create(data)

    def recapture_from_host(self, profile_id: int) -> AzureProfile:
        """De bestanden van dit profiel opnieuw van de host halen — de knop voor
        'ik heb net opnieuw ingelogd'. Vervangt alleen het secret; naam,
        omschrijving en alles wat naar dit profiel verwijst blijven staan."""
        row = self.get_or_404(profile_id)
        if row.kind != "msal_bundle":
            raise HTTPException(status_code=400, detail=(
                "Alleen een 'msal_bundle'-profiel bestaat uit az-bestanden."))
        files = self._read_host_bundle()
        row.secret_encrypted = encrypt(json.dumps(files))
        row.identity_json = None
        row.updated_at = _now_iso()
        self.db.commit()
        self.db.refresh(row)
        return row

    async def refresh_tokens(self, profile_id: int) -> Dict[str, Any]:
        """Het refresh token inwisselen voor een vers paar en terugschrijven.

        Een az-sessie verloopt niet omdat het access token oud is — dat wisselt
        de CLI zelf wel — maar omdat het REFRESH token verloopt als er lang niets
        mee gebeurt. Een profiel dat alleen in de kluis ligt, gebruikt niemand,
        dus verloopt het juist wél. Deze knop houdt het levend: nieuw refresh
        token erin, verlopen access tokens eruit.
        """
        row = self.get_or_404(profile_id)
        payload = self._decrypt(row)
        if row.kind == "service_principal":
            # Een service principal kent geen refresh token; een geldig
            # client_secret ís de vernieuwing. Verifiëren is hier het antwoord.
            identity = await self.verify(profile_id)
            return {"ok": "error" not in identity, "kind": row.kind,
                    "detail": "Service principal: opnieuw een token gemint.",
                    "identity": identity}
        if row.kind != "msal_bundle":
            raise HTTPException(status_code=400, detail=(
                "Een 'bearer'-profiel kan niet vernieuwd worden: er hoort geen "
                "refresh token bij. Plak een nieuw token of gebruik een ander soort profiel."))

        try:
            cache = json.loads(payload.get("msal_token_cache.json") or "{}")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"msal_token_cache.json is geen geldige JSON: {exc}")
        if not isinstance(cache, dict):
            # Geldige JSON hoeft nog geen token-cache te zijn (een half geplakt
            # bestand levert bijvoorbeeld een losse string op).
            raise HTTPException(status_code=400, detail=(
                "msal_token_cache.json bevat geen token-cache (verwacht een JSON-object)."))
        entries = cache.get("RefreshToken") or {}
        if not entries:
            raise HTTPException(status_code=400, detail=(
                "Geen refresh token in msal_token_cache.json. Log opnieuw in met 'az login' "
                "en vervang de bestanden van dit profiel."))

        tenant = self._tenant_from_bundle(payload) or "organizations"
        import httpx
        renewed, failures = 0, []
        async with httpx.AsyncClient(timeout=30) as client:
            for key, entry in list(entries.items()):
                secret = (entry or {}).get("secret")
                if not secret:
                    continue
                resp = await client.post(
                    f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                    data={"client_id": (entry.get("client_id") or AZ_CLI_CLIENT_ID),
                          "grant_type": "refresh_token", "refresh_token": secret,
                          "scope": AZ_REFRESH_SCOPE},
                )
                if resp.status_code >= 400:
                    failures.append(self._token_error(resp))
                    continue
                body = resp.json()
                new_rt = body.get("refresh_token")
                if new_rt:
                    entry["secret"] = new_rt
                    entries[key] = entry
                    renewed += 1
                    # Meteen wegschrijven: Azure verzilvert een refresh token
                    # éénmalig. Pas opslaan na de lus zou bij een fout halverwege
                    # betekenen dat het oude token al verbruikt is en het nieuwe
                    # nergens staat — dan is het profiel dood.
                    cache["RefreshToken"] = entries
                    payload["msal_token_cache.json"] = json.dumps(cache)
                    row.secret_encrypted = encrypt(json.dumps(payload))
                    self.db.commit()

        if not renewed:
            raise HTTPException(status_code=400, detail=(
                "Vernieuwen mislukt: " + ("; ".join(failures) or "Azure gaf geen nieuw refresh token terug") +
                ". Log opnieuw in met 'az login' en vervang de bestanden van dit profiel."))

        # Access tokens zijn na een refresh sowieso achterhaald; ze weglaten
        # dwingt de CLI er een verse te halen in plaats van op een verlopen te
        # stuiten.
        cache.pop("AccessToken", None)
        payload["msal_token_cache.json"] = json.dumps(cache)
        row.secret_encrypted = encrypt(json.dumps(payload))
        row.updated_at = _now_iso()
        self.db.commit()

        identity = await self.verify(profile_id)
        identity["refreshed_at"] = _now_iso()
        row.identity_json = json.dumps(identity)
        self.db.commit()
        return {"ok": True, "kind": row.kind, "renewed": renewed,
                "detail": (f"{renewed} refresh token(s) vernieuwd" +
                           (f"; {len(failures)} mislukt: {'; '.join(failures)}" if failures else "")),
                "identity": identity}

    @staticmethod
    def _token_error(resp: Any) -> str:
        try:
            body = resp.json()
            return str(body.get("error_description") or body.get("error") or resp.text)[:300]
        except Exception:  # noqa: BLE001
            return str(resp.text)[:300]

    @staticmethod
    def _tenant_from_bundle(payload: Dict[str, Any]) -> Optional[str]:
        try:
            prof = json.loads(payload.get("azureProfile.json") or "{}")
            subs = prof.get("subscriptions") or []
            active = next((s for s in subs if s.get("isDefault")), (subs[0] if subs else {}))
            return active.get("tenantId")
        except Exception:  # noqa: BLE001
            return None

    async def verify(self, profile_id: int) -> Dict[str, Any]:
        row = self.get_or_404(profile_id)
        payload = self._decrypt(row)
        identity: Dict[str, Any] = {"kind": row.kind}
        if row.kind == "bearer":
            claims = _decode_jwt_claims(payload.get("token") or "")
            identity.update({k: claims.get(k) for k in ("tid", "upn", "appid", "aud", "name") if claims.get(k)})
            if claims.get("error"):
                identity["error"] = claims["error"]
        elif row.kind == "service_principal":
            identity.update({"tenant_id": payload.get("tenant_id"), "client_id": payload.get("client_id")})
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"https://login.microsoftonline.com/{payload['tenant_id']}/oauth2/v2.0/token",
                        data={"client_id": payload["client_id"], "client_secret": payload["client_secret"],
                             "scope": "https://management.azure.com/.default",
                             "grant_type": "client_credentials"},
                    )
                    resp.raise_for_status()
                    token = resp.json().get("access_token") or ""
                claims = _decode_jwt_claims(token)
                identity.update({k: claims.get(k) for k in ("tid", "appid", "aud") if claims.get(k)})
            except Exception as exc:  # noqa: BLE001 — verify is best-effort
                identity["error"] = f"kon geen SP-token minten: {exc}"
        elif row.kind == "uami_federated":
            # Proberen of Entra ons token accepteert, en WIE we dan blijken te
            # zijn. Dat laatste is de vraag: staat straks bij de klant de
            # managed identity op de actie, of iets anders?
            from services.azure import uami_federated as uf
            identity.update({"issuer": payload.get("issuer"),
                             "subject": payload.get("subject"),
                             "client_id": payload.get("client_id"),
                             "kid": payload.get("actieve_kid")})
            try:
                res = await uf.haal_token(payload, _actieve_sleutel(payload))
                identity.update(res.get("identity") or {})
                identity["expires_on"] = res.get("expires_on")
            except Exception as exc:  # noqa: BLE001 — verify is best-effort
                identity["error"] = str(exc)[:900]
        else:  # msal_bundle
            try:
                prof = json.loads(payload.get("azureProfile.json") or "{}")
                subs = prof.get("subscriptions") or []
                active = next((s for s in subs if s.get("isDefault")), (subs[0] if subs else {}))
                user = (active.get("user") or {})
                identity.update({
                    "account": user.get("name"), "tenant_id": active.get("tenantId"),
                    "subscription": active.get("name"), "subscription_id": active.get("id"),
                })
            except Exception as exc:  # noqa: BLE001
                identity["error"] = f"kon azureProfile.json niet parsen: {exc}"
        row.identity_json = json.dumps(identity)
        self.db.commit()
        return identity

    async def apply_everywhere(self, profile_id: int) -> Dict[str, Any]:
        """De sessie doorzetten naar álles wat dit profiel gebruikt.

        Bestaat omdat de losse stappen (verifiëren, naar de host syncen, per lab
        syncen) samen één handeling zijn: na een verse login of een vernieuwing
        is de opgeslagen bundel veranderd, en dan hébben de host en de draaiende
        labs per definitie een oude. Ze één voor één moeten aanklikken is niet
        alleen omslachtig, het is ook makkelijk half te doen — en een lab met een
        halve az-sessie faalt pas als de agent er iets mee probeert.

        Best-effort per doel: een lab dat niet draait is geen fout (het krijgt de
        sessie bij de volgende start, zie LabService._sync_azure_profile_into_lab).
        """
        row = self.get_or_404(profile_id)
        steps: List[Dict[str, Any]] = []

        identity = await self.verify(profile_id)
        steps.append({"target": "identiteit", "ok": "error" not in identity,
                      "detail": identity.get("error") or self._identity_line(identity)})

        if row.kind == "bearer":
            steps.append({"target": "host", "ok": False,
                          "detail": "Een bearer-profiel kent geen az-sessie om door te zetten."})
        else:
            try:
                res = await self._sync_to_host(row, self._decrypt(row))
                steps.append({"target": "host", "ok": bool(res.get("ok")),
                              "detail": "az-sessie op de LabX-host bijgewerkt"})
            except Exception as exc:  # noqa: BLE001
                steps.append({"target": "host", "ok": False, "detail": str(exc)[:200]})

        if row.kind == "msal_bundle":
            from models.lab import Lab
            labs = self.db.query(Lab).filter(Lab.azure_profile_id == profile_id).all()
            if not labs:
                steps.append({"target": "labs", "ok": True,
                              "detail": "Geen lab gebruikt dit profiel."})
            for lab in labs:
                if lab.status != "running":
                    steps.append({"target": f"lab {lab.name}", "ok": True,
                                  "detail": "staat uit — krijgt de sessie bij de volgende start"})
                    continue
                try:
                    # Via LabService, want die kent de werkers. Rechtstreeks
                    # syncen zou alleen werker 1 raken en de rest met een oude
                    # sessie laten zitten — het gat uit KRI-44.
                    from services.lab.lab_service import LabService
                    await LabService(self.db)._sync_azure_profile_into_lab(lab)
                    res = {"ok": True}
                    steps.append({"target": f"lab {lab.name}", "ok": bool(res.get("ok")),
                                  "detail": "az-sessie in het lab bijgewerkt" if res.get("ok")
                                            else str(res.get("detail"))[:200]})
                except Exception as exc:  # noqa: BLE001
                    steps.append({"target": f"lab {lab.name}", "ok": False, "detail": str(exc)[:200]})

        return {"ok": all(s["ok"] for s in steps), "steps": steps}

    @staticmethod
    def _identity_line(identity: Dict[str, Any]) -> str:
        parts = [str(identity.get(k)) for k in ("account", "subscription", "tenant_id", "appid")
                 if identity.get(k)]
        return " · ".join(parts) or "geverifieerd"

    async def _uami_naar_lab(self, row: AzureProfile, payload: Dict[str, Any],
                             lab_id: str) -> Dict[str, Any]:
        """Een vers token als LAB-GEHEIM neerzetten.

        Niet als `az login`: er valt niets in te loggen — de identiteit blijft
        van de klant en wij hebben alleen een token. Als lab-geheim past het
        wél precies: het model schrijft `{{secret:naam}}`, LabX vult het in
        vlak voor het commando via een bestand in de container, en de waarde
        staat dus op geen enkele commandoregel en niet in het spoor.
        """
        from services.azure import uami_federated as uf
        from services.lab.secrets import SecretService, geldige_naam

        naam = _geheimnaam(row.name)
        if not geldige_naam(naam):
            raise HTTPException(status_code=400, detail=(
                f"De naam '{row.name}' levert geen bruikbare geheimnaam op. "
                "Gebruik letters, cijfers, - of _ in de profielnaam."))
        try:
            res = await uf.haal_token(payload, _actieve_sleutel(payload))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)[:900])
        svc = SecretService(self.db)
        rij = svc.zet(lab_id, naam=naam, waarde=res["token"],
                      omschrijving=(f"Token van '{row.name}' voor {res['scope']} "
                                    "— automatisch ververst"),
                      ttl_minuten=uf.minuten_geldig(res.get("expires_on")))
        rij.azure_profile_id = row.id
        self.db.commit()
        return {"ok": True, "target": "lab", "detail": {
            "geheim": naam, "gebruik": f"{{{{secret:{naam}}}}}",
            "scope": res["scope"], "identity": res.get("identity") or {},
            "geldig_tot": res.get("expires_on"),
        }}

    def issuer_bestanden(self, profile_id: int) -> Dict[str, Any]:
        """De twee bestanden die publiek moeten staan.

        Dit is het enige dat de buitenwereld van LabX ziet, en het is statisch.
        Ze hier laten zien in plaats van een script: je publiceert ze één keer
        en daarna nooit meer, behalve bij een rotatie."""
        from services.azure import uami_federated as uf

        row = self.get_or_404(profile_id)
        if row.kind != "uami_federated":
            raise HTTPException(status_code=400, detail=(
                "Alleen een gefedereerde managed identity heeft issuer-bestanden."))
        payload = self._decrypt(row)
        bestanden = uf.issuer_bestanden(payload, payload.get("sleutels") or [])
        return {"issuer": payload.get("issuer"), "actieve_kid": payload.get("actieve_kid"),
                "bestanden": bestanden,
                "subject": payload.get("subject"),
                "audience": uf.TOKEN_EXCHANGE_AUDIENCE}

    def roteer_sleutel(self, profile_id: int) -> Dict[str, Any]:
        """Een nieuwe sleutel ernáást zetten en daarmee gaan tekenen.

        De oude blijft in de JWKS staan: publiceer het nieuwe bestand eerst,
        want Entra haalt de sleutels op wanneer het hém uitkomt. Pas als alles
        weer loopt haal je de oude eruit met `vergeet_oude_sleutels`."""
        from services.azure import uami_federated as uf

        row = self.get_or_404(profile_id)
        if row.kind != "uami_federated":
            raise HTTPException(status_code=400, detail="Dit profiel heeft geen sleutel.")
        payload = self._decrypt(row)
        nieuw = uf.nieuwe_sleutel()
        payload["sleutels"] = (payload.get("sleutels") or []) + [nieuw]
        payload["actieve_kid"] = nieuw["kid"]
        row.secret_encrypted = encrypt(json.dumps(payload))
        row.updated_at = _now_iso()
        self.db.commit()
        return self.issuer_bestanden(profile_id)

    def vergeet_oude_sleutels(self, profile_id: int) -> Dict[str, Any]:
        """Alles behalve de actieve sleutel weggooien.

        Pas doen als de nieuwe JWKS gepubliceerd is en er weer tokens
        binnenkomen — anders haal je de sleutel weg waar Entra nog mee rekent."""
        row = self.get_or_404(profile_id)
        payload = self._decrypt(row)
        kid = payload.get("actieve_kid")
        payload["sleutels"] = [s for s in (payload.get("sleutels") or [])
                               if s.get("kid") == kid]
        row.secret_encrypted = encrypt(json.dumps(payload))
        row.updated_at = _now_iso()
        self.db.commit()
        return self.issuer_bestanden(profile_id)

    async def sync(self, profile_id: int, *, target: str, lab_id: Optional[str] = None,
                   az_dir: str = "/root/.azure",
                   worker_id: Optional[int] = None) -> Dict[str, Any]:
        row = self.get_or_404(profile_id)
        payload = self._decrypt(row)
        if target == "host":
            return await self._sync_to_host(row, payload)
        if target == "lab":
            if not lab_id:
                raise HTTPException(status_code=400, detail="lab_id is verplicht voor een lab-sync.")
            return await self._sync_to_lab(row, payload, lab_id, az_dir, worker_id)
        raise HTTPException(status_code=400, detail="target moet 'host' of 'lab' zijn.")

    async def _sync_to_host(self, row: AzureProfile, payload: Dict[str, Any]) -> Dict[str, Any]:
        from services.azure.az_login_service import import_token_files, start_service_principal
        if row.kind == "msal_bundle":
            res = import_token_files(
                msal_token_cache=payload["msal_token_cache.json"],
                azure_profile=payload["azureProfile.json"],
                service_principal_entries=payload.get("service_principal_entries.json"))
            return {"ok": True, "target": "host", "detail": res}
        if row.kind == "service_principal":
            res = await start_service_principal(
                tenant_id=payload["tenant_id"], client_id=payload["client_id"],
                client_secret=payload["client_secret"])
            return {"ok": bool(res.get("ok")), "target": "host", "detail": res}
        raise HTTPException(status_code=400, detail=(
            "Een 'bearer'-profiel kan niet naar de host gesynct worden (geen login). "
            "Gebruik het als opgeslagen credential."))

    async def _sync_to_lab(self, row: AzureProfile, payload: Dict[str, Any],
                           lab_id: str, az_dir: str,
                           worker_id: Optional[int] = None) -> Dict[str, Any]:
        if row.kind == "uami_federated":
            return await self._uami_naar_lab(row, payload, lab_id)
        if row.kind != "msal_bundle":
            raise HTTPException(status_code=400, detail=(
                "Alleen een 'msal_bundle'-profiel kan naar een lab gesynct worden. "
                "Voor een service principal: draai 'az login --service-principal' in het lab."))
        from services.lab.lab_service import LabService
        files = {k: v for k, v in payload.items() if k in AZ_BUNDLE_FILES}
        res = await LabService(self.db).az_login(lab_id, az_dir=az_dir, files=files,
                                                 worker_id=worker_id)
        return {"ok": bool(res.get("ok")), "target": "lab", "detail": res}
