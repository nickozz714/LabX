"""Geen eigen product-, werkgever- of klantnamen in wat de gebruiker ziet.

LabX is een product op zichzelf. Placeholders als "nectar", een voorbeeldsleutel
"BICC" of een hulptekst die naar een klant verwijst, maken er een intern
gereedschap van — en lekken bovendien namen naar iedereen die het scherm ziet.

Deze test kijkt naar de OPPERVLAKTE: de teksten in de interface, de
beschrijvingen die de agent leest, en de voorbeelden in de bordinstellingen.
Codecommentaar blijft buiten schot; daar mag de echte aanleiding gewoon
opgeschreven staan, want dat leest alleen wie de code openslaat.
"""
import re
import sys
from pathlib import Path

import pytest

WORTEL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORTEL / "backend" / "src"))

# Eigen producten, de werkgever, klanten en hun projecten. Bewust letterlijk:
# een reguliere expressie die "hive" of "lab" vangt, raakt te veel.
VERBODEN = [
    "nectar", "nd3x", "beeminds", "intelligenthive", "hivemind",
    "swinkels", "krimpenerwaard", "bicc", "rsfb", "nickduchatinier",
    "hive_recall", "hive_search",
]

_REGELCOMMENTAAR = re.compile(r"^\s*(//|\*|/\*)")


def _zonder_commentaar(bron: str) -> str:
    """Grofweg de commentaarregels eruit. Grof is hier genoeg: we zoeken naar
    namen, niet naar syntaxis, en een enkele meegenomen regel levert hooguit
    een strengere test op."""
    zonder_blok = re.sub(r"/\*.*?\*/", "", bron, flags=re.S)
    return "\n".join(r for r in zonder_blok.splitlines()
                     if not _REGELCOMMENTAAR.match(r))


FRONTEND = sorted((WORTEL / "frontend" / "src").rglob("*.tsx")) + \
           sorted((WORTEL / "frontend" / "src").rglob("*.ts"))


@pytest.mark.parametrize("pad", FRONTEND, ids=lambda p: p.name)
def test_de_interface_noemt_geen_eigen_namen(pad):
    tekst = _zonder_commentaar(pad.read_text()).lower()
    gevonden = [naam for naam in VERBODEN if naam in tekst]
    assert not gevonden, (
        f"{pad.relative_to(WORTEL)} noemt {', '.join(gevonden)} in code of tekst die "
        f"de gebruiker kan zien. Maak het generiek.")


def test_de_systeemprompt_noemt_geen_eigen_namen():
    """De agent leest dit bij elke beurt, en het komt terug in het auditspoor."""
    from services.agent.chat_agent import AGENT_PREAMBLE

    laag = AGENT_PREAMBLE.lower()
    assert not [n for n in VERBODEN if n in laag]


def test_de_tools_die_de_agent_ziet_noemen_geen_eigen_namen():
    """De beschrijvingen van de ingebouwde tools; die staan letterlijk in de
    context van het model."""
    bron = (WORTEL / "backend" / "src" / "services" / "mcp" / "gateway.py").read_text()
    beschrijvingen = re.findall(r'description=\(([^)]*)\)', bron, flags=re.S)
    beschrijvingen += re.findall(r'\("board__\w+",\s*\n\s*"(.*?)",\s*\n\s*\{',
                                 bron, flags=re.S)
    samen = " ".join(beschrijvingen).lower()
    gevonden = [n for n in VERBODEN if n in samen]
    assert not gevonden, f"tool-beschrijving noemt {', '.join(gevonden)}"


def test_de_voorbeelden_in_de_bordinstellingen_zijn_generiek():
    """Die placeholders staan in het scherm waar je een koppeling instelt."""
    bron = (WORTEL / "backend" / "src" / "routers" / "board_router.py").read_text()
    voorbeelden = " ".join(re.findall(r'"placeholder":\s*"([^"]*)"', bron)).lower()
    gevonden = [n for n in VERBODEN if n in voorbeelden]
    assert not gevonden, f"placeholder noemt {', '.join(gevonden)}"
