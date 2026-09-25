"""routers/workflow_router.py — workflows beheren en uitvoeren.

Een workflow is een graaf van activiteiten die LabX zelf uitvoert (zie
services/workflows/engine.py). Markdown en `steps` blijven bestaan als im- en
export, en een workflow van vóór de graaf wordt bij het uitvoeren ingelezen als
een rechte keten.

Uitvoeren geeft meteen een run-id terug: een workflow van zeven activiteiten
duurt minuten tot uren, en daar mag geen HTTP-verzoek op wachten. De voortgang
lees je van de run en zijn stappen — dat is ook het spoor dat er achteraf nog
ligt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.lab import Lab
from models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from services.workflows import engine, graph
from services.workflows.workflow_service import (
    parse_markdown_to_steps, render_steps_to_markdown,
)

router = APIRouter(prefix="/workflows", tags=["workflows"], dependencies=[Depends(require_user)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dict(w: Workflow) -> Dict[str, Any]:
    nodes, edges = graph.zorg_voor_graaf(w)
    return {
        "id": w.id, "name": w.name, "description": w.description,
        "markdown": w.markdown, "steps": w.steps_json or [],
        "nodes": nodes, "edges": edges,
        # Waarschuwingen, geen fouten: een workflow mag half af opgeslagen
        # worden — je bent hem aan het bouwen.
        "waarschuwingen": graph.valideer(nodes, edges),
        "is_enabled": w.is_enabled, "created_at": w.created_at, "updated_at": w.updated_at,
    }


def _run_dict(run: WorkflowRun, stappen: list | None = None) -> Dict[str, Any]:
    uit: Dict[str, Any] = {
        "id": run.id, "workflow_id": run.workflow_id, "lab_id": run.lab_id,
        "status": run.status, "trigger_type": run.trigger_type,
        "trigger_ref": run.trigger_ref, "thread_id": run.thread_id,
        "output": run.output, "error": run.error,
        "totals": run.totals_json or {}, "input": run.input_json or {},
        "created_at": run.created_at, "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
    if stappen is not None:
        uit["stappen"] = [{
            "id": s.id, "node_id": s.node_id, "naam": s.naam, "soort": s.soort,
            "volgnummer": s.volgnummer, "iteratie": s.iteratie, "item": s.item,
            "status": s.status, "invoer": s.invoer, "uitvoer": s.uitvoer,
            "resultaat": s.resultaat_json, "stappen": s.stappen_json or [],
            "exit_code": s.exit_code, "error": s.error, "tak": s.tak,
            "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
            "cost_usd": s.cost_usd, "duur_ms": s.duur_ms,
            "created_at": s.created_at, "finished_at": s.finished_at,
        } for s in stappen]
    return uit


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
    nodes, edges = graph.normaliseer(payload.get("nodes") or [], payload.get("edges") or [])
    if not nodes:
        # Zonder getekende graaf: de stappen als rechte keten. Zo levert het
        # oude scherm nog steeds iets uitvoerbaars op.
        nodes, edges = graph.uit_stappen(steps)
    elif not steps:
        # Andersom ook: een getekende graaf levert de leesbare export op, zodat
        # markdown niet leeg blijft bij een workflow die nooit een stappenlijst
        # heeft gehad.
        steps = [{"index": i, "title": n["naam"], "instruction": n.get("prompt") or ""}
                 for i, n in enumerate([x for x in nodes if x["type"] == "agent"], start=1)]
        markdown = render_steps_to_markdown(steps)
    w = Workflow(name=name, description=payload.get("description"),
                markdown=markdown, steps_json=steps,
                nodes_json=nodes, edges_json=edges,
                is_enabled=bool(payload.get("is_enabled", True)),
                created_at=now, updated_at=now)
    db.add(w)
    db.commit()
    return _to_dict(w)


# Let op de volgorde: '/runs' moet vóór '/{workflow_id}' staan, anders vangt
# die laatste hem af en probeert FastAPI 'runs' als getal te lezen.
@router.get("/runs")
def list_runs(workflow_id: Optional[int] = None, limit: int = 50,
              db: Session = Depends(get_db)):
    """De laatste runs — handmatig én gepland, want dat is hetzelfde ding."""
    q = db.query(WorkflowRun)
    if workflow_id:
        q = q.filter(WorkflowRun.workflow_id == workflow_id)
    rijen = q.order_by(WorkflowRun.created_at.desc()).limit(max(1, min(limit, 200))).all()
    return [_run_dict(r) for r in rijen]


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    """Het volledige verslag: per activiteit de invoer, de redenatie, de
    uitvoer, de duur en wat het kostte."""
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run niet gevonden")
    stappen = (db.query(WorkflowRunStep)
               .filter(WorkflowRunStep.run_id == run_id)
               .order_by(WorkflowRunStep.volgnummer, WorkflowRunStep.id).all())
    return _run_dict(run, stappen)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)):
    """Afbreken tussen twee activiteiten. De lopende activiteit maakt hij af —
    een agent-beurt halverwege afkappen laat het lab in een toestand achter
    waarvan niemand meer weet welke."""
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run niet gevonden")
    if run.status not in ("pending", "running"):
        return _run_dict(run)
    engine.breek_af(run_id)
    return {"ok": True, "status": "afbreken gevraagd"}


@router.get("/{workflow_id}/verwijzingen")
def workflow_verwijzingen(workflow_id: int, node: Optional[str] = None,
                          db: Session = Depends(get_db)):
    """Waar je vanaf deze activiteit naar kunt verwijzen, en welke lijsten er
    zijn om met een lus langs te lopen.

    Dit is wat een keuzelijst vult in plaats van een veld waar je
    `stap.stap_2.json.rijen` uit je hoofd moet typen — een typefout daarin
    levert geen foutmelding op maar een voorwaarde die altijd onwaar is."""
    from services.workflows import verwijzingen as vw

    w = db.get(Workflow, workflow_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workflow niet gevonden")
    nodes, edges = graph.zorg_voor_graaf(w)
    return {"verwijzingen": vw.beschikbaar(nodes, edges, node),
            "lijsten": vw.lijsten(nodes)}


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
    if "nodes" in payload and payload["nodes"] is not None:
        # De graaf is leidend zodra hij meekomt; markdown en stappen lopen mee
        # als leesbare export van de agent-activiteiten.
        nodes, edges = graph.normaliseer(payload["nodes"], payload.get("edges") or [])
        w.nodes_json, w.edges_json = nodes, edges
        w.steps_json = [{"index": i, "title": n["naam"], "instruction": n.get("prompt") or ""}
                        for i, n in enumerate([x for x in nodes if x["type"] == "agent"], start=1)]
        w.markdown = render_steps_to_markdown(w.steps_json)
    elif "steps" in payload and payload["steps"] is not None:
        w.steps_json = payload["steps"]
        w.markdown = render_steps_to_markdown(payload["steps"])
        w.nodes_json, w.edges_json = graph.uit_stappen(payload["steps"])
    elif "markdown" in payload and payload["markdown"] is not None:
        w.markdown = payload["markdown"]
        w.steps_json = parse_markdown_to_steps(payload["markdown"])
        w.nodes_json, w.edges_json = graph.uit_stappen(w.steps_json)
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
    """Start de workflow tegen een lab en geef meteen de run terug.

    Het werk loopt op de achtergrond (services/workflows/engine.py); volg het
    met GET /workflows/runs/{run_id}."""
    w = db.get(Workflow, workflow_id)
    if not w:
        raise HTTPException(status_code=404, detail="Workflow niet gevonden")
    lab_id = payload.get("lab_id")
    lab = db.get(Lab, lab_id) if lab_id else None
    if not lab:
        raise HTTPException(status_code=409, detail="Geef een lab_id op")
    # Een lab dat slaapt is geen beletsel: de motor zet hem als eerste stap aan
    # (en zet dat ook in het verslag). Weigeren zou betekenen dat een workflow
    # precies niet draait op het moment waarvoor hij bestaat — 's nachts, als
    # het lab al uren niet gebruikt is.
    run = engine.maak_run(db, w, lab_id=lab_id, trigger_type="manual",
                          invoer=payload.get("invoer") or None,
                          worker_id=payload.get("worker_id"))
    if not engine.start_in_achtergrond(run.id):
        raise HTTPException(status_code=500, detail="De run kon niet gestart worden")
    return _run_dict(run)
