"""routers/workflow_router.py — Workflow CRUD (markdown <-> steps, kept in
sync both ways) + manual run against a lab. Scheduled runs are wired in
schedule_router.py / services/scheduling/cron.py (Fase 5)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.lab import Lab
from models.workflow import Workflow, WorkflowRun
from services.workflows.workflow_service import (
    parse_markdown_to_steps, render_steps_to_markdown, steps_as_agent_instructions,
)

router = APIRouter(prefix="/workflows", tags=["workflows"], dependencies=[Depends(require_user)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dict(w: Workflow) -> Dict[str, Any]:
    return {
        "id": w.id, "name": w.name, "description": w.description,
        "markdown": w.markdown, "steps": w.steps_json or [],
        "is_enabled": w.is_enabled, "created_at": w.created_at, "updated_at": w.updated_at,
    }


@router.get("")
def list_workflows(db: Session = Depends(get_db)):
    return [_to_dict(w) for w in db.query(Workflow).order_by(Workflow.name.asc()).all()]


@router.post("")
def create_workflow(payload: Dict[str, Any], db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is verplicht")
    now = _now_iso()
    if "steps" in payload and payload["steps"] is not None:
        steps = payload["steps"]
        markdown = render_steps_to_markdown(steps)
    else:
        markdown = payload.get("markdown") or ""
        steps = parse_markdown_to_steps(markdown)
    w = Workflow(name=name, description=payload.get("description"),
                markdown=markdown, steps_json=steps,
                is_enabled=bool(payload.get("is_enabled", True)),
                created_at=now, updated_at=now)
    db.add(w)
    db.commit()
    return _to_dict(w)


@router.get("/{workflow_id}")
def get_workflow(workflow_id: int, db: Session = Depends(get_db)):
    w = db.get(Workflow, workflow_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workflow niet gevonden")
    return _to_dict(w)


@router.patch("/{workflow_id}")
def update_workflow(workflow_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Editing `steps` (the visual editor) re-renders markdown; editing
    `markdown` directly re-parses steps. Sending both is not expected — the
    front-end's tab switch decides which side is authoritative for the save."""
    w = db.get(Workflow, workflow_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workflow niet gevonden")
    if "name" in payload:
        w.name = payload["name"]
    if "description" in payload:
        w.description = payload["description"]
    if "is_enabled" in payload:
        w.is_enabled = bool(payload["is_enabled"])
    if "steps" in payload and payload["steps"] is not None:
        w.steps_json = payload["steps"]
        w.markdown = render_steps_to_markdown(payload["steps"])
    elif "markdown" in payload and payload["markdown"] is not None:
        w.markdown = payload["markdown"]
        w.steps_json = parse_markdown_to_steps(payload["markdown"])
    w.updated_at = _now_iso()
    db.commit()
    return _to_dict(w)


@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: int, db: Session = Depends(get_db)):
    w = db.get(Workflow, workflow_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workflow niet gevonden")
    db.delete(w)
    db.commit()
    return {"ok": True}


@router.post("/{workflow_id}/run")
async def run_workflow(workflow_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Run every step as one chat turn's instructions against a lab (manual
    trigger — the same path a cron Schedule uses, see schedule_router.py)."""
    w = db.get(Workflow, workflow_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workflow niet gevonden")
    lab_id = payload.get("lab_id")
    lab = db.get(Lab, lab_id) if lab_id else None
    if not lab or lab.status != "running":
        raise HTTPException(status_code=409, detail="Geef een draaiend lab_id op")

    run = WorkflowRun(id=str(uuid4()), workflow_id=workflow_id, lab_id=lab_id,
                      trigger_type="manual", status="running", created_at=_now_iso())
    db.add(run)
    db.commit()

    from services.agent.chat_agent import ChatAgent
    instructions = steps_as_agent_instructions(w.steps_json or parse_markdown_to_steps(w.markdown))
    prompt = f"Voer deze workflow uit: {w.name}\n\n{instructions}"
    agent = ChatAgent(db)
    answer = ""
    try:
        async for ev in agent.run_stream_events(lab_id=lab_id, user_input=prompt):
            if ev["kind"] == "answer":
                answer = ev["text"]
        run.status = "completed"
        run.output = answer
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)[:2000]
    run.finished_at = _now_iso()
    db.commit()
    return {"id": run.id, "status": run.status, "output": run.output, "error": run.error}
