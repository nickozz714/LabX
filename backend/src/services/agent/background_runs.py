"""
services/agent/background_runs.py

Non-blocking chat tasks — LabX's own equivalent of Claude Code's background
agents. The CLI's native `--bg`/`claude agents` feature is architecturally
unusable here (verified live: `--bg` hard-conflicts with `-p`, and LabX's
whole agent path is headless `-p --output-format stream-json`), so this
module reimplements the same UX at the LabX level: fire a run, the HTTP
request returns immediately, and the run's thinking/tool/delta events are
both persisted (steps column) and fanned out live to any number of
subscribers — exactly the monitoring experience the CLI gives, but through
LabX's own API/UI.

Concurrency note: a background run always starts its OWN CLI session
(resume_session_id=None). The thread's cli_session_id must never be shared —
the CLI's session file has no multi-writer safety and the foreground chat
may be appending to it at the same moment.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.background_run import BackgroundRun

log = get_logger(__name__)

# Strong references so tasks aren't garbage-collected mid-flight (a bare
# unreferenced asyncio.Task can be GC'd, per Python's own docs) and so
# cancel() can find them.
_ACTIVE_TASKS: Dict[str, asyncio.Task] = {}
_SUBSCRIBERS: Dict[str, List[asyncio.Queue]] = {}

_TERMINAL = ("completed", "failed", "cancelled", "interrupted")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_dict(r: BackgroundRun) -> Dict[str, Any]:
    return {
        "id": r.id, "thread_id": r.thread_id, "prompt": r.prompt,
        "model": r.model, "effort": r.effort, "status": r.status,
        "mode": getattr(r, "mode", "background") or "background",
        "steps": r.steps or [], "answer": r.answer, "error": r.error,
        "message_id": r.message_id,
        "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at,
    }


def subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _SUBSCRIBERS.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    subs = _SUBSCRIBERS.get(run_id)
    if subs and q in subs:
        subs.remove(q)
    if subs is not None and not subs:
        _SUBSCRIBERS.pop(run_id, None)


def _publish(run_id: str, event: Dict[str, Any]) -> None:
    for q in _SUBSCRIBERS.get(run_id, []):
        q.put_nowait(event)


def is_active(run_id: str) -> bool:
    t = _ACTIVE_TASKS.get(run_id)
    return t is not None and not t.done()


def start(db: Session, *, thread_id: str, lab_id: str,
          history: List[Dict[str, str]], prompt: str,
          model: Optional[str] = None, effort: Optional[str] = None,
          mode: str = "background",
          resume_session_id: Optional[str] = None,
          pre_subscribe: bool = False):
    """Create + fire a run. mode="foreground" is a normal chat turn: it
    resumes the thread's CLI session, keeps the task tools available, and
    persists a plain assistant message. pre_subscribe=True returns
    (run, queue) with the queue registered BEFORE the task starts, so the
    caller's live stream can't miss the first events."""
    now = _now_iso()
    run = BackgroundRun(id=str(uuid4()), thread_id=thread_id, prompt=prompt,
                        model=model, effort=effort, status="running", mode=mode,
                        steps=[], created_at=now, started_at=now)
    db.add(run)
    db.commit()
    db.refresh(run)

    q = subscribe(run.id) if pre_subscribe else None
    task = asyncio.get_running_loop().create_task(
        _execute(run.id, lab_id=lab_id, history=history, model=model, effort=effort,
                 mode=mode, resume_session_id=resume_session_id))
    _ACTIVE_TASKS[run.id] = task
    task.add_done_callback(lambda _t: _ACTIVE_TASKS.pop(run.id, None))
    return (run, q) if pre_subscribe else run


async def _execute(run_id: str, *, lab_id: str, history: List[Dict[str, str]],
                   model: Optional[str], effort: Optional[str],
                   mode: str = "background",
                   resume_session_id: Optional[str] = None) -> None:
    from db.database import SessionLocal
    from models.message import Message
    from services.agent.chat_agent import ChatAgent

    db = SessionLocal()
    steps: List[Dict[str, Any]] = []
    answer: str = ""
    session_id: Optional[str] = None
    status = "completed"
    error: Optional[str] = None
    foreground = mode == "foreground"
    run_row = db.get(BackgroundRun, run_id)
    thread_id_for_run = run_row.thread_id if run_row else None
    try:
        agent = ChatAgent(db)
        async for ev in agent.run_stream_events(
            lab_id=lab_id, user_input=history,
            # Foreground = a normal turn: resume the thread's CLI session and
            # keep the task__* tools. Background = own fresh session, no
            # thread context + explicit flag so the gateway doesn't register
            # the task tools (no task-spawning trees; backstopped in
            # internal_router).
            resume_session_id=resume_session_id if foreground else None,
            model=model, effort=effort,
            thread_id=thread_id_for_run if foreground else None,
            is_background=not foreground,
        ):
            kind = ev.get("kind")
            if kind == "session":
                session_id = ev.get("id")
            elif kind == "answer":
                answer = ev.get("text") or ""
            elif kind in ("thinking", "tool", "usage"):
                steps.append(ev)
            # delta events are fanned out live but not persisted per-chunk —
            # the final answer supersedes them, same as the foreground chat.
            _publish(run_id, ev)
            if kind in ("thinking", "tool"):
                # Persist progress as it happens so a fresh page load (or a
                # backend that later restarts) shows how far the run got.
                run = db.get(BackgroundRun, run_id)
                if run is not None:
                    run.steps = list(steps)
                    if session_id:
                        run.cli_session_id = session_id
                    db.commit()
    except asyncio.CancelledError:
        status = "cancelled"
    except Exception as exc:  # noqa: BLE001 — a background run must record, not raise
        status = "failed"
        error = str(exc)[:2000]
        log.warningx("achtergrondtaak mislukt", run_id=run_id, error=str(exc)[:300])
    finally:
        try:
            run = db.get(BackgroundRun, run_id)
            if run is not None:
                run.status = status
                run.steps = list(steps)
                run.answer = answer or None
                run.error = error
                if session_id:
                    run.cli_session_id = session_id
                run.finished_at = _now_iso()
                if foreground:
                    # A normal chat turn: plain assistant message (same shape
                    # the old in-request handler produced) + carry the CLI
                    # session forward on the thread for the next turn.
                    from models.thread import Thread
                    t = db.get(Thread, run.thread_id)
                    if t is not None:
                        if session_id:
                            t.cli_session_id = session_id
                        t.updated_at = _now_iso()
                    if status == "completed":
                        content = answer
                    elif status == "cancelled":
                        content = "[beurt geannuleerd]"
                    else:
                        content = f"[fout] {(error or 'onbekende fout')[:800]}"
                    msg = Message(id=str(uuid4()), thread_id=run.thread_id,
                                  role="assistant", content=content, steps=steps,
                                  created_at=_now_iso())
                else:
                    # CCC-style task notification: EVERY terminal outcome
                    # lands in the transcript with a recognizable prefix —
                    # a silently failed task visible only in the task list is
                    # exactly what this feature exists to prevent.
                    short = run.id[:8]
                    if status == "completed":
                        content = f"[Achtergrondtaak {short} afgerond] {answer}"
                    elif status == "cancelled":
                        content = f"[Achtergrondtaak {short} geannuleerd]"
                    else:
                        content = (f"[Achtergrondtaak {short} mislukt] "
                                   f"{(error or 'onbekende fout')[:800]}")
                    msg = Message(id=str(uuid4()), thread_id=run.thread_id,
                                  role="assistant", content=content,
                                  steps=steps if status == "completed" else [],
                                  created_at=_now_iso())
                db.add(msg)
                db.flush()
                run.message_id = msg.id
                db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warningx("achtergrondtaak-afronding mislukt", run_id=run_id, error=str(exc)[:300])
        finally:
            db.close()
        _publish(run_id, {"kind": "run_status", "status": status})


def active_foreground_run(db: Session, thread_id: str) -> Optional[BackgroundRun]:
    rows = (db.query(BackgroundRun)
            .filter(BackgroundRun.thread_id == thread_id,
                    BackgroundRun.mode == "foreground",
                    BackgroundRun.status == "running").all())
    for r in rows:
        if is_active(r.id):
            return r
    return None


def cancel(run_id: str) -> bool:
    task = _ACTIVE_TASKS.get(run_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def reconcile_on_start(db: Session) -> int:
    """A backend restart orphans any in-flight run's subprocess — the row
    must not claim 'running' forever. Same reasoning as
    LabService.reconcile_on_start."""
    rows = db.query(BackgroundRun).filter(BackgroundRun.status == "running").all()
    for r in rows:
        r.status = "interrupted"
        r.error = "Backend herstart tijdens de run"
        r.finished_at = _now_iso()
    if rows:
        db.commit()
    return len(rows)
