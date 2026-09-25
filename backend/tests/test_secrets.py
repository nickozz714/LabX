"""Tokens horen niet in de tekst van een commando.

Een agent die met Fabric of Azure werkt, haalt een access-token op en gebruikt
dat daarna in tien commando's. Die commando's zijn TEKST: ze staan in het
audit-spoor, ze komen als tool-invoer terug bij het model, en ze lekken zodra
een script iets echoot. Zo reisde er een geldig OAuth-token door de hele keten.

Met `{{secret:naam}}` vervangt LabX de verwijzing pas in de container, en zelfs
daar niet door de waarde maar door een omgevingsvariabele uit een bestand dat
alleen root kan lezen. Deze tests leggen vast dat de waarde nergens in de tekst
terechtkomt.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.secrets import (  # noqa: E402
    SECRETS_BESTAND, SecretService, envnaam, geldige_naam, verwijzingen_in)

GEHEIM = "gEhEiM-t0k3n-abcdefghijklmnop-0123456789"


# ── namen ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("naam,verwacht", [
    ("fabric", "LABX_SECRET_FABRIC"),
    ("fabric-token", "LABX_SECRET_FABRIC_TOKEN"),
    ("az_prd", "LABX_SECRET_AZ_PRD"),
])
def test_naam_wordt_een_bruikbare_variabele(naam, verwacht):
    assert envnaam(naam) == verwacht


@pytest.mark.parametrize("naam", ["fabric", "a", "AZ-1_x", "x" * 64])
def test_geldige_namen(naam):
    assert geldige_naam(naam)


@pytest.mark.parametrize("naam", ["", "met spatie", "punt.jes", "x" * 65, "sla/sh", "$(whoami)"])
def test_ongeldige_namen(naam):
    """De naam wordt een omgevingsvariabele én komt in een bestand terecht dat
    de shell inleest; alles wat daar kan ontsnappen hoort er niet in."""
    assert not geldige_naam(naam)


# ── verwijzingen vinden ─────────────────────────────────────────────────────

def test_verwijzingen_worden_gevonden():
    cmd = 'curl -H "Bearer {{secret:fabric}}" && echo {{ secret : az_prd }}'
    assert verwijzingen_in(cmd) == ["fabric", "az_prd"]


def test_dubbelen_tellen_een_keer():
    assert verwijzingen_in("{{secret:a}} {{secret:a}} {{secret:b}}") == ["a", "b"]


@pytest.mark.parametrize("cmd", ["echo hallo", "", None, "{{secret}}", "{secret:a}"])
def test_geen_verwijzing_geen_treffer(cmd):
    assert verwijzingen_in(cmd) == []


# ── de vervanging ───────────────────────────────────────────────────────────

class NepRuntime:
    def __init__(self):
        self.aanroepen = []
    async def exec(self, cid, cmd, *, stdin=None, timeout=60, **kw):
        self.aanroepen.append({"cmd": cmd, "stdin": stdin})
        return {"exit_code": 0, "output": "", "truncated": False}


class _LegeQuery:
    """Een lege kluis. Sinds lab-geheimen kunnen terugvallen op de kluis die
    niet aan één lab hangt (services/secrets/vault.py), kijkt deze code daar
    ook — deze tests gaan over de lab-kant, dus daar hoort niets te staan."""

    def order_by(self, *a, **kw):
        return self

    def filter(self, *a, **kw):
        return self

    def all(self):
        return []

    def first(self):
        return None


def _svc(geheimen):
    svc = SecretService.__new__(SecretService)
    svc.haal = lambda lab_id, naam: geheimen.get(naam)
    svc.lijst = lambda lab_id: list(geheimen.values())
    svc.db = SimpleNamespace(commit=lambda: None,
                             query=lambda *a, **kw: _LegeQuery())
    return svc


def _rij(naam, waarde):
    from utils.crypto import encrypt
    return SimpleNamespace(name=naam, kind="waarde", value_encrypted=encrypt(waarde),
                           produce_command=None, ttl_minutes=50, refreshed_at="x",
                           last_used_at=None)


@pytest.mark.parametrize("cmd", [
    'curl -H "Authorization: Bearer {{secret:fabric}}" https://x',
    "TOKEN={{secret:fabric}}",
    'echo "{{secret:fabric}}" > /tmp/t',
])
def test_de_waarde_staat_nooit_in_het_commando(cmd):
    import asyncio

    svc = _svc({"fabric": _rij("fabric", GEHEIM)})
    rt = NepRuntime()
    klaar, gebruikt, onbekend = asyncio.run(
        svc.bereid_voor("lab-1", cmd, runtime=rt, container_id="c"))
    assert GEHEIM not in klaar, "de waarde hoort niet in de commandotekst"
    assert "LABX_SECRET_FABRIC" in klaar
    assert gebruikt == ["fabric"] and onbekend == []
    # En hij staat ook niet op de commandoregel van het schrijf-commando: die
    # zou zichtbaar zijn in `ps` op de HOST. Alleen via stdin.
    for aanroep in rt.aanroepen:
        assert GEHEIM not in " ".join(aanroep["cmd"])
    assert any(GEHEIM.encode() in (a["stdin"] or b"") for a in rt.aanroepen)


def test_het_bestand_krijgt_strakke_rechten():
    import asyncio

    svc = _svc({"fabric": _rij("fabric", GEHEIM)})
    rt = NepRuntime()
    asyncio.run(svc.bereid_voor("lab-1", "echo {{secret:fabric}}",
                                runtime=rt, container_id="c"))
    schrijf = " ".join(rt.aanroepen[0]["cmd"])
    assert "umask 077" in schrijf
    assert SECRETS_BESTAND in schrijf


def test_onbekend_geheim_wordt_gemeld_niet_stil_genegeerd():
    import asyncio

    svc = _svc({})
    klaar, gebruikt, onbekend = asyncio.run(
        svc.bereid_voor("lab-1", "echo {{secret:weg}}", runtime=NepRuntime(), container_id="c"))
    assert onbekend == ["weg"]
    # De verwijzing blijft staan: stil vervangen door niets zou een commando
    # opleveren dat er goed uitziet en het verkeerde doet.
    assert "{{secret:weg}}" in klaar


def test_commando_zonder_verwijzing_blijft_onaangeraakt():
    import asyncio

    svc = _svc({"fabric": _rij("fabric", GEHEIM)})
    rt = NepRuntime()
    klaar, gebruikt, _ = asyncio.run(
        svc.bereid_voor("lab-1", "ls -la /workspace", runtime=rt, container_id="c"))
    assert klaar == "ls -la /workspace"
    assert gebruikt == [] and rt.aanroepen == []


# ── het vangnet op de uitvoer ───────────────────────────────────────────────

def test_geheim_in_de_uitvoer_wordt_gemaskeerd():
    """Een script kan een token echoën of in een foutmelding laten vallen. De
    vervanging voorkomt dat hij in het commando staat; dit vangt de andere kant."""
    svc = _svc({"fabric": _rij("fabric", GEHEIM)})
    uit = svc.maskeer_in("lab-1", f"Authorization: Bearer {GEHEIM}\nklaar")
    assert GEHEIM not in uit
    assert "[geheim fabric verborgen]" in uit
    assert "klaar" in uit


def test_korte_waarden_worden_niet_gemaskeerd():
    """Anders vervang je willekeurige tekst: een geheim van vier tekens komt
    overal in normale uitvoer voor."""
    svc = _svc({"kort": _rij("kort", "abc")})
    assert svc.maskeer_in("lab-1", "abc def abc") == "abc def abc"


def test_maskeren_op_lege_uitvoer_valt_niet_om():
    svc = _svc({"fabric": _rij("fabric", GEHEIM)})
    assert svc.maskeer_in("lab-1", "") == ""
    assert svc.maskeer_in("lab-1", None) == ""
