"""
services/scheduling/cron.py

Cron evaluation for Schedules, ported in spirit from ND3X's
workflow_factory.py tick(): croniter.get_prev() finds the last due fire time;
if it falls inside the lookback window and no ScheduleRun already exists for
that exact `scheduled_for`, enqueue one. Tick interval (30s, registered in
server.py) is shorter than the lookback (60s) so a fire is never missed
between ticks, and the scheduled_for de-dupe key prevents a double-run.

Een schedule voert één van drie dingen uit (Schedule.kind):
- "prompt"   — de prompt tegen het lab
- "workflow" — de stappen van een workflow tegen het lab
- "board"    — tickets uit de agent-kolom van een board laten oppakken

Belangrijk: de tick WACHT NIET op de runs. Een agent-run duurt minuten; zou de
tick erop wachten, dan blokkeert schedule A schedule B én slaat de scheduler
(die een nog lopende taak overslaat) de volgende tick over — waardoor fires
verloren gaan. Elke due run krijgt daarom zijn eigen asyncio-task.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Set
from uuid import uuid4

from croniter import croniter

from component_logging import get_logger
from db.database import SessionLocal
from models.lab import Lab
from models.schedule import Schedule, ScheduleRun
from models.workflow import Workflow
from services.workflows.workflow_service import parse_markdown_to_steps, steps_as_agent_instructions

log = get_logger(__name__)

_LOOKBACK_SECONDS = 60

# Runs die nu draaien, zodat een tweede tick dezelfde fire niet nog eens start
# vóór de ScheduleRun-rij zichtbaar is (de de-dupe op scheduled_for dekt de
# database-kant; dit dekt de race binnen één proces).
_IN_FLIGHT: Set[str] = set()
_TASKS: Set[asyncio.Task] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kind_of(sched: Schedule) -> str:
    """Rijen van vóór de `kind`-kolom hebben hem niet — leid hem dan af."""
    kind = (getattr(sched, "kind", None) or "").strip()
    if kind:
        return kind
    if getattr(sched, "board_id", None):
        return "board"
    return "workflow" if sched.workflow_id else "prompt"


async def _run_board_schedule(db, sched: Schedule, run: ScheduleRun) -> None:
    """Board-werk is niet één agent-run maar N stuks (één per ticket), die
    zelfstandig doorlopen. De ScheduleRun rapporteert dus wat er GESTART is —
    het resultaat per ticket landt op het ticket zelf."""
    from services.boards.agent_work import pick_up_column
    started = pick_up_column(db, sched.board_id, column=sched.board_column,
                             max_tickets=sched.board_max_tickets or 1,
                             trigger=f"schedule '{sched.name}'")
    if not started:
        run.status = "completed"
        run.output = "Geen tickets klaar om op te pakken."
        return
    lines = [f"{len(started)} ticket(s) opgepakt:"]
    for item in started:
        if item.get("status") == "failed":
            lines.append(f"- {item.get('ticket_key')}: mislukt — {item.get('error')}")
        else:
            lines.append(f"- {item.get('ticket_key')}: agent-run {str(item.get('run_id'))[:8]} gestart")
    run.status = "completed"
    run.output = "\n".join(lines)


async def _run_agent_schedule(db, sched: Schedule, run: ScheduleRun) -> None:
    lab = db.get(Lab, sched.lab_id)
    if not lab or lab.status != "running":
        run.status = "failed"
        run.error = "Lab draait niet"
        return

    if _kind_of(sched) == "workflow" and sched.workflow_id:
        wf = db.get(Workflow, sched.workflow_id)
        if wf is None:
            run.status = "failed"
            run.error = "De gekoppelde workflow bestaat niet meer"
            return
        steps = wf.steps_json or parse_markdown_to_steps(wf.markdown)
        prompt = f"Voer deze workflow uit: {wf.name}\n\n{steps_as_agent_instructions(steps)}"
    else:
        prompt = sched.prompt or ""
    if not prompt.strip():
        run.status = "failed"
        run.error = "Deze schedule heeft niets uit te voeren (lege prompt)"
        return

    from services.agent.chat_agent import ChatAgent
    agent = ChatAgent(db)
    answer = ""
    async for ev in agent.run_stream_events(lab_id=sched.lab_id, user_input=prompt,
                                            json_schema=sched.json_schema):
        if ev["kind"] == "answer":
            answer = ev["text"]
    run.status = "completed"
    run.output = answer


async def _run_schedule(schedule_id: int, scheduled_for: str) -> None:
    db = SessionLocal()
    try:
        sched = db.get(Schedule, schedule_id)
        if not sched or not sched.is_enabled:
            return
        run = ScheduleRun(id=str(uuid4()), schedule_id=schedule_id, scheduled_for=scheduled_for,
                          status="running", created_at=_now_iso())
        db.add(run)
        db.commit()

        try:
            if _kind_of(sched) == "board" and sched.board_id:
                await _run_board_schedule(db, sched, run)
            else:
                await _run_agent_schedule(db, sched, run)
        except Exception as exc:  # noqa: BLE001 — een run legt zijn fout vast, hij gooit niet door
            run.status = "failed"
            run.error = str(exc)[:2000]
        run.finished_at = _now_iso()
        sched.last_run_at = _now_iso()
        db.commit()
    finally:
        db.close()
        _IN_FLIGHT.discard(f"{schedule_id}:{scheduled_for}")


def run_now(schedule_id: int) -> str:
    """Handmatig vuren ("Nu uitvoeren" in de UI). Gebruikt hetzelfde pad als
    de cron, met een eigen scheduled_for-stempel zodat hij niet botst met de
    de-dupe van een echte fire."""
    scheduled_for = f"manual:{_now_iso()}"
    _spawn(schedule_id, scheduled_for)
    return scheduled_for


def _spawn(schedule_id: int, scheduled_for: str) -> None:
    key = f"{schedule_id}:{scheduled_for}"
    if key in _IN_FLIGHT:
        return
    _IN_FLIGHT.add(key)
    task = asyncio.get_running_loop().create_task(_run_schedule(schedule_id, scheduled_for))
    # Een taak zonder harde referentie mag door de GC opgeruimd worden
    # (Python's eigen documentatie) — vasthouden tot hij klaar is.
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def tick() -> None:
    """Called every 30s by the DynamicScheduler. Finds schedules whose
    previous cron fire time falls within the lookback window and haven't
    already been enqueued for that exact minute."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due: List[tuple[int, str]] = []
        for sched in db.query(Schedule).filter(Schedule.is_enabled == True).all():  # noqa: E712
            try:
                itr = croniter(sched.cron_expression, now)
                previous_due = itr.get_prev(datetime)
            except Exception as exc:  # noqa: BLE001 — a bad cron expression must not break the tick
                log.warningx("Ongeldige cron-expressie overgeslagen", schedule_id=sched.id, error=str(exc))
                continue
            if previous_due.tzinfo is None:
                previous_due = previous_due.replace(tzinfo=timezone.utc)
            delta = (now - previous_due).total_seconds()
            if not (0 <= delta <= _LOOKBACK_SECONDS):
                continue
            scheduled_for = previous_due.isoformat()
            exists = (db.query(ScheduleRun)
                     .filter(ScheduleRun.schedule_id == sched.id,
                             ScheduleRun.scheduled_for == scheduled_for)
                     .first())
            if exists:
                continue
            due.append((sched.id, scheduled_for))
    finally:
        db.close()

    for schedule_id, scheduled_for in due:
        _spawn(schedule_id, scheduled_for)
