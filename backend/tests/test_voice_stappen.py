"""Alle activiteitsoorten uit spreektaal.

Aanleiding: iemand zei "als de logging is uitgelezen hoeft er niets te
gebeuren, anders moet hij het hello-script aanmaken" en dat werd één tekststap
in plaats van een vertakking. De tekenaar kent zes soorten; een vlakke
opsomming van zinnen kan die niet uitdrukken.

Wat hier wordt vastgelegd: dat de soort uit je woorden komt, dat de server
alleen doorvraagt naar wat er voor díé soort mist, en dat de boom omgezet
wordt naar een graaf die de tekenaar accepteert.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.voice import stappen  # noqa: E402

KLAAR = {"klaar", "klar"}


@pytest.mark.parametrize("zin,soort", [
    ("Lees het logbestand van het dev-systeem", "agent"),
    ("Als de logging gevuld is", "als"),
    ("Controleer of het gelukt is", "als"),
    ("Wacht vijf minuten", "wacht"),
    ("Voer uit: ls -la", "shell"),
    ("Voor elke klant een samenvatting maken", "voorelk"),
    ("Doe die twee dingen tegelijk", "parallel"),
])
def test_de_soort_komt_uit_je_woorden(zin, soort):
    assert stappen.raad_soort(zin) == soort


@pytest.mark.parametrize("zin,seconden", [
    ("wacht 5 minuten", 300),
    ("wacht 30 seconden", 30),
    ("wacht twee uur", 7200),
    ("wacht vijftien minuten", 900),
])
def test_wachttijden_uit_spreektaal(zin, seconden):
    assert stappen.raad_seconden(zin) == min(3600, seconden)


@pytest.mark.parametrize("zin,operator,rechts", [
    ("gelijk is aan ok", "==", "ok"),
    ("niet gelijk is aan fout", "!=", "fout"),
    ("bevat fout", "bevat", "fout"),
    ("groter dan 10", ">", 10),
    ("leeg is", "is_leeg", None),
    ("gevuld is", "is_niet_leeg", None),
])
def test_voorwaarden_uit_spreektaal(zin, operator, rechts):
    uit = stappen.lees_voorwaarde(zin)
    assert uit["operator"] == operator
    assert uit["rechts"] == rechts


def test_zonder_herkenbare_vergelijking_vallen_we_terug_op_gevuld():
    """De minst verrassende betekenis van "kijk of er iets uitkwam" -- en hij
    staat in de bevestigingszin, dus je kunt hem tegenhouden."""
    assert stappen.lees_voorwaarde("iets raars")["operator"] == "is_niet_leeg"


def test_een_keuze_krijgt_twee_takken():
    """Precies het geval dat eerder als tekststap verdween."""
    velden = {}
    stappen.verwerk(velden, "Lees het logbestand", KLAAR)
    stappen.verwerk(velden, "Als de logging is uitgelezen", KLAAR)
    assert "waar moet ik naar kijken" in stappen.vraag(velden).lower()

    stappen.verwerk(velden, "of het lezen gelukt is", KLAAR)
    assert "wat moet daarmee" in stappen.vraag(velden).lower()

    stappen.verwerk(velden, "gelijk aan ok", KLAAR)
    assert "als dat klopt" in stappen.vraag(velden).lower()

    stappen.verwerk(velden, "niets", KLAAR)          # lege ja-tak
    assert "als dat niet zo is" in stappen.vraag(velden).lower()

    stappen.verwerk(velden, "Maak het hello-script aan", KLAAR)
    stappen.verwerk(velden, "klaar", KLAAR)          # nee-tak afsluiten
    stappen.verwerk(velden, "klaar", KLAAR)          # de workflow afsluiten

    assert stappen.compleet(velden)
    keuze = velden["stappen"][1]
    assert keuze["type"] == "als"
    # De verwijzing is er eentje die ECHT bestaat, niet wat er letterlijk
    # werd gezegd: "of het lezen gelukt is" wijst naar de status van stap 1.
    assert keuze["conditie"]["links"] == "stap.stap1.status"
    assert keuze["conditie"]["operator"] == "=="
    assert keuze["conditie"]["rechts"] == "ok"
    assert not keuze.get("ja")
    assert [s["prompt"] for s in keuze["nee"]] == ["Maak het hello-script aan"]


def test_de_graaf_van_een_keuze_heeft_ja_en_nee_verbindingen():
    velden = {}
    for zin in ["Lees het logbestand", "Als het gevuld is", "of het lezen gelukt is",
                "gevuld is", "niets", "Maak het hello-script", "klaar", "klaar"]:
        stappen.verwerk(velden, zin, KLAAR)

    nodes, edges = stappen.naar_graaf(velden["stappen"])
    soorten = {n["type"] for n in nodes}
    assert soorten == {"agent", "als"}
    assert {e["soort"] for e in edges} >= {"nee"}
    # De lege ja-tak loopt door naar wat er ná de keuze komt; hier is dat
    # niets, dus die verbinding bestaat nog niet.
    keuze = next(n for n in nodes if n["type"] == "als")
    assert any(e["van"] == keuze["id"] and e["soort"] == "nee" for e in edges)


def test_een_lus_krijgt_kinderen_via_een_groep():
    velden = {}
    stappen.verwerk(velden, "Voor elke klant iets doen", KLAAR)
    assert "waar komt die lijst vandaan" in stappen.vraag(velden).lower()
    stappen.verwerk(velden, "stap.klanten.json.items", KLAAR)
    assert "lus" in stappen.vraag(velden).lower()
    stappen.verwerk(velden, "Maak een samenvatting", KLAAR)
    stappen.verwerk(velden, "klaar", KLAAR)
    stappen.verwerk(velden, "klaar", KLAAR)

    nodes, _edges = stappen.naar_graaf(velden["stappen"])
    lus = next(n for n in nodes if n["type"] == "voorelk")
    assert lus["bron"] == "stap.klanten.json.items"
    kind = next(n for n in nodes if n.get("groep") == lus["id"])
    assert kind["prompt"] == "Maak een samenvatting"


def test_een_wachtstap_met_tijd_erin_vraagt_niet_door():
    velden = {}
    stappen.verwerk(velden, "Wacht vijf minuten", KLAAR)
    assert velden["stappen"][0]["seconden"] == 300
    assert velden.get("open") is None


def test_een_commando_vraagt_wel_door():
    velden = {}
    stappen.verwerk(velden, "Voer een commando uit", KLAAR)
    assert "welk commando" in stappen.vraag(velden).lower()
    stappen.verwerk(velden, "ls -la /workspace", KLAAR)
    assert velden["stappen"][0]["commando"] == "ls -la /workspace"


def test_de_graaf_gaat_door_de_normalisatie_van_de_tekenaar():
    """Anders krijg je een workflow die niet opent."""
    from services.workflows import graph

    velden = {}
    for zin in ["Lees de logs", "Wacht 30 seconden", "klaar"]:
        stappen.verwerk(velden, zin, KLAAR)

    rauw_n, rauw_e = stappen.naar_graaf(velden["stappen"])
    nodes, edges = graph.normaliseer(rauw_n, rauw_e)
    assert len(nodes) == 2
    assert all("positie" in n and "sleutel" in n for n in nodes)
    assert len(edges) == 1 and edges[0]["soort"] == "succes"


# ── Waar een keuze naar kijkt ────────────────────────────────────────────────
# Dit was een echte fout: een conditie verwees naar stap.X.json.Y terwijl die
# stap geen exportschema had. De voorwaarde evalueerde dan tegen niets en de
# keuze ging altijd stil de nee-tak in -- een workflow die niet faalt, maar
# ook nooit doet wat je bedoelde.

def test_een_nieuw_veld_wordt_aan_de_vorige_stap_toegevoegd():
    velden = {}
    stappen.verwerk(velden, "Lees het logbestand", KLAAR)
    stappen.verwerk(velden, "Als het goed ging", KLAAR)
    stappen.verwerk(velden, "aantal regels", KLAAR)       # bestaat nog niet
    stappen.verwerk(velden, "groter dan 0", KLAAR)

    lezen = velden["stappen"][0]
    assert lezen["_velden"] == ["aantal_regels"], "de vorige stap moet dit teruggeven"
    keuze = velden["stappen"][1]
    assert keuze["conditie"]["links"] == f"stap.{lezen['sleutel']}.json.aantal_regels"


def test_de_stap_krijgt_ook_echt_een_exportschema():
    """Zonder dit staat het veld wel in de voorwaarde maar levert de stap het
    nooit op -- precies de stille fout die dit moest verhelpen."""
    velden = {}
    for zin in ["Lees het logbestand", "Als het goed ging", "aantal regels",
                "groter dan 0", "niets", "Meld het", "klaar", "klaar"]:
        stappen.verwerk(velden, zin, KLAAR)

    nodes, _ = stappen.naar_graaf(velden["stappen"])
    lezen = nodes[0]
    assert lezen["json_schema"], "de stap moet gestructureerd antwoorden"
    schema = json.loads(lezen["json_schema"])
    assert "aantal_regels" in schema["properties"]
    assert "aantal_regels" in schema["required"]
    # En de opdracht zegt het er ook bij, anders weet de agent het niet.
    assert "aantal_regels" in lezen["prompt"]


def test_een_bestaande_verwijzing_wordt_hergebruikt():
    """Noem je iets dat er al is, dan komt er geen tweede veld bij."""
    velden = {}
    stappen.verwerk(velden, "Lees het logbestand", KLAAR)
    stappen.verwerk(velden, "Als het goed ging", KLAAR)
    stappen.verwerk(velden, "aantal regels", KLAAR)
    stappen.verwerk(velden, "groter dan 0", KLAAR)
    stappen.verwerk(velden, "niets", KLAAR)
    stappen.verwerk(velden, "klaar", KLAAR)
    stappen.verwerk(velden, "Als er nog iets is", KLAAR)
    stappen.verwerk(velden, "aantal regels", KLAAR)       # bestaat nu wél

    assert velden["stappen"][0]["_velden"] == ["aantal_regels"], "geen dubbel veld"


def test_zonder_eerdere_stap_wordt_het_een_invoerparameter():
    """Begint de workflow met een keuze, dan kan er niets geëxporteerd zijn --
    dan is het logische alternatief invoer van de workflow zelf."""
    velden = {}
    stappen.verwerk(velden, "Als de klant bekend is", KLAAR)
    stappen.verwerk(velden, "klantnaam", KLAAR)
    stappen.verwerk(velden, "niet leeg", KLAAR)

    assert velden["parameters"] == [{"naam": "klantnaam", "soort": "tekst"}]
    assert velden["stappen"][0]["conditie"]["links"] == "invoer.klantnaam"


def test_de_keuzelijst_noemt_wat_er_te_kiezen_valt():
    velden = {}
    stappen.verwerk(velden, "Lees het logbestand", KLAAR)
    stappen.verwerk(velden, "Als het goed ging", KLAAR)

    vraag = stappen.vraag(velden)
    assert "kiezen uit" in vraag.lower()
    assert "gelukt" in vraag.lower()


@pytest.mark.parametrize("zin,rechts", [
    ("groter dan nul", 0),
    ("groter dan 10", 10),
    ("kleiner dan vijf", 5),
    ("gelijk aan 3.5", 3.5),
    ("gelijk aan ok", "ok"),
])
def test_getallen_in_een_voorwaarde_worden_getallen(zin, rechts):
    """">" op twee stukjes tekst doet iets heel anders dan op twee getallen."""
    assert stappen.lees_voorwaarde(zin)["rechts"] == rechts
