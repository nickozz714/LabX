"""routers/schedule_router.py — cron-driven Schedules against a lab (prompt
or workflow). The actual cron evaluation runs in
services/scheduling/cron.tick(), ticked by the scheduler registered in
server.py."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.lab import Lab
from models.schedule import Schedule, ScheduleRun
from models.workflow import Workflow

router = APIRouter(prefix="/schedules", tags=["schedules"], dependencies=[Depends(require_user)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dict(s: Schedule) -> Dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "cron_expression": s.cron_expression, "lab_id": s.lab_id,
        "prompt": s.prompt, "workflow_id": s.workflow_id, "is_enabled": s.is_enabled,
        "json_schema": s.json_schema,
        "last_run_at": s.last_run_at, "created_at": s.created_at, "updated_at": s.updated_at,
    }


def _validate(payload: Dict[str, Any], db: Session) -> None:
    cron_expr = payload.get("cron_expression")
    if cron_expr and not croniter.is_valid(cron_expr):
        raise HTTPException(status_code=400, detail=f"Ongeldige cron-expressie '{cron_expr}'")
    lab_id = payload.get("lab_id")
    if lab_id and not db.get(Lab, lab_id):
        raise HTTPException(status_code=404, detail="Lab niet gevonden")
    workflow_id = payload.get("workflow_id")
    if workflow_id and not db.get(Workflow, int(workflow_id)):
        raise HTTPException(status_code=404, detail="Workflow niet gevonden")


@router.get("")
def list_schedules(db: Session = Depends(get_db)):
    return [_to_dict(s) for s in db.query(Schedule).order_by(Schedule.name.asc()).all()]


@router.post("")
def create_schedule(payload: Dict[str, Any], db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip()
    if not name or not payload.get("cron_expression") or not payload.get("lab_id"):
        raise HTTPException(status_code=400, detail="name, cron_expression en lab_id zijn verplicht")
    _validate(payload, db)
    if not payload.get("prompt") and not payload.get("workflow_id"):
        raise HTTPException(status_code=400, detail="Geef een prompt of een workflow_id op")
    now = _now_iso()
    s = Schedule(name=name, cron_expression=payload["cron_expression"], lab_id=payload["lab_id"],
                prompt=payload.get("prompt"), workflow_id=payload.get("workflow_id"),
                json_schema=payload.get("json_schema"),
                is_enabled=bool(payload.get("is_enabled", True)), created_at=now, updated_at=now)
    db.add(s)
    db.commit()
    return _to_dict(s)


@router.get("/{schedule_id}")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    s = db.get(Schedule, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule niet gevonden")
    return _to_dict(s)


@router.patch("/{schedule_id}")
def update_schedule(schedule_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    s = db.get(Schedule, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule niet gevonden")
    _validate(payload, db)
    for field in ("name", "cron_expression", "lab_id", "prompt", "workflow_id", "is_enabled", "json_schema"):
        if field in payload:
            setattr(s, field, payload[field])
    s.updated_at = _now_iso()
    db.commit()
    return _to_dict(s)


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    s = db.get(Schedule, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule niet gevonden")
    db.query(ScheduleRun).filter(ScheduleRun.schedule_id == schedule_id).delete(synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.get("/{schedule_id}/runs")
def list_runs(schedule_id: int, db: Session = Depends(get_db)):
    if not db.get(Schedule, schedule_id):
        raise HTTPException(status_code=404, detail="Schedule niet gevonden")
    rows = (db.query(ScheduleRun).filter(ScheduleRun.schedule_id == schedule_id)
            .order_by(ScheduleRun.created_at.desc()).limit(100).all())
    return [{"id": r.id, "scheduled_for": r.scheduled_for, "status": r.status,
            "output": r.output, "error": r.error, "created_at": r.created_at,
            "finished_at": r.finished_at} for r in rows]
