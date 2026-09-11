"""Een gebruikslimiet is geen fout.

Op 2026-09-11 liepen drie runs achter elkaar stuk met:

    Claude Code gaf een fout terug (success): You've hit your session limit
    · resets 1:40pm (UTC)

Alles eromheen werkte, het werk was niet stuk, en om 13:40 had het gewoon
verder gekund. Toch stonden die tickets als 'failed' op het bord en moest
iemand later uitzoeken wat er aan de hand was.

Deze tests leggen twee dingen vast: dat zo'n melding als limiet wordt herkend
in de vormen die de CLI gebruikt, en dat er altijd een bruikbare hersteltijd
uitkomt — ook als er geen tijd bij staat.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.agent.limiet import (  # noqa: E402
    BLIND_WACHTEN_MINUTEN, MARGE_SECONDEN, SessieLimiet, als_limiet, hersteltijd, is_limiet)

NU = datetime(2026, 9, 11, 12, 50, tzinfo=timezone.utc)


@pytest.mark.parametrize("tekst", [
    # De melding die het daadwerkelijk deed, letterlijk uit de database.
    "You've hit your session limit · resets 1:40pm (UTC)",
    "Claude AI usage limit reached|1757600400",
    "5-hour limit reached ∙ resets 3pm",
    "Your usage limit will reset at 15:00",
    "Rate limit exceeded",
    "You have reached your limit",
])
def test_wordt_herkend_als_limiet(tekst):
    assert is_limiet(tekst)


@pytest.mark.parametrize("tekst", [
    "Claude Code CLI faalde (exit 1): command not found",
    "De agent is gestopt op de tijdslimiet van 7200s",
    "Er ging iets mis met de pipeline",
    "", None,
])
def test_gewone_fouten_zijn_geen_limiet(tekst):
    assert not is_limiet(tekst)


def test_kloktijd_vanmiddag():
    """13:40 terwijl het 12:50 is: vandaag, straks."""
    t = hersteltijd("You've hit your session limit · resets 1:40pm (UTC)", nu=NU)
    assert t.date() == NU.date()
    assert t.hour == 13 and t.minute == 41  # 13:40 + 90s marge


def test_kloktijd_die_al_geweest_is_wordt_morgen():
    """Om 15:00 horen dat het 'om 1:40' weer mag, betekent vannacht — niet
    dertien uur geleden."""
    laat = NU.replace(hour=15)
    t = hersteltijd("resets 1:40am (UTC)", nu=laat)
    assert t.date() == (laat + timedelta(days=1)).date()
    assert t.hour == 1


def test_am_pm_wordt_goed_gelezen():
    assert hersteltijd("resets 3pm", nu=NU).hour == 15
    assert hersteltijd("resets 3am", nu=NU.replace(hour=1)).hour == 3
    # Middernacht en middag zijn de twee die mensen (en regexes) fout doen.
    assert hersteltijd("resets 12am", nu=NU).hour == 0
    assert hersteltijd("resets 12pm", nu=NU.replace(hour=9)).hour == 12


def test_epoch_wordt_gelezen():
    doel = NU + timedelta(hours=2)
    t = hersteltijd(f"Claude AI usage limit reached|{int(doel.timestamp())}", nu=NU)
    assert abs((t - doel).total_seconds() - MARGE_SECONDEN) < 2


def test_onzinnige_epoch_wordt_genegeerd():
    """Een getal achter een pijp is niet vanzelf een hersteltijd. Uit het
    verleden of over een week: dan liever de blinde wachttijd dan een run die
    nooit meer start."""
    for slecht in ("|1000000000", f"|{int((NU + timedelta(days=9)).timestamp())}"):
        t = hersteltijd(f"usage limit reached{slecht}", nu=NU)
        assert t == NU + timedelta(minutes=BLIND_WACHTEN_MINUTEN)


def test_zonder_tijd_toch_een_antwoord():
    """Nooit None: de aanroeper moet het werk kunnen inplannen, en 'ik weet het
    niet' zou daar alsnog een fout van maken."""
    t = hersteltijd("You have hit your session limit", nu=NU)
    assert t == NU + timedelta(minutes=BLIND_WACHTEN_MINUTEN)


def test_er_zit_altijd_marge_op():
    """Op de seconde herstarten vraagt dat twee klokken het eens zijn, en dat
    zijn ze nooit — dan loop je meteen weer tegen dezelfde muur."""
    t = hersteltijd("resets 1:40pm (UTC)", nu=NU)
    kaal = NU.replace(hour=13, minute=40, second=0, microsecond=0)
    assert (t - kaal).total_seconds() == MARGE_SECONDEN


def test_als_limiet_geeft_een_uitzondering_met_tijd():
    exc = als_limiet("You've hit your session limit · resets 1:40pm (UTC)", nu=NU)
    assert isinstance(exc, SessieLimiet)
    assert exc.resets_at.hour == 13
    tekst = str(exc)
    assert "13:41" in tekst
    # De oorspronkelijke melding moet erin blijven staan: zonder dat kun je
    # later niet nagaan of het écht een limiet was.
    assert "session limit" in tekst


def test_als_limiet_laat_gewone_fouten_met_rust():
    assert als_limiet("Claude Code CLI faalde (exit 1): boem") is None


# ── wat er met het WERK gebeurt ─────────────────────────────────────────────

def test_een_limiet_is_geen_gewone_fout():
    """De CLI meldt een limiet met dezelfde vorm als elke andere fout
    (`is_error: true, subtype: success`). Alleen aan de TEKST is te zien dat
    er niets stuk is — en dat onderscheid bepaalt of een ticket in de
    mislukt-hoek belandt of gewoon even wacht."""
    from services.agent.claude_cli_provider import ClaudeCliProvider

    echt = ClaudeCliProvider._fout_van_result(
        "success", "You've hit your session limit · resets 1:40pm (UTC)")
    assert isinstance(echt, SessieLimiet)

    gewoon = ClaudeCliProvider._fout_van_result("error", "spawn claude ENOENT")
    assert isinstance(gewoon, RuntimeError) and not isinstance(gewoon, SessieLimiet)


def test_hersteltijd_is_altijd_in_de_toekomst():
    """Een hersteltijd in het verleden zou het werk meteen opnieuw laten
    starten, regelrecht terug in dezelfde limiet."""
    nu = datetime.now(timezone.utc)
    for tekst in ("resets 1:40pm (UTC)", "resets 3am", "usage limit reached",
                  "You've hit your session limit"):
        assert hersteltijd(tekst, nu=nu) > nu
