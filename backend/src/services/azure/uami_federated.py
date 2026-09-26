"""
services/azure/uami_federated.py

De managed identity van een klant gebruiken vanaf een server die niet in Azure
draait — zonder secret, zonder IMDS, en zonder iets van ons in Azure.

**Waarom dit kan terwijl een UAMI-token normaal niet te halen is.** Een managed
identity hangt aan een Azure-resource; zijn token komt van het metadata-endpoint
ín die resource, en er is geen sleutel waarmee je van buitenaf "als de UAMI"
kunt inloggen. Workload identity federation draait dat om: op de UAMI registreer
je een FEDERATED CREDENTIAL met een issuer en een subject. Wie een OIDC-token
van die issuer kan tonen met dat subject, krijgt van Entra een access token als
die identiteit.

Dus wordt LabX zijn eigen issuer. Niet als draaiende dienst — alleen twee
STATISCHE bestanden op een publiek adres:

    <issuer>/.well-known/openid-configuration    zegt waar de sleutels staan
    <issuer>/jwks.json                           de publieke sleutel(s)

De privésleutel blijft hier, versleuteld. Bij elke tokenaanvraag tekent LabX een
verse JWT (RS256, `kid` in de kop, tien minuten geldig) en wisselt die bij Entra
in. Entra haalt de JWKS op, controleert de handtekening tegen de federated
credential, en geeft een token terug.

**Wat dat oplevert.** Elke actie in Fabric staat op naam van de UAMI van díé
klant — niet op een gedeeld serviceaccount, en niet op iemands persoonlijke
inlog. Er is geen secret dat verloopt of rondslingert.

**Wat de prijs is.** De privésleutel hier ÍS de identiteit, bij elke klant waar
hij als federated credential staat. Hij hoort dus versleuteld te blijven, en bij
twijfel roteer je hem (zie `nieuwe_sleutel` — de JWKS kan meerdere `kid`s
dragen, zodat een rotatie niemand omvergooit).

**Waarom geen azure-identity.** `ClientAssertionCredential` zou dit ook doen,
maar dan komt er een afhankelijkheid bij voor één POST — en de foutmelding van
Entra (`AADSTS…`) is hier het enige bruikbare houvast, dus die willen we zelf
onverkort doorgeven in plaats van ingepakt in een SDK-exception.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from component_logging import get_logger

log = get_logger(__name__)

# Wat Entra in het `aud`-veld van ONS token wil zien. Vast; de federated
# credential wordt met dezelfde waarde geregistreerd.
TOKEN_EXCHANGE_AUDIENCE = "api://AzureADTokenExchange"

# Hoe lang onze assertie geldig is. Kort, want hij wordt per aanvraag gemaakt en
# hoeft alleen de reis naar Entra te overleven.
ASSERTIE_MINUTEN = 10

# Waar we standaard een token voor vragen.
STANDAARD_SCOPE = "https://api.fabric.microsoft.com/.default"

_ISSUER = re.compile(r"^https://[a-z0-9.-]+(/[A-Za-z0-9._~\-/]*)?$")


def _b64u(ruw: bytes) -> str:
    return base64.urlsafe_b64encode(ruw).decode().rstrip("=")


def normaliseer(data: Any) -> Dict[str, Any]:
    """De instellingen van zo'n profiel, met de scherpe randen eraf.

    De issuer-URL is het gevoeligste veld: hij staat straks in de federated
    credential van elke klant én in elk token dat we tekenen, en die twee moeten
    LETTERLIJK gelijk zijn. Een schuine streep aan het eind is genoeg om het
    stil te laten mislukken — Entra weigert de uitwisseling dan zonder
    foutmelding die daarover gaat."""
    issuer = str(getattr(data, "issuer", "") or "").strip().rstrip("/")
    return {
        "tenant_id": str(getattr(data, "tenant_id", "") or "").strip(),
        # De CLIENT id van de managed identity — niet het object/principal id.
        # Dat eerste hoort in de tokenaanvraag, het tweede in groepen en rollen,
        # en ze verwisselen levert een AADSTS700016 op waar niets over de
        # oorzaak in staat.
        "client_id": str(getattr(data, "client_id", "") or "").strip(),
        "issuer": issuer,
        "subject": str(getattr(data, "subject", "") or "").strip() or "labx",
        "scope": str(getattr(data, "scope", "") or "").strip() or STANDAARD_SCOPE,
    }


def controleer(config: Dict[str, Any]) -> List[str]:
    """Wat er mist of niet kan kloppen, in gewone taal.

    Vooraf en niet pas bij de eerste aanvraag: een verkeerd ingevulde issuer
    levert bij Entra een uitwisseling op die mislukt zonder uitleg, en dan zoek
    je op de verkeerde plek."""
    fouten: List[str] = []
    if not config.get("tenant_id"):
        fouten.append("De tenant-id van de klant ontbreekt.")
    if not config.get("client_id"):
        fouten.append("De client-id van de managed identity ontbreekt.")
    issuer = config.get("issuer") or ""
    if not issuer:
        fouten.append("De issuer-URL ontbreekt.")
    elif not _ISSUER.match(issuer):
        fouten.append("De issuer moet een https-adres zijn zonder poort of queryreeks, "
                      f"bijvoorbeeld https://jouwdomein.nl/oidc — nu: {issuer!r}")
    if not config.get("subject"):
        fouten.append("Het subject ontbreekt; dat moet exact gelijk zijn aan wat er in "
                      "de federated credential staat.")
    return fouten


# ── de sleutel ──────────────────────────────────────────────────────────────

def nieuwe_sleutel(kid: Optional[str] = None) -> Dict[str, str]:
    """Een vers RSA-sleutelpaar, met een `kid` om hem bij naam te kennen.

    Die `kid` is wat een rotatie pijnloos maakt: de JWKS mag er meerdere
    dragen, dus je publiceert de nieuwe sleutel ernáást, zet hem in gebruik, en
    haalt de oude er pas later uit. Zonder `kid` zou elke rotatie een moment
    zijn waarop niets meer werkt."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    sleutel = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    prive = sleutel.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    publiek = sleutel.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return {"kid": kid or uuid.uuid4().hex[:16], "private_pem": prive, "public_pem": publiek}


def jwk_van(public_pem: str, kid: str) -> Dict[str, Any]:
    """De publieke sleutel in de vorm die in een JWKS hoort.

    `use: "sig"` staat er niet voor de sier: Entra verwacht dat veld op de
    sleutels in de discovery, en laat hem anders links liggen."""
    from cryptography.hazmat.primitives import serialization

    pub = serialization.load_pem_public_key(public_pem.encode())
    getallen = pub.public_numbers()
    lengte = (getallen.n.bit_length() + 7) // 8
    return {
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
        "n": _b64u(getallen.n.to_bytes(lengte, "big")),
        "e": _b64u(getallen.e.to_bytes((getallen.e.bit_length() + 7) // 8, "big")),
    }


def issuer_bestanden(config: Dict[str, Any], sleutels: List[Dict[str, str]]) -> Dict[str, Any]:
    """De twee bestanden die publiek moeten staan.

    Dit is het enige stuk van LabX dat de buitenwereld ziet, en het is statisch:
    geen dienst die kan omvallen, geen poort naar de huisserver. Alle velden uit
    de OIDC-discovery die Entra verlangt staan erin — ook de endpoints die voor
    ons geen betekenis hebben, want een ontbrekend veld laat hem het document
    afwijzen.
    """
    issuer = (config.get("issuer") or "").rstrip("/")
    return {
        ".well-known/openid-configuration": {
            "issuer": issuer,
            "jwks_uri": f"{issuer}/jwks.json",
            # Wij geven geen tokens uit aan browsers; deze velden staan er
            # omdat de discovery-spec ze verplicht stelt en Entra het document
            # anders niet accepteert.
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "token_endpoint_auth_methods_supported": ["private_key_jwt"],
            "response_types_supported": ["id_token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid"],
            "claims_supported": ["sub", "iss", "aud", "exp", "iat", "jti"],
        },
        "jwks.json": {"keys": [jwk_van(s["public_pem"], s["kid"]) for s in sleutels]},
    }


# ── de uitwisseling ─────────────────────────────────────────────────────────

def maak_assertie(config: Dict[str, Any], sleutel: Dict[str, str]) -> str:
    """Het token waarmee we ons bij Entra melden.

    `iss` en `sub` moeten LETTERLIJK gelijk zijn aan wat er in de federated
    credential op de UAMI staat; wijkt er iets af, dan weigert Entra de
    uitwisseling. De `kid` in de kop wijst naar de sleutel in onze JWKS."""
    import jwt

    nu = datetime.now(timezone.utc)
    body = {
        "iss": config["issuer"],
        "sub": config["subject"],
        "aud": TOKEN_EXCHANGE_AUDIENCE,
        "iat": int(nu.timestamp()),
        "nbf": int(nu.timestamp()),
        "exp": int((nu + timedelta(minutes=ASSERTIE_MINUTEN)).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(body, sleutel["private_pem"], algorithm="RS256",
                      headers={"kid": sleutel["kid"], "typ": "JWT"})


def claims_van(token: str) -> Dict[str, Any]:
    """De claims uit een JWT, zonder handtekeningcontrole — alleen om te TONEN
    wie dit token is. Het controleren doet Fabric zelf."""
    try:
        deel = token.split(".")[1]
        deel += "=" * (-len(deel) % 4)
        return json.loads(base64.urlsafe_b64decode(deel))
    except Exception:  # noqa: BLE001
        return {}


# De meldingen die je in de praktijk tegenkomt, met wat je eraan doet. De code
# van Entra zelf blijft er onverkort bij staan — dit is uitleg, geen vervanging.
AADSTS_UITLEG = {
    "AADSTS70021": ("Er is geen federated credential op de UAMI die bij deze issuer én dit "
                    "subject past. Controleer of beide LETTERLIJK gelijk zijn (let op een "
                    "schuine streep aan het eind van de issuer)."),
    "AADSTS700211": ("Entra kent deze issuer niet als vertrouwde uitgever. Staat de "
                     "federated credential op de juiste managed identity, en is de "
                     "issuer-URL daar exact zo ingevuld?"),
    "AADSTS700024": ("De assertie is verlopen of nog niet geldig. Vaak staat de klok van "
                     "deze server scheef."),
    "AADSTS700027": ("De handtekening klopt niet. Entra heeft een andere publieke sleutel "
                     "opgehaald dan waarmee getekend is — is de nieuwe jwks.json al "
                     "gepubliceerd, en staat de gebruikte kid erin?"),
    "AADSTS700016": ("Deze client-id bestaat niet in die tenant. Let op: de tokenaanvraag "
                     "heeft de CLIENT id van de managed identity nodig, niet het object- "
                     "of principal-id."),
    "AADSTS900023": "De tenant-id klopt niet.",
}


def _uitleg_bij(melding: str) -> Optional[str]:
    # Langste code eerst: AADSTS70021 is een prefix van AADSTS700211, en die
    # twee betekenen iets heel anders. Op volgorde van de dict zoeken zou de
    # verkeerde uitleg geven bij precies de fout die het lastigst te vinden is.
    for code in sorted(AADSTS_UITLEG, key=len, reverse=True):
        if code in melding:
            return AADSTS_UITLEG[code]
    if "unable to retrieve" in melding.lower() or "keys" in melding.lower():
        return ("Entra kon de sleutels niet ophalen. Staan de twee bestanden publiek, "
                "geven ze application/json terug, en is .well-known niet geblokkeerd "
                "door een regel tegen verborgen mappen?")
    return None


async def haal_token(config: Dict[str, Any], sleutel: Dict[str, str], *,
                     scope: Optional[str] = None,
                     timeout: float = 30.0) -> Dict[str, Any]:
    """Onze assertie inwisselen voor een access token als de managed identity.

    Bij een fout geven we de melding van Entra ONVERKORT door plus, als we hem
    herkennen, wat je eraan doet. Elke `AADSTS…` hier heeft een andere oorzaak
    die je zelf moet oplossen, en "token ophalen mislukt" helpt je bij geen
    enkele."""
    import httpx

    fouten = controleer(config)
    if fouten:
        raise ValueError(" ".join(fouten))

    gevraagd = (scope or config.get("scope") or STANDAARD_SCOPE).strip()
    url = f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"
    formulier = {
        "grant_type": "client_credentials",
        # De client-id van de managed identity: die is hier de "app".
        "client_id": config["client_id"],
        "scope": gevraagd,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": maak_assertie(config, sleutel),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, data=formulier)

    if resp.status_code >= 400:
        melding = resp.text[:600]
        uitleg = _uitleg_bij(melding)
        raise RuntimeError(f"Entra gaf {resp.status_code}: {melding}"
                           + (f"\n\n→ {uitleg}" if uitleg else ""))

    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise RuntimeError(f"Entra gaf geen access_token terug: {json.dumps(body)[:400]}")
    claims = claims_van(token)
    return {
        "token": token,
        "scope": gevraagd,
        "expires_on": int(claims.get("exp") or 0) or None,
        # `oid` is het object-id van de managed identity; daarmee is bij de
        # klant te herkennen wie er aan de knoppen zat.
        "identity": {k: claims.get(k) for k in ("oid", "appid", "tid", "aud", "sub")
                     if claims.get(k)},
    }


def minuten_geldig(expires_on: Optional[int], *, marge: int = 10) -> int:
    """Hoe lang we dit token nog durven te gebruiken.

    Met een marge eraf: een token dat over drie minuten verloopt, is halverwege
    een commando waardeloos."""
    if not expires_on:
        return 50
    over = int(expires_on - datetime.now(timezone.utc).timestamp()) // 60
    return max(1, min(over - marge, 24 * 60)) if over > marge else 1


async def ververs_labgeheimen(db, *, marge_minuten: int = 10) -> Dict[str, Any]:
    """Tokens die bijna verlopen opnieuw ophalen.

    Een lab-geheim van het soort `waarde` ververst zichzelf niet — dat hoefde
    ook niet, want zo'n waarde was altijd iets dat bleef staan. Een token van
    een managed identity leeft een uur, dus zonder dit zou LabX na dat uur een
    verlopen token blijven aanbieden en krijgt een agent een 401 op een moment
    dat niemand keek.

    Draait mee in de achtergrondlus (server.py). Alleen wat bijna om is: een
    tokenaanvraag die niet nodig is, hoort niet elke vijf minuten te gebeuren.
    """
    from models.azure_profile import AzureProfile
    from models.lab_secret import LabSecret
    from utils.crypto import encrypt

    grens = datetime.now(timezone.utc) + timedelta(minutes=max(1, marge_minuten))
    ververst, mislukt = 0, 0
    rijen = (db.query(LabSecret)
             .filter(LabSecret.azure_profile_id.isnot(None),
                     LabSecret.kind == "waarde").all())
    for rij in rijen:
        if rij.refreshed_at:
            try:
                oud = datetime.fromisoformat(str(rij.refreshed_at).replace("Z", "+00:00"))
                if oud.tzinfo is None:
                    oud = oud.replace(tzinfo=timezone.utc)
                if oud + timedelta(minutes=int(rij.ttl_minutes or 50)) > grens:
                    continue            # nog ruim geldig
            except ValueError:
                pass
        profiel = db.get(AzureProfile, int(rij.azure_profile_id))
        if profiel is None or profiel.kind != "uami_federated":
            continue
        from services.azure.azure_profile_service import (
            AzureProfileService, _actieve_sleutel,
        )
        try:
            config = AzureProfileService(db)._decrypt(profiel)
            res = await haal_token(config, _actieve_sleutel(config))
        except Exception as exc:  # noqa: BLE001
            rij.last_error = str(exc)[:900]
            rij.updated_at = datetime.now(timezone.utc).isoformat()
            db.commit()
            mislukt += 1
            log.warningx("UAMI-token verversen mislukt", geheim=rij.name,
                         error=str(exc)[:200])
            continue
        rij.value_encrypted = encrypt(res["token"])
        rij.ttl_minutes = minuten_geldig(res.get("expires_on"))
        rij.refreshed_at = datetime.now(timezone.utc).isoformat()
        rij.updated_at = rij.refreshed_at
        rij.last_error = None
        db.commit()
        ververst += 1
    if ververst or mislukt:
        log.infox("UAMI-tokens ververst", ververst=ververst, mislukt=mislukt)
    return {"ververst": ververst, "mislukt": mislukt}
