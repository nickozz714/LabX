"""Een run mag zijn antwoord niet kwijtraken door een schrijffout onderweg.

Dit gebeurde echt, op 16-10-2026 in de chat van het lab Swinkels. Er liepen
twee chats, een bordsync en de modelpool tegelijk; SQLite laat onder WAL één
schrijver toe, en de VOORTGANGS-update van de run liep op "database is
locked". Die fout sloeg door naar de buitenste except, de sessie bleef in een
afgebroken transactie achter, en daardoor mislukte óók het wegschrijven van
het echte antwoord. De run bleef op "running" staan tot de opruiming hem als
"verdwenen" markeerde — en de gebruiker zag zijn antwoord nooit verschijnen.

Twee regels liggen hier vast:
  1. Een mislukte voortgangsregel kost je niets. Die is cosmetisch.
  2. Een kapotte sessie wordt eerst opgeruimd, zodat het antwoord er nog in kan.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _bron() -> str:
    return (Path(__file__).resolve().parents[1] / "src" / "services" / "agent"
            / "background_runs.py").read_text(encoding="utf-8")


def test_een_mislukte_voortgangsregel_breekt_de_run_niet_af():
    """De commit die de voortgang opslaat heeft een eigen vangnet.

    Zonder dat vangnet eindigt een run die prima werkte als "verdwenen",
    omdat een cosmetische schrijfactie toevallig op een slot liep.
    """
    bron = _bron()
    kop = bron.index("Persist progress as it happens")
    staart = bron.index("except asyncio.CancelledError", kop)
    blok = bron[kop:staart]
    assert "try:" in blok and "db.rollback()" in blok, (
        "de voortgangs-commit hoort een eigen try/except met rollback te hebben")


def test_de_afronding_begint_met_een_schone_sessie():
    """Was er onderweg een schrijffout, dan hangt de sessie in een afgebroken
    transactie en mislukt élke volgende schrijfactie — inclusief die van het
    antwoord zelf. Precies zo eindigde een afgeronde run alsnog als
    'verdwenen'."""
    bron = _bron()
    kop = bron.index("    finally:\n        try:\n")
    blok = bron[kop:kop + 600]
    assert "db.rollback()" in blok, (
        "de afronding hoort met een rollback te beginnen")
    assert blok.index("db.rollback()") < blok.index("db.get(BackgroundRun"), (
        "de rollback moet VOOR het ophalen van de rij staan")


def test_sqlite_wacht_lang_genoeg_op_het_slot():
    """Vijf seconden was te krap zodra er meer tegelijk schrijft. Een
    schrijver die dertig seconden wacht is onzichtbaar; eentje die opgeeft
    kost een gebruiker zijn antwoord."""
    bron = (Path(__file__).resolve().parents[1] / "src" / "db"
            / "database.py").read_text(encoding="utf-8")
    assert "busy_timeout=30000" in bron
    assert "journal_mode=WAL" in bron


def test_de_guard_leest_zijn_antwoord_buiten_het_finally():
    """In een `finally` na een time-out bestaat `resp` niet, en dan verbergt
    een UnboundLocalError de echte oorzaak. Het log meldde "cannot access
    local variable 'resp'" terwijl er gewoon een time-out was."""
    bron = (Path(__file__).resolve().parents[1] / "src" / "services" / "lab"
            / "data_guard_llm.py").read_text(encoding="utf-8")
    kop = bron.index("ModelPool.vrij(runner_id)")
    eind = bron.index("content = (resp.json()", kop)
    tussen = bron[kop:eind]
    # Tussen het vrijgeven en het uitlezen hoort het finally-blok te eindigen:
    # de uitleesregel staat op acht spaties, niet op twaalf.
    assert "\n        content = (resp.json()" in bron[kop - 200:eind + 60], (
        "het uitlezen van het antwoord hoort BUITEN het finally te staan")
    assert "            content = (resp.json()" not in bron
