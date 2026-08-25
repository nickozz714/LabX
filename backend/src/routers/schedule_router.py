"""routers/schedule_router.py — cron-driven Schedules against a lab: een
prompt, een workflow, of board-werk dat de agent oppakt (Schedule.kind). De
cron-evaluatie zelf draait in services/scheduling/cron.tick(), getikt door de
scheduler uit server.py."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.board import Board
from models.lab import Lab
from models.schedule import SCHEDULE_KINDS, Schedule, ScheduleRun
from models.workflow import Workflow

router = APIRouter(prefix="/schedules", tags=["schedules"], dependencies=[Depends(require_user)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kind_of(s: Schedule) -> str:
    """Rijen van vóór de `kind`-kolom dragen hem niet — leid hem dan af, zodat
    een bestaande schedule niet plots als "prompt" in de UI verschijnt."""
    kind = (getattr(s, "kind", None) or "").strip()
    if kind:
        return kind
    if getattr(s, "board_id", None):
        return "board"
    return "workflow" if s.workflow_id else "prompt"


def _to_dict(s: Schedule) -> Dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "cron_expression": s.cron_expression, "lab_id": s.lab_id,
        "kind": _kind_of(s),
        "prompt": s.prompt, "workflow_id": s.workflow_id,
        "board_id": s.board_id, "board_column": s.board_column,
        "board_max_tickets": s.board_max_tickets,
        "is_enabled": s.is_enabled,
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
    board_id = payload.get("board_id")
    if board_id and not db.get(Board, int(board_id)):
        raise HTTPException(status_code=404, detail="Board niet gevonden")
    kind = payload.get("kind")
    if kind and kind not in SCHEDULE_KINDS:
        raise HTTPException(status_code=400, detail=f"Onbekende kind '{kind}'")


def _assert_target(kind: str, payload: Dict[str, Any]) -> None:
    """Een schedule zonder uit te voeren werk is stil kapot: hij vuurt netjes
    en doet niets. Hier valt hij om, bij het opslaan."""
    if kind == "board":
        if not payload.get("board_id"):
            raise HTTPException(status_code=400, detail="Een board-schedule heeft een board_id nodig")
    elif kind == "workflow":
        if not payload.get("workflow_id"):
            raise HTTPException(status_code=400, detail="Een workflow-schedule heeft een workflow_id nodig")
    elif not str(payload.get("prompt") or "").strip():
        raise HTTPException(status_code=400, detail="Een prompt-schedule heeft een prompt nodig")


@router.get("")
def list_schedules(db: Session = Depends(get_db)):
    return [_to_dict(s) for s in db.query(Schedule).order_by(Schedule.name.asc()).all()]


@router.post("")
def create_schedule(payload: Dict[str, Any], db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip()
    if not name or not payload.get("cron_expression") or not payload.get("lab_id"):
        raise HTTPException(status_code=400, detail="name, cron_expression en lab_id zijn verplicht")
    _validate(payload, db)
    kind = payload.get("kind") or ("board" if payload.get("board_id")
                                   else "workflow" if payload.get("workflow_id") else "prompt")
    _assert_target(kind, payload)
    now = _now_iso()
    s = Schedule(name=name, cron_expression=payload["cron_expression"], lab_id=payload["lab_id"],
                kind=kind,
                prompt=payload.get("prompt"), workflow_id=payload.get("workflow_id"),
                board_id=payload.get("board_id"), board_column=payload.get("board_column"),
                board_max_tickets=int(payload.get("board_max_tickets") or 1),
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
    for field in ("name", "cron_expression", "lab_id", "kind", "prompt", "workflow_id",
                  "board_id", "board_column", "board_max_tickets", "is_enabled", "json_schema"):
        if field in payload:
            setattr(s, field, payload[field])
    _assert_target(_kind_of(s), {"prompt": s.prompt, "workflow_id": s.workflow_id,
                                 "board_id": s.board_id})
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


@router.post("/{schedule_id}/run")
def run_schedule_now(schedule_id: int, db: Session = Depends(get_db)):
    """Nu uitvoeren, zonder op de cron te wachten. Loopt via hetzelfde pad als
    een echte fire en levert dus ook een gewone ScheduleRun op."""
    if not db.get(Schedule, schedule_id):
        raise HTTPException(status_code=404, detail="Schedule niet gevonden")
    from services.scheduling.cron import run_now
    return {"ok": True, "scheduled_for": run_now(schedule_id)}
