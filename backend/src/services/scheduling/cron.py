"""
services/scheduling/cron.py

Cron evaluation for Schedules, ported in spirit from ND3X's
workflow_factory.py tick(): croniter.get_prev() finds the last due fire time;
if it falls inside the lookback window and no ScheduleRun already exists for
that exact `scheduled_for`, enqueue one. Tick interval (30s, registered in
server.py) is shorter than the lookback (60s) so a fire is never missed
between ticks, and the scheduled_for de-dupe key prevents a double-run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

        lab = db.get(Lab, sched.lab_id)
        if not lab or lab.status != "running":
            run.status = "failed"
            run.error = "Lab draait niet"
            run.finished_at = _now_iso()
            db.commit()
            return

        if sched.workflow_id:
            wf = db.get(Workflow, sched.workflow_id)
            steps = (wf.steps_json if wf else None) or parse_markdown_to_steps(wf.markdown if wf else "")
            prompt = f"Voer deze workflow uit: {wf.name if wf else ''}\n\n{steps_as_agent_instructions(steps)}"
        else:
            prompt = sched.prompt or ""

        from services.agent.chat_agent import ChatAgent
        agent = ChatAgent(db)
        answer = ""
        try:
            async for ev in agent.run_stream_events(lab_id=sched.lab_id, user_input=prompt,
                                                    json_schema=sched.json_schema):
                if ev["kind"] == "answer":
                    answer = ev["text"]
            run.status = "completed"
            run.output = answer
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error = str(exc)[:2000]
        run.finished_at = _now_iso()
        sched.last_run_at = _now_iso()
        db.commit()
    finally:
        db.close()


async def tick() -> None:
    """Called every 30s by the DynamicScheduler. Finds schedules whose
    previous cron fire time falls within the lookback window and haven't
    already been enqueued for that exact minute."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due: list[tuple[int, str]] = []
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
        await _run_schedule(schedule_id, scheduled_for)
