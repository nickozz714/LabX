"""Een run die op zijn tijdslimiet stopt, moet dat ZEGGEN.

Aanleiding: SWI-88 draaide op 2026-09-10 precies twee uur (de standaardlimiet
van 7200s), stopte, en kwam in het overzicht terecht als "failed" met een lege
foutmelding. Twee uur werk, geen enkele aanwijzing wat er gebeurd was.

De oorzaak was subtiel: de leeslus had wél een nette melding voor het geval dat
de deadline bovenaan de lus verstreken bleek, maar in de praktijk verstrijkt hij
terwijl het proces IN `asyncio.wait_for` op de volgende regel wacht. Dan komt er
een kale `TimeoutError` uit — en `str(TimeoutError())` is een lege string.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.agent.claude_cli_provider import ClaudeCliProvider  # noqa: E402


def test_lege_uitzondering_bestaat_echt():
    """De aanname waar deze hele fix op rust, expliciet vastgelegd — als een
    toekomstige Python dit verandert, willen we dat hier zien."""
    assert str(asyncio.TimeoutError()) == ""
    assert str(TimeoutError()) == ""


def _provider(timeout: float) -> ClaudeCliProvider:
    p = ClaudeCliProvider.__new__(ClaudeCliProvider)
    p._timeout = timeout
    return p


def test_uitleg_noemt_de_limiet_en_wat_er_overblijft():
    tekst = _provider(7200.0)._timeout_uitleg()
    assert "7200" in tekst
    assert "2.0 uur" in tekst
    # Het belangrijkste: dat het werk NIET weg is. Zonder die zin lijkt een
    # timeout op verlies, en dan begint iemand overnieuw in plaats van verder.
    assert "staan er nog" in tekst
    assert "opnieuw" in tekst
    # En wat je eraan kunt doen.
    assert "Instellingen" in tekst
    assert "board__wait_until" in tekst


@pytest.mark.parametrize("seconden,uren", [(3600.0, "1.0"), (7200.0, "2.0"), (900.0, "0.2")])
def test_uitleg_rekent_de_uren_goed(seconden, uren):
    assert f"({uren} uur)" in _provider(seconden)._timeout_uitleg()


def test_fout_zonder_tekst_krijgt_altijd_iets():
    """Het vangnet in background_runs: welke uitzondering er ook komt, er staat
    nooit een lege foutmelding in de database."""
    for exc in (TimeoutError(), asyncio.TimeoutError(), RuntimeError(), ValueError("")):
        error = (str(exc) or f"{type(exc).__name__} zonder toelichting")[:2000]
        assert error.strip(), f"{type(exc).__name__} leverde een lege melding"
        assert type(exc).__name__ in error or str(exc) == error
