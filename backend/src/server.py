"""
server.py — LabX FastAPI entrypoint.

Wires up: DB init, the lab TTL-reaper + reconcile-on-start (the sibling daemon
outlives the LabX process, see LabService.reconcile_on_start), and every
router. New routers from later phases (chat, skills, tools, mcp, workflows,
schedules, azure-profiles) register themselves the same way — see the
`# --- fase N ---` markers below.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from component_logging import get_logger
from config import settings
from db.database import SessionLocal
from db.init_db import init_db
from services.lab.lab_service import LabService
from services.scheduling.scheduler import DynamicScheduler

log = get_logger(__name__)

scheduler = DynamicScheduler(tick_seconds=30)


async def _lab_reaper_tick() -> None:
    db = SessionLocal()
    try:
        await LabService(db).expire_due()
    finally:
        db.close()


async def _warm_mcp_gateway() -> None:
    """Importeer de gateway in een apart proces zodat de bestandscache warm is
    tegen de tijd dat een echte run hem start. Faalt dit, dan is er niets aan de
    hand — het was alleen een voorsprong."""
    import sys
    src_root = os.path.dirname(os.path.abspath(__file__))
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import fastmcp, services.mcp.gateway",
            env={**os.environ, "PYTHONPATH": src_root},
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=180)
        log.infox("MCP-gateway voorverwarmd", exit_code=proc.returncode)
    except Exception as exc:  # noqa: BLE001
        log.warningx("MCP-gateway voorverwarmen overgeslagen", error=str(exc)[:200])


async def _plan_tick() -> None:
    """Planningen die aan de beurt zijn losmaken, en lopende planningen die
    stilvielen weer aanschoppen (zie services/boards/plan_service.tick)."""
    from services.boards.plan_service import tick as _tick
    await _tick()


async def _guard_audit_opruimen() -> None:
    """Oude guard-auditregels weggooien. Dat spoor bevat per definitie precies
    de gegevens die de guard tegenhield; zonder opruimen groeit er een archief
    van klantgegevens dat niemand meer beheert."""
    from services.lab.guard_audit_service import ruim_op

    db = SessionLocal()
    try:
        ruim_op(db)
    finally:
        db.close()


async def _hervat_limiet_tick() -> None:
    """Werk dat op een gebruikslimiet stilviel weer oppakken zodra de limiet
    opengaat. Alleen voor runs die NIET in een planning zitten; die hervat de
    planning zelf (zie services/agent/hervatten.py)."""
    from services.agent.hervatten import tick as _tick
    await _tick()


async def _notify_inbox_tick() -> None:
    """Antwoorden op meldingen ophalen.

    HALEN en niet ontvangen: deze opstelling staat op een prive server zonder
    domein en zonder open poort, dus een webhook kan hier niet aankomen.
    Telegram (getUpdates) en mail (IMAP) laten zich prima bevragen, en dat is
    hier de robuustere kant — niets dat stukgaat als het IP verandert.

    Elke 30 seconden: snel genoeg dat een antwoord op je telefoon voelt als een
    gesprek, rustig genoeg om geen enkele API-limiet te raken.
    """
    from services.notify.notify_service import tick as _tick
    await _tick()


async def _worker_reaper_tick() -> None:
    """Extra werkers die een tijd niets deden weer opruimen (de andere kant van
    de autoscaler). Nooit onder de ondergrens van het lab."""
    db = SessionLocal()
    try:
        await LabService(db).reap_idle_workers()
    finally:
        db.close()


async def _mcp_session_tick() -> None:
    """Blijvende MCP-processen in labs die niemand meer gebruikt afsluiten —
    een browser die staat te wachten hoeft geen geheugen te bezetten."""
    from services.mcp.lab_session_pool import close_idle
    await close_idle()


async def _board_sync_tick() -> None:
    """Boards met auto_sync_minutes > 0 bijwerken vanuit hun bron (DevOps/Jira)."""
    from services.boards.sync_service import BoardSyncService
    db = SessionLocal()
    try:
        await BoardSyncService(db).tick()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()

    db = SessionLocal()
    try:
        # De meegeleverde lab-extra's (Playwright + Chromium, Node, ...) als rij
        # neerzetten zodra ze ontbreken; bestaande, aangepaste rijen blijven.
        from services.lab.extras_catalog import seed_builtin_extras
        seed_builtin_extras(db)
        # De meegeleverde guard-regels. Aanpasbaar door de gebruiker; een
        # update werkt alleen regels bij die niemand zelf heeft gewijzigd.
        from services.lab.classifier import seed_standaardregels
        seed_standaardregels(db)
        fixed = await LabService(db).reconcile_on_start()
        if fixed:
            log.infox("Labs gereconcilieerd bij opstart", fixed=fixed)
        from services.agent.background_runs import reconcile_on_start as _bg_reconcile
        interrupted = _bg_reconcile(db)
        if interrupted:
            log.infox("Achtergrondtaken als 'interrupted' gemarkeerd na herstart", count=interrupted)
        # Tickets waarvan de agent-run door de herstart is weggevallen: anders
        # blijven ze eeuwig op "running" staan en kan niemand ze oppakken.
        from services.boards.agent_work import reconcile_on_start as _ticket_reconcile
        stale_tickets = _ticket_reconcile(db)
        if stale_tickets:
            log.infox("Ticket-agentruns teruggezet na herstart", count=stale_tickets)
    finally:
        db.close()

    scheduler.register(
        name="lab_reaper", interval_seconds=settings.LAB_REAPER_INTERVAL_SECONDS,
        fn=_lab_reaper_tick, run_immediately=False,
    )
    from services.scheduling.cron import tick as _cron_tick
    scheduler.register(name="schedule_cron", interval_seconds=30, fn=_cron_tick, run_immediately=True)
    # Elke 20s: fijner dan de cron, want een planning die op een bezet lab
    # wacht moet er kort na het vrijkomen in kunnen.
    scheduler.register(name="ticket_plans", interval_seconds=20, fn=_plan_tick,
                       run_immediately=True)
    scheduler.register(name="board_sync", interval_seconds=60, fn=_board_sync_tick,
                       run_immediately=False)
    scheduler.register(name="mcp_lab_sessions", interval_seconds=300, fn=_mcp_session_tick,
                       run_immediately=False)
    scheduler.register(name="notify_inbox", interval_seconds=30, fn=_notify_inbox_tick,
                       run_immediately=False)
    # Elke minuut: fijn genoeg dat er na een limiet niet onnodig lang niets
    # gebeurt, en het kost niets zolang er geen gepauzeerde runs staan.
    scheduler.register(name="hervat_limiet", interval_seconds=60, fn=_hervat_limiet_tick,
                       run_immediately=True)
    # Eens per uur is ruim voldoende voor een bewaartermijn in dagen.
    scheduler.register(name="guard_audit_opruimen", interval_seconds=3600,
                       fn=_guard_audit_opruimen, run_immediately=True)
    scheduler.register(name="worker_reaper", interval_seconds=300, fn=_worker_reaper_tick,
                       run_immediately=False)
    await scheduler.start()
    # De MCP-gateway alvast één keer laten importeren. De CLI start hem per run
    # als eigen proces, en dat proces moet de halve backend van schijf lezen:
    # koud gemeten op de server duurde die handshake 25s, tegen ~2s warm — met
    # de standaard 30s van de CLI is dat een gok. Deze opwarming kost niets
    # (best-effort, op de achtergrond) en haalt de eerste run uit de gevarenzone.
    asyncio.create_task(_warm_mcp_gateway())
    log.infox("LabX gestart")
    yield
    await scheduler.stop()


app = FastAPI(title="LabX", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from routers import (  # noqa: E402
    auth_router, system_router, lab_router, chat_router, internal_router, settings_router,
    skill_router, tool_router, mcp_router, workflow_router, schedule_router, azure_profile_router,
    board_router, notify_router, guard_router,
)

app.include_router(auth_router.router, prefix="/api")
app.include_router(system_router.router, prefix="/api")
app.include_router(lab_router.router, prefix="/api")
app.include_router(lab_router.ws_router, prefix="/api")
# De zichtbare browser van een lab: eigen router, want een <iframe> en een
# WebSocket kunnen geen Authorization-header meesturen (zie lab_router).
app.include_router(lab_router.browser_router, prefix="/api")
app.include_router(chat_router.router, prefix="/api")
app.include_router(internal_router.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(skill_router.router, prefix="/api")
app.include_router(tool_router.router, prefix="/api")
app.include_router(mcp_router.router, prefix="/api")
app.include_router(workflow_router.router, prefix="/api")
app.include_router(schedule_router.router, prefix="/api")
app.include_router(azure_profile_router.router, prefix="/api")
app.include_router(board_router.router, prefix="/api")
app.include_router(notify_router.router, prefix="/api")
app.include_router(guard_router.router, prefix="/api")
