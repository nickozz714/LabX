"""
services/azure/entra_app_login.py

Inloggen bij een EIGEN Entra-app-registratie met de device-code-flow, en dat
token daarna vanzelf ververst houden.

**Waarom dit moest bestaan.** De bestaande profieltypen dekken Work IQ geen van
drieën. Een `msal_bundle` is een `az`-CLI-sessie, en de Azure CLI krijgt van
Microsoft geen token voor Work IQ — dat is geen instelling maar een weigering
van de identity-service zelf:

    AADSTS65002: Consent between first party application '04b07795…' (Azure CLI)
    and first party resource 'fdcc1f02…' (Work IQ) must be configured via
    preauthorization.

Een `service_principal` helpt ook niet: de Work IQ-permissies bestaan alleen
als DELEGATED, er is geen application-variant. Alles wat Work IQ doet gebeurt
namens een ingelogde mens, en dat is met opzet — het is de reden dat de agent
precies jouw mail ziet en niet die van de hele organisatie. En een `bearer` is
een geplakt token dat na een uur dood is.

Blijft over: een eigen app-registratie waar jij je één keer bij aanmeldt met
een code op een telefoon of in een tweede tabblad, waarna LabX het
verversingstoken bewaart en er zelf tokens mee haalt.

**Eén inlog, meerdere diensten.** Een verversingstoken van Entra hangt aan de
APP en aan wat jij die app hebt toegestaan — niet aan één API. Dezelfde inlog
levert dus zowel een Work IQ-token als een Graph-token, elk met hun eigen
audience. Daarom vraagt de inlog om `offline_access` en vraagt elke aanroep
daarna om precies de scope die híj nodig heeft.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from component_logging import get_logger

log = get_logger(__name__)

AUTORITEIT = "https://login.microsoftonline.com"
# Zonder deze drie krijg je geen verversingstoken terug en is de inlog na een
# uur weer weg. `offline_access` is degene die het doet; de andere twee maken
# de identiteit leesbaar zodat het profiel kan tonen wie er is ingelogd.
BASIS_SCOPES = ("offline_access", "openid", "profile")
# Marge op de vervaltijd: een token dat over tien seconden verloopt is bij een
# trage aanroep al verlopen voordat hij aankomt.
MARGE_SECONDEN = 300


def _url(tenant: str, pad: str) -> str:
    return f"{AUTORITEIT}/{tenant or 'organizations'}/oauth2/v2.0/{pad}"


def scope_regel(scopes: Any) -> str:
    """De scope-parameter zoals Entra hem wil: spatie-gescheiden, met
    `offline_access` erbij. Accepteert een lijst of een losse string, want de
    UI stuurt het ene en de code het andere."""
    if isinstance(scopes, str):
        delen = scopes.replace(",", " ").split()
    else:
        delen = [str(s) for s in (scopes or [])]
    alles = list(dict.fromkeys([*delen, *BASIS_SCOPES]))
    return " ".join(a for a in alles if a)


async def start_device_login(*, tenant_id: str, client_id: str,
                             scopes: Any) -> Dict[str, Any]:
    """Stap 1: een code aanvragen die de gebruiker in de browser invoert.

    Geeft terug wat het scherm moet tonen (`user_code`, `verification_uri`) én
    de `device_code` waarmee stap 2 kan pollen. Die laatste is kortlevend en
    hoort niet in de database — hij gaat heen en weer via de UI.
    """
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            _url(tenant_id, "devicecode"),
            data={"client_id": client_id, "scope": scope_regel(scopes)})
    if resp.status_code >= 400:
        raise RuntimeError(_fout(resp))
    uit = resp.json()
    return {
        "device_code": uit.get("device_code"),
        "user_code": uit.get("user_code"),
        "verification_uri": uit.get("verification_uri"),
        "expires_in": int(uit.get("expires_in") or 900),
        "interval": int(uit.get("interval") or 5),
        "message": uit.get("message"),
    }


async def poll_device_login(*, tenant_id: str, client_id: str,
                            device_code: str) -> Dict[str, Any]:
    """Stap 2: is er al ingelogd?

    Geeft `{"status": "wacht"}` zolang de gebruiker nog bezig is — dat is geen
    fout maar de normale gang van zaken, en het scherm hoort er niet rood van
    te worden. Pas bij een echte weigering komt er een uitzondering.
    """
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            _url(tenant_id, "token"),
            data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                  "client_id": client_id, "device_code": device_code})
    if resp.status_code < 400:
        return {"status": "klaar", **resp.json()}
    fout = (resp.json() or {}).get("error") if _is_json(resp) else None
    if fout in ("authorization_pending", "slow_down"):
        return {"status": "wacht", "error": fout}
    raise RuntimeError(_fout(resp))


async def token_voor_scope(payload: Dict[str, Any], scope: str) -> Tuple[str, Dict[str, Any]]:
    """Een geldig access-token voor één scope, uit het bewaarde profiel.

    Geeft het token terug PLUS de eventueel bijgewerkte payload, zodat de
    aanroeper het nieuwe verversingstoken kan opslaan. Entra geeft bij elke
    verversing een nieuwe uit en trekt de oude na verloop van tijd in; wie dat
    niet bewaart, is na een paar weken stil uitgelogd.
    """
    cache = dict(payload.get("tokens") or {})
    bewaard = cache.get(scope) or {}
    if bewaard.get("access_token") and float(bewaard.get("expires_at") or 0) > time.time() + MARGE_SECONDEN:
        return str(bewaard["access_token"]), payload

    verversing = payload.get("refresh_token")
    if not verversing:
        raise RuntimeError("Dit profiel is nog niet ingelogd — start de device-code-login.")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _url(str(payload.get("tenant_id") or ""), "token"),
            data={"grant_type": "refresh_token", "refresh_token": verversing,
                  "client_id": str(payload.get("client_id") or ""),
                  "scope": scope_regel(scope)})
    if resp.status_code >= 400:
        raise RuntimeError(_fout(resp))
    uit = resp.json()
    token = str(uit.get("access_token") or "")
    if not token:
        raise RuntimeError("Entra gaf geen access_token terug.")

    nieuw = dict(payload)
    if uit.get("refresh_token"):
        nieuw["refresh_token"] = uit["refresh_token"]
    cache[scope] = {"access_token": token,
                    "expires_at": time.time() + float(uit.get("expires_in") or 3600)}
    nieuw["tokens"] = cache
    return token, nieuw


def identiteit_uit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Wie is dit? Uit het id-token, puur om in het scherm te tonen."""
    ruw = payload.get("id_token") or ""
    claims = _claims(ruw)
    return {k: claims.get(k) for k in ("name", "preferred_username", "tid", "oid")
            if claims.get(k)}


def _claims(token: str) -> Dict[str, Any]:
    import base64
    try:
        deel = token.split(".")[1]
        deel += "=" * (-len(deel) % 4)
        return json.loads(base64.urlsafe_b64decode(deel))
    except Exception:  # noqa: BLE001
        return {}


def _is_json(resp: httpx.Response) -> bool:
    return "json" in (resp.headers.get("content-type") or "")


def _fout(resp: httpx.Response) -> str:
    """De foutmelding van Entra zelf doorgeven, niet een eigen samenvatting.

    `AADSTS65002` of `AADSTS65001` vertelt precies wat er mist (geen
    preauthorisatie, geen consent) en dat is wat de gebruiker in Entra moet
    oplossen. Een vertaling naar "inloggen mislukt" maakt dat onvindbaar.
    """
    try:
        uit = resp.json()
        beschrijving = uit.get("error_description") or uit.get("error") or ""
        return str(beschrijving).split("\r\n")[0][:500] or f"HTTP {resp.status_code}"
    except Exception:  # noqa: BLE001
        return f"HTTP {resp.status_code}: {resp.text[:300]}"
