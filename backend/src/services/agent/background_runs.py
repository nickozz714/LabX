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
from services.agent.limiet import SessieLimiet

log = get_logger(__name__)

# Strong references so tasks aren't garbage-collected mid-flight (a bare
# unreferenced asyncio.Task can be GC'd, per Python's own docs) and so
# cancel() can find them.
_ACTIVE_TASKS: Dict[str, asyncio.Task] = {}
_SUBSCRIBERS: Dict[str, List[asyncio.Queue]] = {}
# Afloop-hooks per run: callables die één keer draaien zodra de run een
# eindstatus bereikt. Bedoeld voor werk dat AAN de run hangt maar er niet in
# thuishoort — een board-ticket bijwerken nadat de agent hem heeft opgepakt.
# Ze krijgen een EIGEN db-sessie (zie _run_finish_hooks): de sessie van de run
# is op dat moment al aan het afsluiten.
_FINISH_HOOKS: Dict[str, List[Any]] = {}

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
        # Alleen gevuld bij status "limited": vanaf dit moment gaat het werk
        # vanzelf verder. Zonder dit ziet een gepauzeerde run eruit als een
        # afgebroken run.
        "resume_at": getattr(r, "resume_at", None),
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


def on_finish(run_id: str, hook) -> None:
    """Registreer een callback `hook(db, run)` die draait zodra deze run een
    eindstatus heeft. Een falende hook mag de run nooit alsnog laten stuk
    lopen — hij is een gevolg van de run, geen onderdeel ervan."""
    _FINISH_HOOKS.setdefault(run_id, []).append(hook)


def _run_finish_hooks(run_id: str) -> None:
    hooks = _FINISH_HOOKS.pop(run_id, None)
    if not hooks:
        return
    from db.database import SessionLocal
    db = SessionLocal()
    try:
        run = db.get(BackgroundRun, run_id)
        for hook in hooks:
            try:
                hook(db, run)
            except Exception as exc:  # noqa: BLE001
                log.warningx("afloop-hook mislukt", run_id=run_id, error=str(exc)[:300])
    finally:
        db.close()


def start(db: Session, *, thread_id: str, lab_id: str,
          history: List[Dict[str, str]], prompt: str,
          model: Optional[str] = None, effort: Optional[str] = None,
          mode: str = "background",
          resume_session_id: Optional[str] = None,
          pre_subscribe: bool = False,
          lab_worker_id: Optional[int] = None):
    """Create + fire a run. mode="foreground" is a normal chat turn: it
    resumes the thread's CLI session, keeps the task tools available, and
    persists a plain assistant message. pre_subscribe=True returns
    (run, queue) with the queue registered BEFORE the task starts, so the
    caller's live stream can't miss the first events."""
    now = _now_iso()
    run = BackgroundRun(id=str(uuid4()), thread_id=thread_id, prompt=prompt,
                        model=model, effort=effort, status="running", mode=mode,
                        # De werker waarin deze run werkt: alles wat hij in het
                        # lab doet moet in díe container landen, niet in die van
                        # een run waar hij niets mee te maken heeft.
                        lab_worker_id=lab_worker_id,
                        steps=[], created_at=now, started_at=now)
    db.add(run)
    db.commit()
    db.refresh(run)

    q = subscribe(run.id) if pre_subscribe else None
    task = asyncio.get_running_loop().create_task(
        _execute(run.id, lab_id=lab_id, history=history, model=model, effort=effort,
                 mode=mode, resume_session_id=resume_session_id,
                 lab_worker_id=lab_worker_id))
    _ACTIVE_TASKS[run.id] = task
    task.add_done_callback(lambda _t: _ACTIVE_TASKS.pop(run.id, None))
    return (run, q) if pre_subscribe else run


async def _execute(run_id: str, *, lab_id: str, history: List[Dict[str, str]],
                   model: Optional[str], effort: Optional[str],
                   mode: str = "background",
                   resume_session_id: Optional[str] = None,
                   lab_worker_id: Optional[int] = None) -> None:
    from db.database import SessionLocal
    from models.message import Message
    from services.agent.chat_agent import ChatAgent

    db = SessionLocal()
    steps: List[Dict[str, Any]] = []
    answer: str = ""
    session_id: Optional[str] = None
    status = "completed"
    error: Optional[str] = None
    resume_at: Optional[str] = None
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
            lab_worker_id=lab_worker_id,
        ):
            kind = ev.get("kind")
            if kind == "session":
                session_id = ev.get("id")
            elif kind == "answer":
                answer = ev.get("text") or ""
                # Het eindantwoord kwam ook als laatste tussentekst voorbij;
                # die eruit halen, anders staat hij twee keer in het verslag.
                while steps and steps[-1].get("kind") == "thinking" \
                        and (steps[-1].get("text") or "").strip() == answer.strip():
                    steps.pop()
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
    except SessieLimiet as limiet:
        # GEEN fout. Er is niets stuk; de dienst laat ons even niet werken.
        # De run krijgt een eigen toestand en een tijdstip, zodat hij straks
        # hervat kan worden in plaats van als mislukt te blijven staan.
        status = "limited"
        error = limiet.leesbaar()[:2000]
        resume_at = limiet.resets_at.isoformat()
        log.infox("Run gepauzeerd op een gebruikslimiet", run_id=run_id,
                  hervat_om=resume_at)
    except Exception as exc:  # noqa: BLE001 — a background run must record, not raise
        status = "failed"
        # `str()` van een uitzondering kan LEEG zijn — `TimeoutError()` is het
        # bekendste geval. Zo eindigde een run van twee uur als "failed" met
        # niets erbij, en dan is er geen beginnen aan uitzoeken wat er gebeurd
        # is. De soort erbij is het minste wat er altijd staat.
        error = (str(exc) or f"{type(exc).__name__} zonder toelichting")[:2000]
        log.warningx("achtergrondtaak mislukt", run_id=run_id,
                     soort=type(exc).__name__, error=error[:300])
    finally:
        try:
            run = db.get(BackgroundRun, run_id)
            if run is not None:
                # De melder: heeft deze run een eigen CLI-tool gebruikt die wij
                # niet toestaan? Dan staat hij niet op de denylist — en die
                # loopt per definitie achter op de CLI. Hier zichtbaar maken is
                # het verschil tussen "we ontdekken het bij de volgende
                # release" en "het draait maanden stil mee".
                from services.agent.claude_cli_provider import onverwachte_native_tools
                vreemd = onverwachte_native_tools(steps)
                if vreemd:
                    log.warningx("Run gebruikte CLI-tools die niet zijn toegestaan",
                                 run_id=run_id, tools=", ".join(vreemd))
                run.status = status
                run.steps = list(steps)
                run.answer = answer or None
                run.error = error
                run.resume_at = resume_at
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
        _geef_resources_vrij(run_id)
        _run_finish_hooks(run_id)
        _meld_achtergrondtaak(run_id, mode, status, answer, error)
        _publish(run_id, {"kind": "run_status", "status": status})


def _geef_resources_vrij(run_id: str) -> None:
    """Wat deze sessie in het lab had gereserveerd, gaat los zodra de beurt om is.

    Een claim hoort bij het WERK, niet bij het gesprek: houdt een chat de
    browser vast tussen twee berichten door, dan staat de collega in dezelfde
    container voor niets te wachten. De houdbaarheid op de claim is het
    vangnet; dit is het normale pad.
    """
    from db.database import SessionLocal
    from models.thread import Thread

    db = SessionLocal()
    try:
        run = db.get(BackgroundRun, run_id)
        if run is None or not run.thread_id:
            return
        t = db.get(Thread, run.thread_id)
        if t is None or not t.lab_id:
            return
        from services.lab.resources import ResourceService
        ResourceService(db).release(lab_id=t.lab_id, houder=str(run.thread_id),
                                    reden="beurt afgelopen")
    except Exception as exc:  # noqa: BLE001
        log.warningx("Resources vrijgeven na een run mislukt", run_id=run_id,
                     error=str(exc)[:200])
    finally:
        db.close()


def _meld_achtergrondtaak(run_id: str, mode: str, status: str,
                          answer: Optional[str], error: Optional[str]) -> None:
    """Melden dat een ACHTERGRONDtaak klaar is.

    Alleen achtergrondtaken: een gewone chatbeurt kijk je zelf aan, en daar een
    duwtje op je telefoon voor krijgen is ruis. Een ticket-run meldt zichzelf al
    via zijn eigen afloop-hook (agent_work), met de ticketsleutel erbij — die
    slaan we hier over om niet twee berichten voor hetzelfde werk te sturen.
    """
    if mode != "background" or status == "cancelled":
        return
    from db.database import SessionLocal
    from models.thread import Thread

    db = SessionLocal()
    try:
        run = db.get(BackgroundRun, run_id)
        if run is None:
            return
        thread = db.get(Thread, run.thread_id)
        if thread is not None and getattr(thread, "source", "chat") == "board":
            return  # het ticket meldt dit zelf, met meer context
        from services.notify.notify_service import meld
        titel = (thread.title if thread else None) or "Achtergrondtaak"
        context = {"thread_id": run.thread_id, "run_id": run.id,
                   "lab_id": thread.lab_id if thread else None}
        if status == "completed":
            meld("run_klaar", f"Klaar: {titel}"[:200],
                 (answer or "").strip() or "Geen samenvatting.", context)
        else:
            meld("run_mislukt", f"Mislukt: {titel}"[:200],
                 (error or f"Run eindigde als '{status}'.")[:2000], context)
    except Exception as exc:  # noqa: BLE001 — melden mag nooit de run alsnog laten vallen
        log.warningx("Melding over achtergrondtaak mislukt", run_id=run_id,
                     error=str(exc)[:300])
    finally:
        db.close()


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


def ruim_dode_runs_op(db: Session, *, respijt_seconden: int = 60) -> int:
    """Runs die "running" heten terwijl er niets meer draait.

    Een run hangt aan een asyncio-taak in dit proces. Sneuvelt die taak zonder
    de rij af te sluiten — een event loop die sluit, een exception op een plek
    waar niemand hem vangt — dan blijft de rij voorgoed op "running" staan. En
    daarmee blijft het TICKET op "running" staan: de knop "Agent starten" is
    uitgeschakeld, de agent doet niets, en er is geen weg terug.

    Dit bestond wel voor planningen (`PlanService._ruim_dode_runs_op`) maar niet
    voor een handmatig gestart ticket — dezelfde blinde vlek als bij het kiezen
    van een werker. Op 13-09-2026 stonden er drie zulke runs: 23 minuten
    "running", nul stappen, containers op 0,09% CPU.

    Het respijt is er voor de seconde tussen het aanmaken van de rij en het
    registreren van de taak; zonder dat zou deze opruiming een run kunnen
    afschieten die net begint.
    """
    from datetime import datetime, timedelta, timezone

    grens = (datetime.now(timezone.utc) - timedelta(seconds=max(10, respijt_seconden))).isoformat()
    opgeruimd = 0
    for r in db.query(BackgroundRun).filter(BackgroundRun.status == "running").all():
        if (r.started_at or "") > grens:
            continue          # net begonnen; de taak kan nog geregistreerd worden
        if is_active(r.id):
            continue
        r.status = "interrupted"
        r.error = "De run is verdwenen zonder af te ronden"
        r.finished_at = _now_iso()
        opgeruimd += 1
        log.warningx("Dode run opgeruimd", run_id=r.id[:8], gestart=r.started_at)
    if opgeruimd:
        db.commit()
        # Het ticket moet ook los, anders blijft het op "de agent werkt eraan"
        # staan terwijl er niets werkt.
        from services.boards.agent_work import geef_tickets_vrij
        geef_tickets_vrij(db)
    return opgeruimd


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
