"""routers/chat_router.py — threads (always lab-bound) + the SSE ask
endpoint. This is where "Zonder [lab] mag er niets werken" is enforced: a
thread cannot be created without a lab_id, and the ask endpoint 404s a
thread whose lab was deleted rather than silently falling back to some
unbound mode."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from authentication import require_user
from component_logging import get_logger
from db.database import get_db
from models.lab import Lab
from models.message import Message
from models.thread import Thread

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_user)])
log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_dict(t: Thread) -> Dict[str, Any]:
    return {"id": t.id, "title": t.title, "lab_id": t.lab_id, "model": t.model, "effort": t.effort,
            "created_at": t.created_at, "updated_at": t.updated_at}


def _message_dict(m: Message) -> Dict[str, Any]:
    return {"id": m.id, "thread_id": m.thread_id, "role": m.role,
            "content": m.content, "steps": m.steps or [], "created_at": m.created_at}


@router.get("/threads")
def list_threads(db: Session = Depends(get_db)):
    rows = db.query(Thread).order_by(Thread.updated_at.desc()).all()
    return [_thread_dict(t) for t in rows]


@router.post("/threads")
def create_thread(payload: Dict[str, Any], db: Session = Depends(get_db)):
    lab_id = (payload.get("lab_id") or "").strip()
    if not lab_id:
        # The fix for "GUI pagina waarin we chatten... Zonder mag er niets
        # werken": there is no unbound thread to fall back to.
        raise HTTPException(status_code=400, detail="lab_id is verplicht — koppel eerst een lab")
    lab = db.get(Lab, lab_id)
    if not lab:
        raise HTTPException(status_code=404, detail="Lab niet gevonden")
    now = _now_iso()
    t = Thread(id=str(uuid4()), title=(payload.get("title") or "Nieuwe chat")[:255],
              lab_id=lab_id, created_at=now, updated_at=now)
    db.add(t)
    db.commit()
    return _thread_dict(t)


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, db: Session = Depends(get_db)):
    t = db.get(Thread, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Thread niet gevonden")
    return _thread_dict(t)


@router.patch("/threads/{thread_id}")
def update_thread(thread_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    t = db.get(Thread, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Thread niet gevonden")
    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Titel mag niet leeg zijn")
        t.title = title[:255]
    if "model" in payload:
        model = (payload.get("model") or "").strip()
        t.model = model or None
    if "effort" in payload:
        effort = (payload.get("effort") or "").strip()
        t.effort = effort or None
    t.updated_at = _now_iso()
    db.commit()
    return _thread_dict(t)


@router.delete("/threads/{thread_id}")
def delete_thread(thread_id: str, db: Session = Depends(get_db)):
    t = db.get(Thread, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Thread niet gevonden")
    db.query(Message).filter(Message.thread_id == thread_id).delete(synchronize_session=False)
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.get("/threads/{thread_id}/messages")
def list_messages(thread_id: str, db: Session = Depends(get_db)):
    if not db.get(Thread, thread_id):
        raise HTTPException(status_code=404, detail="Thread niet gevonden")
    rows = db.query(Message).filter(Message.thread_id == thread_id).order_by(Message.created_at.asc()).all()
    return [_message_dict(m) for m in rows]


def _history_for_prompt(db: Session, thread_id: str, *, limit: int = 20) -> List[Dict[str, str]]:
    rows = (db.query(Message).filter(Message.thread_id == thread_id)
            .order_by(Message.created_at.asc()).all())[-limit:]
    return [{"role": m.role, "content": m.content} for m in rows if m.role in ("user", "assistant")]


@router.post("/threads/{thread_id}/background")
async def start_background(thread_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Non-blocking variant of ask: fires the same agent run via
    asyncio.create_task and returns the BackgroundRun row immediately —
    monitor it via /background-runs/{id}(/stream)."""
    t = db.get(Thread, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Thread niet gevonden")
    lab = db.get(Lab, t.lab_id)
    if not lab or lab.status != "running":
        raise HTTPException(status_code=409, detail="Het gekoppelde lab draait niet — start het eerst")
    text = str(payload.get("message") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Leeg bericht")

    now = _now_iso()
    user_msg = Message(id=str(uuid4()), thread_id=thread_id, role="user",
                       content=text, steps=[], created_at=now)
    db.add(user_msg)
    t.updated_at = now
    db.commit()

    from services.agent import background_runs
    history = _history_for_prompt(db, thread_id)
    run = background_runs.start(
        db, thread_id=thread_id, lab_id=lab.id, history=history, prompt=text,
        model=payload.get("model") or t.model, effort=payload.get("effort") or t.effort)
    return background_runs.to_dict(run)


@router.get("/background-runs")
def list_background_runs(thread_id: str | None = None, status: str | None = None,
                         mode: str | None = None,
                         limit: int = 100, db: Session = Depends(get_db)):
    from models.background_run import BackgroundRun
    from services.agent.background_runs import to_dict
    q = db.query(BackgroundRun)
    if thread_id:
        q = q.filter(BackgroundRun.thread_id == thread_id)
    if status:
        q = q.filter(BackgroundRun.status == status)
    if mode:
        q = q.filter(BackgroundRun.mode == mode)
    rows = q.order_by(BackgroundRun.created_at.desc()).limit(min(limit, 500)).all()
    return [to_dict(r) for r in rows]


@router.get("/background-runs/{run_id}")
def get_background_run(run_id: str, db: Session = Depends(get_db)):
    from models.background_run import BackgroundRun
    from services.agent.background_runs import to_dict
    r = db.get(BackgroundRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Achtergrondtaak niet gevonden")
    return to_dict(r)


@router.post("/background-runs/{run_id}/stream")
async def stream_background_run(run_id: str, db: Session = Depends(get_db)):
    """Replay persisted steps, then tail live events until the run reaches a
    terminal status. POST (not GET) to match the frontend's streamSSE helper."""
    import asyncio as _asyncio
    from models.background_run import BackgroundRun
    from services.agent import background_runs

    r = db.get(BackgroundRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Achtergrondtaak niet gevonden")
    replay = list(r.steps or [])
    if r.answer:
        replay.append({"kind": "answer", "text": r.answer})
    initial_status = r.status
    live = background_runs.is_active(run_id)

    async def _events():
        q = background_runs.subscribe(run_id) if live else None
        try:
            for ev in replay:
                yield f"data: {json.dumps(ev)}\n\n"
            if not live:
                yield f"data: {json.dumps({'kind': 'run_status', 'status': initial_status})}\n\n"
                return
            while True:
                try:
                    ev = await _asyncio.wait_for(q.get(), timeout=30.0)
                except _asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n"
                if ev.get("kind") == "run_status":
                    return
        finally:
            if q is not None:
                background_runs.unsubscribe(run_id, q)

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.post("/background-runs/{run_id}/cancel")
def cancel_background_run(run_id: str, db: Session = Depends(get_db)):
    from models.background_run import BackgroundRun
    from services.agent import background_runs
    r = db.get(BackgroundRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Achtergrondtaak niet gevonden")
    ok = background_runs.cancel(run_id)
    if not ok and r.status == "running":
        # Not in the active-task map (e.g. post-restart residue) — reconcile
        # the row instead of leaving it lying.
        r.status = "interrupted"
        r.finished_at = _now_iso()
        db.commit()
    return {"ok": True, "cancelled": ok}


@router.post("/threads/{thread_id}/ask")
async def ask(thread_id: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """SSE stream of {kind: session|thinking|tool|delta|answer} events. The
    turn itself runs SERVER-SIDE as a foreground run (background_runs infra):
    closing this stream — navigating away, switching threads — never kills
    the turn, and any client can reattach via /background-runs/{id}/stream.
    The user + assistant messages and the thread's CLI session id are
    persisted by the run itself, not by this request."""
    import asyncio as _asyncio
    t = db.get(Thread, thread_id)
    if not t:
        raise HTTPException(status_code=404, detail="Thread niet gevonden")
    lab = db.get(Lab, t.lab_id)
    if not lab or lab.status != "running":
        raise HTTPException(status_code=409, detail="Het gekoppelde lab draait niet — start het eerst")

    text = str(payload.get("message") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Leeg bericht")

    from services.agent import background_runs
    if background_runs.active_foreground_run(db, thread_id) is not None:
        raise HTTPException(status_code=409, detail="Er loopt al een beurt in dit gesprek — wacht tot die klaar is")

    now = _now_iso()
    user_msg = Message(id=str(uuid4()), thread_id=thread_id, role="user",
                       content=text, steps=[], created_at=now)
    db.add(user_msg)
    t.updated_at = now
    db.commit()

    history = _history_for_prompt(db, thread_id)
    run, q = background_runs.start(
        db, thread_id=thread_id, lab_id=lab.id, history=history, prompt=text,
        model=payload.get("model") or t.model, effort=payload.get("effort") or t.effort,
        mode="foreground", resume_session_id=t.cli_session_id, pre_subscribe=True)

    async def _events():
        try:
            yield f"data: {json.dumps({'kind': 'run', 'id': run.id})}\n\n"
            while True:
                try:
                    ev = await _asyncio.wait_for(q.get(), timeout=30.0)
                except _asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n"
                if ev.get("kind") == "run_status":
                    return
        finally:
            background_runs.unsubscribe(run.id, q)

    return StreamingResponse(_events(), media_type="text/event-stream")
