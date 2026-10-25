"""De huisstijl voor teksten hoort in élke run te zitten, niet in één scherm.

Waarom een test op een stuk prompttekst: dit is een regel die onzichtbaar
wegvalt. Verdwijnt de regel uit de preamble -- door een merge, door een
herschrijving van het blok eromheen -- dan merkt niemand dat, want de agent
blijft gewoon antwoorden. Alleen leest het weer als een opstel.

De tweede test is de belangrijkste. "Houd het kort" is een instructie die een
model te ver kan doortrekken: dan laat het ook bevindingen weg. De grens
daartegen staat expliciet in de tekst, en als iemand die eruit poetst omdat
het "dubbelop" leest, valt deze test om.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_de_schrijfstijl_zit_in_de_preamble_van_elke_run():
    from services.agent.chat_agent import AGENT_PREAMBLE
    from services.agent.schrijfstijl import SCHRIJFSTIJL

    assert SCHRIJFSTIJL in AGENT_PREAMBLE


def test_de_kernregels_staan_erin():
    """Niet de formulering, wel de regels zelf -- herschrijven mag."""
    from services.agent.schrijfstijl import SCHRIJFSTIJL

    tekst = SCHRIJFSTIJL.lower()
    assert "lead with the answer" in tekst          # het antwoord vooraan
    assert "numbered list" in tekst                 # stappen genummerd
    assert "what is done, what is next" in tekst    # stand van zaken herhalen
    assert "no preamble" in tekst                   # geen aanloop
    assert "five items" in tekst                    # korte zichtbare lijst


def test_kort_schrijven_mag_nooit_informatie_kosten():
    """De grens tegen 'kort' dat 'onvolledig' wordt."""
    from services.agent.schrijfstijl import SCHRIJFSTIJL

    tekst = SCHRIJFSTIJL.lower()
    assert "never drop something relevant" in tekst
    assert "form only" in tekst
    assert "never reduce how much you investigate" in tekst


def test_de_uitzonderingen_staan_erbij():
    """Zonder deze drie wordt de regel schadelijk: geen uitleg meer als erom
    gevraagd wordt, geen bevestiging voor iets onomkeerbaars, en een ticket
    dat een eigen vorm voorschrijft wordt genegeerd."""
    from services.agent.schrijfstijl import SCHRIJFSTIJL

    tekst = SCHRIJFSTIJL.lower()
    assert "explain it fully" in tekst
    assert "before anything destructive" in tekst
    assert "outrank this" in tekst


def test_de_taal_van_het_antwoord_blijft_die_van_de_gebruiker():
    """De preamble zegt al 'answer in the same language'. Een vormregel in het
    Engels mag dat niet per ongeluk omzetten naar Engelse antwoorden."""
    from services.agent.schrijfstijl import SCHRIJFSTIJL

    assert "same language the user used" in SCHRIJFSTIJL.lower()


def test_de_regels_staan_op_een_plek():
    """Eén bron. Een tweede kopie in een andere prompt loopt uiteen zodra er
    iets verandert, en dan schrijft board-werk anders dan de chat."""
    src = Path(__file__).resolve().parents[1] / "src"
    treffers = [
        pad for pad in src.rglob("*.py")
        if pad.name != "schrijfstijl.py"
        and re.search(r"Lead with the answer", pad.read_text(encoding="utf-8"))
    ]
    assert treffers == [], f"schrijfstijl staat dubbel in: {treffers}"
