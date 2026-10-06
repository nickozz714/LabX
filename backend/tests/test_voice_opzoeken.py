"""Gesproken verwijzingen terugvertalen naar iets in LabX.

Een transcriptie maakt van "KRI-114" met enige regelmaat "krie
honderdveertien" of "K R I 114". Daar hangt de hele functie op: noemt de
gebruiker een ticket, dan moet het juiste ticket gevonden worden — of er moet
doorgevraagd worden, maar er mag nooit stil het verkeerde uit komen.

Ruim zoeken is hier veilig omdat elke treffer daarna door de bevestigingszin
gaat, mét de gevonden sleutel erin.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.voice.opzoeken import (  # noqa: E402
    kies_op_naam, kies_op_titel, normaliseer_sleutel,
)

PREFIXEN = ["KRI", "SWI", "KDVS", "BEE"]


def test_een_nette_sleutel_blijft_wat_hij_is():
    assert normaliseer_sleutel("KRI-114", PREFIXEN) == "KRI-114"
    assert normaliseer_sleutel("kri-114", PREFIXEN) == "KRI-114"


def test_zoals_een_transcriptie_het_oplevert():
    assert normaliseer_sleutel("kri 114", PREFIXEN) == "KRI-114"
    assert normaliseer_sleutel("K R I 114", PREFIXEN) == "KRI-114"
    assert normaliseer_sleutel("KRI114", PREFIXEN) == "KRI-114"


def test_uitgesproken_nummers():
    assert normaliseer_sleutel("kri honderdveertien", PREFIXEN) == "KRI-114"
    assert normaliseer_sleutel("kri honderd veertien", PREFIXEN) == "KRI-114"
    assert normaliseer_sleutel("swi veertien", PREFIXEN) == "SWI-14"


def test_de_langste_prefix_wint():
    """KDVS mag niet als K-D-V-S-iets op KRI lijken, en andersom."""
    assert normaliseer_sleutel("kdvs 13", PREFIXEN) == "KDVS-13"
    assert normaliseer_sleutel("kdvs dertien", PREFIXEN) == "KDVS-13"


def test_geen_sleutel_geeft_niets_in_plaats_van_een_gok():
    """Bij twijfel None: dan zoekt de aanroeper op titel, wat eerlijker is
    dan een verzonnen ticketnummer."""
    assert normaliseer_sleutel("het Holland Malt ticket", PREFIXEN) is None
    assert normaliseer_sleutel("", PREFIXEN) is None
    assert normaliseer_sleutel(None, PREFIXEN) is None
    assert normaliseer_sleutel("kri", PREFIXEN) is None       # prefix zonder nummer


def test_een_onbekende_prefix_telt_niet_mee():
    assert normaliseer_sleutel("abc 12", PREFIXEN) is None


def test_zoeken_op_titel():
    kandidaten = [
        ("SWI-141", "Holland Malt — silver reload"),
        ("SWI-96", "Transport events laden niet"),
        ("KRI-114", "Doelgroepenvervoer | D3 RouteUitvoering"),
    ]
    treffers = kies_op_titel("het Holland Malt ticket", kandidaten)
    assert treffers and treffers[0][0] == "SWI-141"


def test_twee_treffers_komen_allebei_terug():
    """Wie één antwoord forceert waar er twee zijn, kiest de verkeerde helft
    van de tijd. De aanroeper hoort dan door te vragen."""
    kandidaten = [
        ("SWI-141", "Holland Malt silver reload"),
        ("SWI-150", "Holland Malt gold model"),
        ("KRI-1", "Iets heel anders"),
    ]
    treffers = kies_op_titel("Holland Malt", kandidaten)
    assert len(treffers) == 2
    assert {t[0] for t in treffers} == {"SWI-141", "SWI-150"}


def test_niets_gevonden_is_een_lege_lijst():
    kandidaten = [("SWI-1", "Transport events")]
    assert kies_op_titel("doelgroepenvervoer", kandidaten) == []
    assert kies_op_titel("", kandidaten) == []


def test_korte_woorden_tellen_niet_mee():
    """'de', 'het' en 'een' zouden anders alles laten matchen."""
    kandidaten = [("A-1", "het de een"), ("A-2", "Holland Malt")]
    assert kies_op_titel("de het een", kandidaten) == []


def test_namen_kiezen():
    borden = ["Swinkels", "Krimpenerwaard", "Koninklijke Scheepsbouw De Vries", "Beeminds"]
    assert kies_op_naam("Krimpenerwaard", borden) == ["Krimpenerwaard"]
    assert kies_op_naam("krimpener", borden) == ["Krimpenerwaard"]
    assert kies_op_naam("de vries", borden) == ["Koninklijke Scheepsbouw De Vries"]
    # Zonder zoekterm alles, dan kiest de aanroeper.
    assert len(kies_op_naam(None, borden)) == 4


def test_een_exacte_naam_wint_van_een_langere_die_hem_bevat():
    namen = ["Swinkels", "Swinkels Family Brewers"]
    assert kies_op_naam("Swinkels", namen) == ["Swinkels"]
