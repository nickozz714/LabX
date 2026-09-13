"""Een vastgelopen agent-run moet te stoppen zijn.

Waargenomen op 13-09-2026: drie handmatig gestarte tickets stonden 23 minuten
op "running" met NUL stappen en containers op 0,09% CPU. Er draaide niets — de
asyncio-taak was verdwenen zonder de rij af te sluiten — maar er was geen weg
terug: zolang `agent_state` op "running" staat is "Agent starten" uitgeschakeld,
en een andere knop bestond niet.

Twee gaten tegelijk:

1. Dode runs werden alleen opgeruimd bij het OPSTARTEN en alleen voor
   PLANNINGEN (`PlanService._ruim_dode_runs_op`). Een handmatig gestarte run die
   tussentijds zijn taak verloor, bleef eeuwig staan — dezelfde blinde vlek als
   bij het kiezen van een werker.
2. Er was geen manier om een ticket-run af te breken.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_de_opruiming_kijkt_naar_de_lopende_taak_en_niet_naar_de_status():
    """De database zegt "running"; of er iets draait weet alleen het proces.
    Dat verschil is het hele punt."""
    import inspect

    from services.agent.background_runs import ruim_dode_runs_op

    bron = inspect.getsource(ruim_dode_runs_op)
    assert "is_active(r.id)" in bron
    assert 'r.status = "interrupted"' in bron


def test_een_net_begonnen_run_wordt_met_rust_gelaten():
    """Tussen het aanmaken van de rij en het registreren van de taak zit een
    moment. Zonder respijt zou deze opruiming een run kunnen afschieten die
    net begint — en dat is erger dan het probleem."""
    import inspect

    from services.agent.background_runs import ruim_dode_runs_op

    bron = inspect.getsource(ruim_dode_runs_op)
    assert "respijt_seconden" in bron
    assert "continue" in bron.split("grens")[2][:200]


def test_de_opruiming_geeft_ook_het_ticket_vrij():
    """Alleen de rij afsluiten is half werk: het ticket blijft dan op "de agent
    werkt eraan" staan, en dat is precies de knop die uitgeschakeld is."""
    import inspect

    from services.agent.background_runs import ruim_dode_runs_op

    assert "geef_tickets_vrij" in inspect.getsource(ruim_dode_runs_op)


def test_afbreken_werkt_ook_als_er_niets_meer_draait():
    """Het geval waar het om begon. `background_runs.cancel()` geeft False als
    er geen taak is; dan moet de rij alsnog dicht en het ticket alsnog los."""
    import inspect

    from services.boards.agent_work import cancel_ticket_run

    bron = inspect.getsource(cancel_ticket_run)
    assert "afgebroken = " in bron
    assert '"cancelled" if afgebroken else "interrupted"' in bron
    assert 'ticket.agent_state = "failed"' in bron


def test_afbreken_maakt_ook_de_planning_los():
    """Hoort het ticket bij een lopende planning, dan blijft die anders wachten
    op iets dat niet meer komt."""
    import inspect

    from services.boards.agent_work import cancel_ticket_run

    bron = inspect.getsource(cancel_ticket_run)
    assert "TicketPlanItem" in bron
    assert 'it.state = "failed"' in bron


def test_de_opruiming_draait_periodiek_en_niet_alleen_bij_het_opstarten():
    """Dat was het gat: `reconcile_on_start` vangt alleen wat een herstart
    achterlaat. Een run die tijdens bedrijf zijn taak verliest, werd nooit
    opgemerkt."""
    bron = (Path(__file__).resolve().parents[1] / "src/server.py").read_text(encoding="utf-8")
    assert 'name="dode_runs"' in bron
    assert "ruim_dode_runs_op" in bron


def test_er_is_een_endpoint_om_te_stoppen():
    bron = (Path(__file__).resolve().parents[1]
            / "src/routers/board_router.py").read_text(encoding="utf-8")
    assert "/agent/cancel" in bron
    assert "cancel_ticket_run" in bron


# ── en in de chat ───────────────────────────────────────────────────────────

def test_een_chatbeurt_is_per_thread_af_te_breken():
    """Per THREAD en niet per run-id, want dat is wat het scherm weet: je ziet
    een antwoord binnendruppelen, geen identificatie."""
    bron = (Path(__file__).resolve().parents[1]
            / "src/routers/chat_router.py").read_text(encoding="utf-8")
    assert '@router.post("/threads/{thread_id}/cancel")' in bron


def test_ook_een_chatbeurt_die_alleen_nog_in_de_database_leeft():
    """Anders strandt elke volgende beurt op "er loopt al een beurt in dit
    gesprek" — en dan is het gesprek dood zonder dat iemand er iets aan kan
    doen."""
    bron = (Path(__file__).resolve().parents[1]
            / "src/routers/chat_router.py").read_text(encoding="utf-8")
    blok = bron[bron.index('/threads/{thread_id}/cancel'):bron.index('/threads/{thread_id}/ask')]
    assert 'r.status = "interrupted"' in blok
    assert "background_runs.cancel(r.id)" in blok


def test_de_chat_stopt_de_run_en_niet_alleen_de_stream():
    """`abortRef.current?.abort()` stopt alleen het MEEKIJKEN; de beurt draait
    server-side door. De volgorde doet ertoe: eerst de run afbreken, dan pas de
    stream loslaten."""
    bron = (Path(__file__).resolve().parents[2]
            / "frontend/src/pages/ChatPage.tsx")
    if not bron.exists():
        pytest.skip("frontend niet aanwezig in deze omgeving")
    tekst = bron.read_text(encoding="utf-8")
    fn = tekst[tekst.index("async function stopBeurt()"):]
    fn = fn[:fn.index("\n  }")]
    assert fn.index("cancelTurn") < fn.index("abort()"), "eerst de run, dan de stream"
