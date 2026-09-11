"""
services/agent/hervatten.py

Werk dat op een gebruikslimiet stilviel weer oppakken zodra de limiet opengaat.

**Waarom dit los staat van de planning.** Een ticket dat vanuit een PLANNING
draait, wordt al hervat door de planning zelf: het item gaat in de wachtstand
met een hersteltijd, en `plan_service.tick` pakt het op — dezelfde weg als
`board__wait_until`. Wat daar níét onder valt is de rest: een ticket waar je
met de hand "Agent starten" op drukte, en een achtergrondtaak uit de chat. Die
hadden niemand die ze wakker maakte, en dat is precies het geval waarin je een
uur later een ticket vindt dat er afgebroken uitziet terwijl er niets aan de
hand was.

**Wat er NIET gebeurt, en met opzet.** De oude run wordt niet voortgezet maar
er komt een nieuwe. De CLI-sessie is weg, en de agent leest bij het hervatten
zijn eigen opmerkingen op het ticket terug — dat is dezelfde weg als na een
herstart van de backend, en de reden dat de prompt zo aandringt op die
opmerkingen. De oude run blijft als "limited" in de geschiedenis staan: die IS
gestopt, en dat wegpoetsen zou de enige aanwijzing verwijderen dat er een
limiet in het spel was.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.background_run import BackgroundRun

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _in_een_planning(db: Session, thread_id: str) -> bool:
    """Hoort deze run bij een planning die hem zelf al hervat?

    Zonder deze toets wordt hetzelfde ticket twee keer gestart: één keer door
    de planning en één keer hier. Twee agents in dezelfde container aan
    hetzelfde ticket is precies wat werkers en claims moeten voorkomen.
    """
    from models.board import Ticket
    from models.plan import TicketPlanItem

    ticket = db.query(Ticket).filter(Ticket.agent_thread_id == thread_id).first()
    if ticket is None:
        return False
    return (db.query(TicketPlanItem)
            .filter(TicketPlanItem.ticket_id == ticket.id,
                    TicketPlanItem.state.in_(("waiting", "blocked", "running"))).count() > 0)


async def hervat_gelimiteerde_runs(db: Session) -> int:
    """Runs waarvan de limiet voorbij is opnieuw starten. Geeft het aantal
    terug dat daadwerkelijk weer draait."""
    from models.board import Ticket
    from models.lab import Lab
    from models.thread import Thread
    from services.agent import background_runs

    nu = _now_iso()
    rijen: List[BackgroundRun] = (
        db.query(BackgroundRun)
        .filter(BackgroundRun.status == "limited",
                BackgroundRun.resume_at.isnot(None),
                BackgroundRun.resume_at <= nu).all())
    hervat = 0
    for run in rijen:
        # Meteen afvinken, wat er verder ook gebeurt: een run die niet hervat
        # kan worden moet niet elke minuut opnieuw geprobeerd worden.
        run.resume_at = None
        db.commit()

        if _in_een_planning(db, run.thread_id):
            log.infox("Gelimiteerde run overgeslagen: de planning hervat hem zelf",
                      run_id=run.id)
            continue

        thread = db.get(Thread, run.thread_id)
        if thread is None:
            continue
        lab = db.get(Lab, thread.lab_id) if thread.lab_id else None
        if lab is None:
            continue

        ticket = db.query(Ticket).filter(Ticket.agent_thread_id == thread.id).first()
        try:
            if ticket is not None:
                # Een ticket dat intussen weer draait of door iemand is
                # opgepakt, laten we met rust.
                if ticket.agent_state == "running" and ticket.agent_run_id and \
                        background_runs.is_active(ticket.agent_run_id):
                    continue
                from services.boards.agent_work import start_ticket_run
                await start_ticket_run(db, ticket.id, trigger="hervat na gebruikslimiet")
                log.infox("Ticket hervat na gebruikslimiet", ticket=ticket.key, run_id=run.id)
            else:
                if background_runs.active_foreground_run(db, thread.id) is not None:
                    continue
                from models.message import Message
                history = [{"role": m.role, "content": m.content} for m in
                           db.query(Message).filter(Message.thread_id == thread.id)
                           .order_by(Message.created_at.asc()).all()[-20:]
                           if m.role in ("user", "assistant")]
                background_runs.start(db, thread_id=thread.id, lab_id=lab.id,
                                      history=history, prompt=run.prompt,
                                      model=run.model, effort=run.effort)
                log.infox("Achtergrondtaak hervat na gebruikslimiet", run_id=run.id)
            hervat += 1
        except Exception as exc:  # noqa: BLE001 — één run mag de rest niet blokkeren
            log.warningx("Hervatten na gebruikslimiet mislukt", run_id=run.id,
                         error=str(exc)[:300])
    return hervat


async def tick() -> None:
    """Scheduler-ingang. Eigen sessie: dit draait los van elk verzoek."""
    from db.database import SessionLocal

    db = SessionLocal()
    try:
        aantal = await hervat_gelimiteerde_runs(db)
        if aantal:
            log.infox("Werk hervat na een gebruikslimiet", aantal=aantal)
    finally:
        db.close()
