"""Een managed identity gebruiken vanaf een server die niet in Azure draait.

Workload identity federation draait het probleem om: op de UAMI staat een
federated credential met een issuer en een subject, en wie een OIDC-token van
die issuer kan tonen met dat subject, krijgt van Entra een token als die
identiteit. LabX wordt dus zijn eigen issuer — twee statische bestanden publiek,
de privésleutel hier.

Deze tests leggen vast wat daarbij niet mag schuiven: de vorm van die twee
bestanden (Entra weigert een discovery-document met een ontbrekend veld), de
assertie die exact moet matchen met wat er bij de klant geregistreerd is, en de
foutmeldingen — want elke AADSTS-code heeft een andere oorzaak die je zelf moet
oplossen.
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.azure import uami_federated as uf  # noqa: E402

ISSUER = "https://nickduchatinier.nl/oidc"


@pytest.fixture(scope="module")
def sleutel():
    return uf.nieuwe_sleutel(kid="test-kid-1")


def _config(**over):
    basis = {"tenant_id": "tenant-abc", "client_id": "uami-client-id",
             "issuer": ISSUER, "subject": "labx", "scope": uf.STANDAARD_SCOPE}
    basis.update(over)
    return basis


# ── de instellingen ─────────────────────────────────────────────────────────

def test_een_schuine_streep_aan_het_eind_gaat_eraf():
    """Issuer en federated credential moeten LETTERLIJK gelijk zijn; een streep
    te veel laat de uitwisseling stil mislukken."""
    assert uf.normaliseer(SimpleNamespace(issuer=ISSUER + "/"))["issuer"] == ISSUER


def test_wat_er_mist_wordt_vooraf_gemeld():
    fouten = " ".join(uf.controleer(_config(tenant_id="", client_id="", issuer="")))
    assert "tenant-id" in fouten and "client-id" in fouten and "issuer" in fouten


def test_een_issuer_die_geen_https_adres_is_wordt_geweigerd():
    melding = " ".join(uf.controleer(_config(issuer="http://nickduchatinier.nl/oidc")))
    assert "https" in melding


def test_zonder_subject_vullen_we_er_een_in():
    assert uf.normaliseer(SimpleNamespace(issuer=ISSUER))["subject"] == "labx"


# ── de twee publieke bestanden ──────────────────────────────────────────────

def test_de_discovery_heeft_alles_wat_entra_verlangt(sleutel):
    """Een ontbrekend veld laat Entra het document afwijzen — ook velden die
    voor ons geen betekenis hebben."""
    uit = uf.issuer_bestanden(_config(), [sleutel])
    doc = uit[".well-known/openid-configuration"]
    for veld in ("issuer", "jwks_uri", "authorization_endpoint", "token_endpoint",
                 "token_endpoint_auth_methods_supported", "response_types_supported",
                 "subject_types_supported"):
        assert veld in doc, f"{veld} ontbreekt in de discovery"
    assert doc["issuer"] == ISSUER
    assert doc["jwks_uri"] == f"{ISSUER}/jwks.json"


def test_de_jwks_draagt_een_ondertekensleutel(sleutel):
    jwks = uf.issuer_bestanden(_config(), [sleutel])["jwks.json"]
    k = jwks["keys"][0]
    assert k["kty"] == "RSA" and k["alg"] == "RS256"
    # Entra verwacht `use: "sig"` en laat de sleutel anders links liggen.
    assert k["use"] == "sig"
    assert k["kid"] == "test-kid-1"
    assert k["n"] and k["e"] and "=" not in k["n"]      # base64url, zonder padding


def test_de_jwks_kan_meerdere_sleutels_dragen_voor_een_rotatie(sleutel):
    """Zonder meerdere kids zou elke rotatie een moment zijn waarop niets werkt:
    je publiceert de nieuwe ernaast, schakelt om, en haalt de oude later weg."""
    tweede = uf.nieuwe_sleutel(kid="test-kid-2")
    jwks = uf.issuer_bestanden(_config(), [sleutel, tweede])["jwks.json"]
    assert [k["kid"] for k in jwks["keys"]] == ["test-kid-1", "test-kid-2"]


def test_de_publieke_sleutel_hoort_echt_bij_de_prive_sleutel(sleutel):
    """De JWKS moet de tegenhanger zijn van waarmee we tekenen; anders
    controleert Entra tegen de verkeerde sleutel en krijg je AADSTS700027."""
    import jwt
    from jwt.algorithms import RSAAlgorithm

    jwk = uf.jwk_van(sleutel["public_pem"], sleutel["kid"])
    assertie = uf.maak_assertie(_config(), sleutel)
    publiek = RSAAlgorithm.from_jwk(json.dumps(jwk))
    body = jwt.decode(assertie, publiek, algorithms=["RS256"],
                      audience=uf.TOKEN_EXCHANGE_AUDIENCE)
    assert body["iss"] == ISSUER and body["sub"] == "labx"


# ── de assertie ─────────────────────────────────────────────────────────────

def test_de_assertie_heeft_de_kid_in_de_kop(sleutel):
    import jwt

    kop = jwt.get_unverified_header(uf.maak_assertie(_config(), sleutel))
    assert kop["kid"] == "test-kid-1" and kop["alg"] == "RS256"


def test_de_assertie_is_kort_geldig_en_uniek(sleutel):
    a = uf.claims_van(uf.maak_assertie(_config(), sleutel))
    b = uf.claims_van(uf.maak_assertie(_config(), sleutel))
    assert a["aud"] == "api://AzureADTokenExchange"
    assert 0 < a["exp"] - a["iat"] <= 15 * 60
    assert a["jti"] != b["jti"]          # hergebruik is geen optie


# ── de uitwisseling ─────────────────────────────────────────────────────────

class _Antwoord:
    def __init__(self, status=200, body=None, tekst=None):
        self.status_code = status
        self._body = body
        self.text = tekst if tekst is not None else json.dumps(body or {})

    def json(self):
        return self._body


class _Client:
    laatste = {}

    def __init__(self, antwoord):
        self._a = antwoord

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None):
        _Client.laatste = {"url": url, "data": data}
        return self._a


def _met(monkeypatch, antwoord):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client(antwoord))


def _access_token(**claims):
    import base64 as b64
    kop = b64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
    body = b64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{kop}.{body}.hand"


def test_de_uitwisseling_stuurt_precies_wat_entra_verwacht(monkeypatch, sleutel):
    token = _access_token(oid="uami-object-id", appid="uami-client-id",
                          aud="https://api.fabric.microsoft.com", exp=int(time.time()) + 3600)
    _met(monkeypatch, _Antwoord(body={"access_token": token}))
    res = asyncio.run(uf.haal_token(_config(), sleutel))

    d = _Client.laatste["data"]
    assert _Client.laatste["url"] == \
        "https://login.microsoftonline.com/tenant-abc/oauth2/v2.0/token"
    assert d["grant_type"] == "client_credentials"
    assert d["client_id"] == "uami-client-id"
    assert d["scope"] == "https://api.fabric.microsoft.com/.default"
    assert d["client_assertion_type"] == \
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    assert d["client_assertion"].count(".") == 2
    assert res["identity"]["oid"] == "uami-object-id"
    assert res["expires_on"] > time.time()


def test_een_andere_scope_mag_per_aanvraag(monkeypatch, sleutel):
    _met(monkeypatch, _Antwoord(body={"access_token": _access_token(oid="x")}))
    asyncio.run(uf.haal_token(_config(), sleutel,
                              scope="https://storage.azure.com/.default"))
    assert _Client.laatste["data"]["scope"] == "https://storage.azure.com/.default"


@pytest.mark.parametrize("code,verwacht", [
    ("AADSTS70021", "federated credential"),
    ("AADSTS700211", "vertrouwde uitgever"),
    ("AADSTS700024", "klok"),
    ("AADSTS700027", "jwks.json"),
    ("AADSTS700016", "CLIENT id"),
])
def test_elke_aadsts_code_krijgt_er_uitleg_bij(monkeypatch, sleutel, code, verwacht):
    """Elke code heeft een andere oorzaak die je zelf moet oplossen; "token
    ophalen mislukt" helpt je bij geen enkele."""
    _met(monkeypatch, _Antwoord(status=400,
                                tekst=f'{{"error":"invalid_client","error_description":"{code}: iets"}}'))
    with pytest.raises(RuntimeError) as fout:
        asyncio.run(uf.haal_token(_config(), sleutel))
    assert code in str(fout.value)            # de melding van Entra blijft staan
    assert verwacht in str(fout.value)        # en er staat bij wat je eraan doet


def test_onvolledige_instellingen_stoppen_voor_de_aanvraag(sleutel):
    with pytest.raises(ValueError) as fout:
        asyncio.run(uf.haal_token(_config(client_id=""), sleutel))
    assert "client-id" in str(fout.value)


# ── hoe lang we hem gebruiken ───────────────────────────────────────────────

def test_er_gaat_marge_af():
    assert 45 <= uf.minuten_geldig(int(time.time()) + 3600) <= 50


def test_een_token_dat_bijna_om_is_geldt_nog_een_minuut():
    assert uf.minuten_geldig(int(time.time()) + 120) == 1


# ── het profiel, de sleutel en het lab ──────────────────────────────────────

@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from db.database import Base
    from models.azure_profile import AzureProfile
    from models.lab import Lab
    from models.lab_secret import LabSecret

    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[
        AzureProfile.__table__, Lab.__table__, LabSecret.__table__])
    s = sessionmaker(bind=motor)()
    s.add(Lab(id="lab-1", name="Swinkels", status="running",
              image="python:3-bookworm", created_at="nu", updated_at="nu"))
    s.commit()
    yield s
    s.close()


def _maak_profiel(db, naam="Fabric Swinkels"):
    from schemas.azure_profile import AzureProfileCreate
    from services.azure.azure_profile_service import AzureProfileService

    return AzureProfileService(db).create(AzureProfileCreate(
        name=naam, kind="uami_federated", tenant_id="tenant-abc",
        client_id="uami-client-id", issuer=ISSUER, subject="labx"))


def test_bij_het_aanmaken_maakt_labx_zelf_een_sleutel(db):
    """Die hoort niet in een formulier: hij mag de server nooit verlaten."""
    from services.azure.azure_profile_service import AzureProfileService

    rij = _maak_profiel(db)
    uit = AzureProfileService(db).issuer_bestanden(rij.id)
    assert uit["issuer"] == ISSUER
    assert uit["audience"] == "api://AzureADTokenExchange"
    jwks = uit["bestanden"]["jwks.json"]
    assert len(jwks["keys"]) == 1
    assert jwks["keys"][0]["kid"] == uit["actieve_kid"]
    # en de privésleutel staat er NIET in — dit is het bestand dat je publiceert
    alles = json.dumps(uit)
    assert "BEGIN PRIVATE KEY" not in alles
    assert "private_pem" not in alles


def test_een_profiel_met_een_onmogelijke_issuer_wordt_geweigerd(db):
    from fastapi import HTTPException
    from schemas.azure_profile import AzureProfileCreate
    from services.azure.azure_profile_service import AzureProfileService

    with pytest.raises(HTTPException) as fout:
        AzureProfileService(db).create(AzureProfileCreate(
            name="Fout", kind="uami_federated", tenant_id="t",
            client_id="c", issuer="ftp://nergens", subject="labx"))
    assert "https" in str(fout.value.detail)


def test_roteren_zet_de_nieuwe_sleutel_ernaast_en_niet_ervoor(db):
    """Entra haalt de sleutels op wanneer het hém uitkomt; de oude moet dus
    blijven staan tot de nieuwe JWKS echt gepubliceerd is."""
    from services.azure.azure_profile_service import AzureProfileService

    svc = AzureProfileService(db)
    rij = _maak_profiel(db)
    eerste = svc.issuer_bestanden(rij.id)["actieve_kid"]
    na = svc.roteer_sleutel(rij.id)
    kids = [k["kid"] for k in na["bestanden"]["jwks.json"]["keys"]]
    assert eerste in kids and len(kids) == 2
    assert na["actieve_kid"] != eerste and na["actieve_kid"] in kids

    # en pas als je het zegt, gaat de oude eruit
    opgeruimd = svc.vergeet_oude_sleutels(rij.id)
    assert [k["kid"] for k in opgeruimd["bestanden"]["jwks.json"]["keys"]] \
        == [na["actieve_kid"]]


def test_er_wordt_getekend_met_de_actieve_sleutel(db, monkeypatch):
    import jwt

    from services.azure.azure_profile_service import AzureProfileService

    svc = AzureProfileService(db)
    rij = _maak_profiel(db)
    na = svc.roteer_sleutel(rij.id)
    _met(monkeypatch, _Antwoord(body={"access_token": _access_token(oid="x")}))
    asyncio.run(svc.token_for(rij.id, uf.STANDAARD_SCOPE))
    kop = jwt.get_unverified_header(_Client.laatste["data"]["client_assertion"])
    assert kop["kid"] == na["actieve_kid"]


def test_het_token_belandt_als_lab_geheim(db, monkeypatch):
    from services.azure.azure_profile_service import AzureProfileService
    from services.lab.secrets import SecretService

    _met(monkeypatch, _Antwoord(body={"access_token": _access_token(
        oid="de-uami", exp=int(time.time()) + 3600)}))
    rij = _maak_profiel(db)
    res = asyncio.run(AzureProfileService(db).sync(rij.id, target="lab", lab_id="lab-1"))

    assert res["detail"]["geheim"] == "fabric-swinkels"
    assert res["detail"]["gebruik"] == "{{secret:fabric-swinkels}}"
    assert res["detail"]["identity"]["oid"] == "de-uami"
    geheim = SecretService(db).haal("lab-1", "fabric-swinkels")
    assert geheim is not None and geheim.kind == "waarde"
    assert geheim.azure_profile_id == rij.id
    assert 45 <= geheim.ttl_minutes <= 50


def test_een_bijna_verlopen_token_wordt_vanzelf_vervangen(db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from services.azure.azure_profile_service import AzureProfileService
    from services.lab.secrets import SecretService
    from utils.crypto import decrypt

    _met(monkeypatch, _Antwoord(body={"access_token": _access_token(
        oid="a", exp=int(time.time()) + 3600)}))
    rij = _maak_profiel(db)
    asyncio.run(AzureProfileService(db).sync(rij.id, target="lab", lab_id="lab-1"))
    geheim = SecretService(db).haal("lab-1", "fabric-swinkels")
    geheim.refreshed_at = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    db.commit()

    _met(monkeypatch, _Antwoord(body={"access_token": _access_token(
        oid="a", nieuw=True, exp=int(time.time()) + 3600)}))
    assert asyncio.run(uf.ververs_labgeheimen(db)) == {"ververst": 1, "mislukt": 0}
    opnieuw = decrypt(SecretService(db).haal("lab-1", "fabric-swinkels").value_encrypted)
    assert uf.claims_van(opnieuw).get("nieuw") is True


def test_een_mislukte_verversing_blijft_zichtbaar_op_het_geheim(db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from services.azure.azure_profile_service import AzureProfileService
    from services.lab.secrets import SecretService

    _met(monkeypatch, _Antwoord(body={"access_token": _access_token(
        oid="a", exp=int(time.time()) + 3600)}))
    rij = _maak_profiel(db)
    asyncio.run(AzureProfileService(db).sync(rij.id, target="lab", lab_id="lab-1"))
    geheim = SecretService(db).haal("lab-1", "fabric-swinkels")
    geheim.refreshed_at = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    db.commit()

    _met(monkeypatch, _Antwoord(status=400,
                                tekst='{"error_description":"AADSTS700027: signature"}'))
    assert asyncio.run(uf.ververs_labgeheimen(db))["mislukt"] == 1
    fout = SecretService(db).haal("lab-1", "fabric-swinkels").last_error or ""
    assert "AADSTS700027" in fout and "jwks.json" in fout
