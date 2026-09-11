"""db/init_db.py — create tables on startup, plus tiny additive column
migrations for existing databases. No Alembic for this POC: `create_all`
only creates missing TABLES, it never alters an existing one, so a new
column on an existing model (like MCPServer.always_allowed) would silently
never appear on a database that already has that table — breaking every
already-configured server rather than just failing loudly. `_ensure_columns`
closes that specific gap with a plain `ALTER TABLE ... ADD COLUMN` per
missing column, which SQLite supports directly; this is deliberately not a
general migration framework, just enough to not lose existing data across a
POC iteration."""
from __future__ import annotations

from sqlalchemy import text

from component_logging import get_logger
from db.database import Base, engine

log = get_logger(__name__)

# table -> [(column_name, ddl_type_and_default), ...] for columns added after
# a table already existed in the wild. Remove an entry once comfortable that
# no deployed database predates it.
_ADDITIVE_COLUMNS = {
    "ticket_comments": [
        # Interne opmerkingen gaan nooit naar de bron; bestaande rijen waren
        # allemaal extern, dus 0 is de juiste standaard voor wat er al staat.
        ("internal", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "mcp_servers": [
        ("always_allowed", "BOOLEAN NOT NULL DEFAULT 0"),
        ("auth_config_encrypted", "TEXT"),
        ("azure_profile_id", "INTEGER"),
        ("usage_scope", "VARCHAR(16)"),
        ("sync_azure_profile_id", "INTEGER"),
        ("sync_auth_config_encrypted", "TEXT"),
    ],
    "app_settings": [
        ("auto_recall_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
        ("auto_recall_tool_name", "VARCHAR(255)"),
        ("auto_recall_query_template", "TEXT"),
        ("auto_recall_instruction", "TEXT"),
        ("default_effort", "VARCHAR(16)"),
        ("fallback_model", "VARCHAR(255)"),
        ("max_budget_usd", "FLOAT"),
        ("autocompact", "VARCHAR(32)"),
        ("custom_agents_json", "TEXT"),
        ("default_agent", "VARCHAR(128)"),
        ("auto_hooks", "TEXT"),
        ("admin_username", "VARCHAR(128)"),
        ("admin_password_hash", "VARCHAR(512)"),
    ],
    "lab_extras": [
        ("mcp_server", "TEXT"),
        ("builtin_hash", "VARCHAR(64)"),
    ],
    "lab_workers": [
        ("last_used_at", "VARCHAR(64)"),
    ],
    "ticket_plans": [
        ("resume_at", "VARCHAR(64)"),
        # Hoeveel tickets tegelijk. NULL = zoveel als er werkers vrij zijn, en
        # dat is precies hoe bestaande planningen zich moeten blijven gedragen
        # zolang hun lab één werker heeft.
        ("max_parallel", "INTEGER"),
        ("workspace_mode", "VARCHAR(16) NOT NULL DEFAULT 'gedeeld'"),
    ],
    "ticket_plan_items": [
        ("worker_id", "INTEGER"),
        # Dit ITEM ligt stil tot dit moment; de planning loopt door.
        ("resume_at", "VARCHAR(64)"),
        # En waaróm het stilligt, in de woorden van de agent.
        ("wait_reason", "TEXT"),
    ],
    "labs": [
        ("azure_profile_id", "INTEGER"),
        ("extras", "TEXT"),
        ("setup_script", "TEXT"),
        ("provision_status", "VARCHAR(16)"),
        ("provision_log", "TEXT"),
        ("worker_count", "INTEGER NOT NULL DEFAULT 1"),
        ("min_workers", "INTEGER NOT NULL DEFAULT 1"),
        ("max_workers", "INTEGER NOT NULL DEFAULT 1"),
    ],
    "schedules": [
        ("json_schema", "TEXT"),
        ("kind", "VARCHAR(16) NOT NULL DEFAULT 'prompt'"),
        ("board_id", "INTEGER"),
        ("board_column", "VARCHAR(64)"),
        ("board_max_tickets", "INTEGER NOT NULL DEFAULT 1"),
    ],
    "threads": [
        ("model", "VARCHAR(128)"),
        ("effort", "VARCHAR(16)"),
        ("source", "VARCHAR(16) NOT NULL DEFAULT 'chat'"),
    ],
    "boards": [
        # Kolom waar een ticket heen gaat zodra de agent eraan begint.
        ("agent_busy_column", "VARCHAR(64)"),
    ],
    "tickets": [
        ("acceptance_criteria", "TEXT"),
        ("depends_on", "TEXT"),
        # Wat de bron als laatste zei; het ijkpunt voor "welk veld is hier
        # veranderd". NULL voor bestaande rijen: de eerste sync vult hem, en
        # tot dat moment gedraagt de push zich als vroeger (alles mee).
        ("external_snapshot", "TEXT"),
    ],
    "background_runs": [
        ("mode", "VARCHAR(16) NOT NULL DEFAULT 'background'"),
        # In welke werker (container) van het lab deze run draaide.
        ("lab_worker_id", "INTEGER"),
        # Gepauzeerd op een gebruikslimiet tot dit moment.
        ("resume_at", "VARCHAR(64)"),
    ],
}


def _ensure_columns() -> set[tuple[str, str]]:
    """Voegt ontbrekende kolommen toe en geeft terug WELKE er zijn toegevoegd,
    zodat een backfill alleen draait op een kolom die net is ontstaan."""
    added: set[tuple[str, str]] = set()
    with engine.connect() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in columns:
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                added.add((table, name))
                log.info(f"migration: added {table}.{name}")
        conn.commit()
    return added


# Eenmalige correcties die NA het toevoegen van een kolom moeten draaien.
# `kind` krijgt door de ALTER TABLE de default 'prompt' — ook voor schedules
# die al een workflow uitvoerden. Zonder deze backfill zou zo'n bestaande
# schedule ineens als prompt-schedule draaien (en dus niets doen). Gekoppeld
# aan het TOEVOEGEN van de kolom, niet aan elke start: wie later bewust een
# workflow-schedule omzet naar een prompt mag daar niet in teruggedraaid worden.
_BACKFILLS = [
    ("schedules", "kind",
     "UPDATE schedules SET kind = 'workflow' WHERE workflow_id IS NOT NULL AND kind = 'prompt'"),
    # Threads die al bij een ticket-agentrun hoorden: zonder deze correctie
    # blijven ze na de upgrade in de chatlijst staan, terwijl board-werk daar
    # juist uit hoort.
    ("threads", "source",
     "UPDATE threads SET source = 'board' WHERE id IN "
     "(SELECT agent_thread_id FROM tickets WHERE agent_thread_id IS NOT NULL)"),
    # Bestaande borden hebben bijna allemaal een kolom die "bezig" betekent.
    # Zonder deze backfill blijft de nieuwe instelling leeg en verandert er
    # voor precies die borden niets, terwijl dit juist de borden zijn waar het
    # probleem zich voordeed. Alleen invullen waar de kolom ook echt bestaat.
    ("boards", "agent_busy_column",
     "UPDATE boards SET agent_busy_column = 'in_progress' "
     "WHERE agent_busy_column IS NULL AND columns LIKE '%\"in_progress\"%'"),
]


def _run_backfills(added: set[tuple[str, str]]) -> None:
    with engine.connect() as conn:
        for table, column, sql in _BACKFILLS:
            if (table, column) not in added:
                continue
            result = conn.execute(text(sql))
            log.info(f"migration backfill: {table}.{column} ({result.rowcount} rijen)")
        conn.commit()


def init_db() -> None:
    # Import every model module so its table registers on Base.metadata before
    # create_all runs — SQLAlchemy only knows about mapped classes it has seen.
    from models import (  # noqa: F401
        audit,
        azure_profile,
        background_run,
        board,
        lab,
        lab_extra,
        lab_worker,
        mcp_server,
        message,
        notification,
        plan,
        schedule,
        skill,
        skill_tool,
        thread,
        tool,
        workflow,
    )

    Base.metadata.create_all(bind=engine)
    _run_backfills(_ensure_columns())
    log.info("db_initialised")
