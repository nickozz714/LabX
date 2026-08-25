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
    "mcp_servers": [
        ("always_allowed", "BOOLEAN NOT NULL DEFAULT 0"),
        ("auth_config_encrypted", "TEXT"),
        ("azure_profile_id", "INTEGER"),
        ("usage_scope", "VARCHAR(16)"),
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
    "labs": [
        ("azure_profile_id", "INTEGER"),
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
    "tickets": [
        ("acceptance_criteria", "TEXT"),
    ],
    "background_runs": [
        ("mode", "VARCHAR(16) NOT NULL DEFAULT 'background'"),
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
        mcp_server,
        message,
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
