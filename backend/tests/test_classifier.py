"""Classifier en guard, en waarom dat twee dingen zijn.

De classifier VINDT en LABELT ("hier staan drie geldige BSN's"). Wat daarmee
gebeurt is beleid, en dat staat per regel in de database waar je het zelf kunt
aanpassen. Door die twee te scheiden kun je de gevoeligheid bijstellen zonder
code te wijzigen, en kun je in de audit teruglezen wat er gevonden is los van
wat eraan gedaan is.

De aanleiding: de oude guard verving bij één treffer de HELE uitvoer door een
melding. Daarmee raakte je ook de exitcode, het pad en de foutmelding kwijt die
je nodig had — en dat is waarom hij het werk in de weg zat in plaats van het te
beschermen.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# `test_regel` komt binnen onder een andere naam: pytest verzamelt elke
# functie die met `test_` begint als test, ook een geïmporteerde.
from services.lab.classifier import (  # noqa: E402
    INGEBOUWD, STANDAARDREGELS, maskeer)
from services.lab.classifier import test_regel as probeer_regel  # noqa: E402

# Geldige voorbeelden — de checksums moeten kloppen, anders test je niets.
BSN = "111222333"          # elfproef: 9*1+8*1+7*1+6*2+5*2+4*2+3*3+2*3-3 = 66
IBAN = "NL91ABNA0417164300"
KAART = "4539578763621486"


def test_de_voorbeelden_in_deze_test_kloppen_echt():
    """Als deze faalt is de rest van het bestand betekenisloos."""
    assert INGEBOUWD["bsn"](BSN), "BSN-voorbeeld doorstaat de elfproef niet"
    assert INGEBOUWD["iban"](IBAN), "IBAN-voorbeeld doorstaat modulo-97 niet"
    assert INGEBOUWD["creditcard"](KAART), "kaartvoorbeeld doorstaat Luhn niet"


# ── checksums: het verschil tussen een bruikbare en een onbruikbare guard ────

def test_negen_cijfers_zonder_elfproef_is_geen_bsn():
    """Zonder deze toets is elk ordernummer van negen cijfers een BSN, en dan
    blokkeert de guard het halve werk."""
    assert INGEBOUWD["bsn"]("123456789") == []
    assert INGEBOUWD["bsn"]("ordernummer 100000001") == []


def test_willekeurige_letters_en_cijfers_zijn_geen_iban():
    assert INGEBOUWD["iban"]("NL00BANK0000000000") == []


def test_lang_getal_zonder_luhn_is_geen_creditcard():
    assert INGEBOUWD["creditcard"]("1234567890123456") == []


def test_geldige_waarden_worden_wel_gevonden():
    assert len(INGEBOUWD["bsn"](f"klant {BSN} verwerkt")) == 1
    assert len(INGEBOUWD["iban"](f"rekening {IBAN}")) == 1
    assert len(INGEBOUWD["creditcard"](f"kaart {KAART}")) == 1
    assert len(INGEBOUWD["email"]("mail jan@voorbeeld.nl door")) == 1


# ── maskeren ────────────────────────────────────────────────────────────────

def test_maskeren_laat_de_rest_staan():
    """De kern van de omslag: je houdt de exitcode, het pad en de foutmelding."""
    tekst = f"http=200\n/workspace/out.json\nklant {BSN}\nexit 0"
    uit = maskeer(tekst, [(m[0], m[1], "persoonsgegeven") for m in INGEBOUWD["bsn"](tekst)])
    assert BSN not in uit
    assert "http=200" in uit and "/workspace/out.json" in uit and "exit 0" in uit
    assert "[persoonsgegeven verborgen]" in uit


def test_overlappende_treffers_verminken_de_tekst_niet():
    """Twee regels die hetzelfde stuk raken (een IBAN die ook als lang getal
    telt) zouden anders over elkaar heen schrijven."""
    tekst = f"rekening {IBAN} einde"
    start = tekst.index(IBAN)
    uit = maskeer(tekst, [(start, start + len(IBAN), "iban"),
                          (start + 2, start + 8, "getal")])
    assert uit.startswith("rekening [") and uit.endswith("einde")
    assert IBAN not in uit
    assert uit.count("verborgen") == 1


def test_maskeren_zonder_treffers_verandert_niets():
    assert maskeer("gewone tekst", []) == "gewone tekst"


def test_posities_schuiven_niet_door_eerdere_vervangingen():
    """Van achteren naar voren vervangen; anders klopt de tweede positie niet
    meer zodra de eerste vervanging een andere lengte heeft."""
    tekst = f"a {BSN} b {BSN} c"
    treffers = [(m[0], m[1], "bsn") for m in INGEBOUWD["bsn"](tekst)]
    uit = maskeer(tekst, treffers)
    assert BSN not in uit
    assert uit.startswith("a [") and uit.endswith(" c")
    assert uit.count("[bsn verborgen]") == 2


# ── het testveld in de UI ───────────────────────────────────────────────────

def test_ongeldig_patroon_geeft_een_leesbare_fout():
    """Hufterproof: je krijgt te horen wát er mis is, niet een stacktrace."""
    r = probeer_regel("regex", "([onafgesloten", None, "test")
    assert not r["ok"] and "Ongeldig patroon" in r["fout"]


def test_patroon_dat_niets_vindt_waarschuwt():
    r = probeer_regel("regex", r"\bzeldzaam\b", None, "hier staat iets anders")
    assert r["ok"] and r["aantal"] == 0
    assert "Geen enkele treffer" in r["waarschuwing"]


def test_patroon_dat_alles_pakt_waarschuwt():
    """De andere manier waarop een zelfgemaakte regel fout is: hij maskeert
    straks de hele uitvoer."""
    r = probeer_regel("regex", r".+", None, "een hele regel tekst")
    assert r["ok"] and "bijna de hele tekst" in r["waarschuwing"]


def test_testveld_toont_hoe_het_eruit_komt_te_zien():
    r = probeer_regel("ingebouwd", None, "bsn", f"klant {BSN} klaar")
    assert r["ok"] and r["aantal"] == 1
    assert BSN not in r["voorbeeld_gemaskeerd"]
    assert "klaar" in r["voorbeeld_gemaskeerd"]


def test_onbekende_detector_wordt_gemeld():
    r = probeer_regel("ingebouwd", None, "bestaatniet", "x")
    assert not r["ok"] and "Onbekende ingebouwde detector" in r["fout"]


# ── de meegeleverde regels ──────────────────────────────────────────────────

def test_opdrachtregels_maskeren_niet():
    """Een commando kun je niet half uitvoeren."""
    for spec in STANDAARDREGELS:
        if spec["target"] == "opdracht":
            assert spec["action"] != "maskeren", spec["name"]


def test_persoonsgegevens_worden_gemaskeerd_niet_geblokkeerd():
    """Standaard maskeren: de rest van de uitvoer (exitcode, pad, foutmelding)
    heb je nodig. Wie tóch wil blokkeren zet dat zelf om — daarom is het
    instelbaar.

    Eén uitzondering, en die is principieel: een uitvoer die ALS GEHEEL een
    dataset is, valt niets zinnigs aan te maskeren. Daar houd je met maskeren
    veertig regels merktekens over, en dat is geen bruikbaar antwoord."""
    for spec in STANDAARDREGELS:
        if spec["target"] != "uitvoer":
            continue
        if spec["key"] == "dataset":
            assert spec["action"] == "blokkeren", spec["name"]
        else:
            assert spec["action"] == "maskeren", spec["name"]


def test_alle_meegeleverde_patronen_compileren():
    import re

    for spec in STANDAARDREGELS:
        if spec["kind"] == "regex":
            re.compile(spec["pattern"])


def test_ingebouwde_regels_wijzen_naar_een_bestaande_detector():
    for spec in STANDAARDREGELS:
        if spec["kind"] == "ingebouwd":
            assert spec["key"] in INGEBOUWD, spec["key"]


# ── de detector zonder patroon ──────────────────────────────────────────────
#
# De andere detectors zoeken naar een IDENTIFICATIE (een BSN, een IBAN). Deze
# zoekt naar de VORM: veertig regels met komma's en datums is een dump van
# klantgegevens, ook als er geen enkel nummer in staat dat ergens op lijkt.
# Zonder deze detector was die dump door de nieuwe guard heen geglipt.

def _rijen(n: int) -> str:
    return "\n".join(
        f"{i},Jansen,Amsterdam,{1000 + i},2026-01-{i % 28 + 1:02d}" for i in range(n))


def test_een_dump_van_klantrijen_wordt_herkend():
    assert INGEBOUWD["dataset"](_rijen(40)), "40 recordregels is een dataset"


def test_een_paar_regels_uitvoer_is_geen_dataset():
    """Anders blokkeert de guard elke foutmelding met wat velden erin."""
    assert INGEBOUWD["dataset"](_rijen(3)) == []
    assert INGEBOUWD["dataset"]("http=200\n/workspace/out.json\nexit 0") == []


def test_de_dataset_treffer_beslaat_de_hele_tekst():
    """Bij veertig regels valt er niets zinnigs te maskeren — je houdt dan
    alleen merktekens over. Daarom blokkeert de meegeleverde regel."""
    tekst = _rijen(40)
    treffers = INGEBOUWD["dataset"](tekst)
    assert treffers[0][0] == 0 and treffers[0][1] == len(tekst)


def test_de_meegeleverde_datasetregel_blokkeert():
    spec = next(s for s in STANDAARDREGELS if s["key"] == "dataset")
    assert spec["action"] == "blokkeren"
