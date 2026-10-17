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
    "clusters": [
        # Welke geheimen dit cluster nodig heeft voordat een branch-lab kan
        # draaien -- de tweede lijst van de verkenner. Dat rijtje is geen
        # restpost: het is precies waar een branch-lab veertig minuten later
        # op omvalt als niemand het vooraf opschreef.
        ("benodigde_geheimen", "TEXT"),
        # Waarop de applicatie in een branch-lab luistert. Bestaande clusters
        # hebben dit niet en vallen terug op de oude standaard.
        ("lab_poorten", "TEXT"),
        # De deploy key van dit cluster. Erbij gekomen omdat een organisatie
        # met goedkeuringsbeleid een token kan tegenhouden, en een deploy key
        # op repo-niveau daarbuiten valt.
        ("proef_status", "VARCHAR(20) NOT NULL DEFAULT 'geen'"),
        ("proef_lab_id", "VARCHAR(64)"),
        ("proef_log", "TEXT"),
        ("lab_cpu", "REAL"),
        ("lab_mem_mb", "INTEGER"),
        # Standaard 0: een branch-lab krijgt de Azure CLI niet, ook al zet
        # deze installatie hem in elk gewoon lab.
        ("lab_azure", "BOOLEAN NOT NULL DEFAULT 0"),
        ("ssh_publiek", "TEXT"),
        ("ssh_prive_encrypted", "TEXT"),
    ],
    "cluster_plannen": [
        # Antwoorden op de vragen van de verkenner, en het plan waar dit uit
        # voortkwam. Erbij gekomen nadat bleek dat een plan wel vragen stelde
        # maar niemand ze kon beantwoorden.
        ("antwoorden", "TEXT"),
        ("token_geheim", "VARCHAR(120)"),
        # Terugkoppeling op het plan en op losse onderdelen. Erbij gekomen
        # nadat bleek dat antwoorden alleen kon op vragen die het model
        # toevallig stelde.
        ("opmerkingen", "TEXT"),
        ("instructie", "TEXT"),
        ("opmerking", "TEXT"),
        ("vorig_plan_id", "INTEGER"),
    ],
    "cluster_diensten": [
        # Waar een dienst van buiten te bereiken is: {containerpoort:
        # machinepoort}. Erbij gekomen nadat bleek dat de containerpoort zelf
        # publiceren botst met wat er al op de machine luistert.
        ("poort_map", "TEXT"),
    ],
    "lab_hosts": [
        # Hoeveel modelcontainers hier mogen draaien. 0 voor wat er al staat:
        # een bestaande machine doet alleen labs tot jij er iets anders van
        # vindt.
        ("ollama_runners", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "ticket_comments": [
        # Interne opmerkingen gaan nooit naar de bron; bestaande rijen waren
        # allemaal extern, dus 0 is de juiste standaard voor wat er al staat.
        ("internal", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "mcp_servers": [
        ("stuur_sessie", "BOOLEAN NOT NULL DEFAULT 0"),
        ("always_allowed", "BOOLEAN NOT NULL DEFAULT 0"),
        ("auth_config_encrypted", "TEXT"),
        ("azure_profile_id", "INTEGER"),
        ("usage_scope", "VARCHAR(16)"),
        ("sync_azure_profile_id", "INTEGER"),
        ("sync_auth_config_encrypted", "TEXT"),
        ("token_scope", "VARCHAR(255)"),
    ],
    "app_settings": [
        ("chat_archive_days", "INTEGER"),
        # Onder welke namen je in Jira/DevOps staat, voor de urenschatting.
        ("eigen_auteurs", "JSON"),
        # Spraakassistent. Alles standaard uit/leeg: een bestaande installatie
        # merkt niets tot je hem zelf aanzet.
        ("openai_key_encrypted", "TEXT"),
        ("voice_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
        ("voice_brein", "VARCHAR(16) NOT NULL DEFAULT 'pipeline'"),
        ("voice_microfoon", "VARCHAR(16) NOT NULL DEFAULT 'ptt'"),
        ("voice_bevestiging", "VARCHAR(16) NOT NULL DEFAULT 'beide'"),
        ("voice_woord", "VARCHAR(64)"),
        ("voice_meld_runs", "BOOLEAN NOT NULL DEFAULT 0"),
        ("voice_sessie_minuten", "INTEGER NOT NULL DEFAULT 30"),
        ("voice_dag_limiet_usd", "FLOAT NOT NULL DEFAULT 5.0"),
        ("voice_realtime_model", "VARCHAR(128)"),
        ("voice_stt_model", "VARCHAR(128)"),
        ("voice_brein_model", "VARCHAR(128)"),
        ("voice_tts", "VARCHAR(16)"),
        ("voice_tts_stem", "VARCHAR(32)"),
        ("voice_tts_model", "VARCHAR(128)"),
        ("fish_key_encrypted", "VARCHAR(512)"),
        ("voice_stt", "VARCHAR(16)"),
        ("voice_fish_stem", "VARCHAR(128)"),
        ("voice_fish_tts_model", "VARCHAR(64)"),
        ("voice_fish_stt_model", "VARCHAR(64)"),
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
    "skills": [
        # NULL = "beide": een bestaande skill geldt overal, net als voorheen.
        ("usage_scope", "VARCHAR(16)"),
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
        # NULL = eigen bundel; bestaande planningen draaien dus onveranderd.
        ("bundel", "INTEGER"),
        # Dit ITEM ligt stil tot dit moment; de planning loopt door.
        ("resume_at", "VARCHAR(64)"),
        # En waaróm het stilligt, in de woorden van de agent.
        ("wait_reason", "TEXT"),
    ],
    "labs": [
        # Bij welk cluster en welke branch dit lab hoort. NULL voor alles
        # wat er al staat: een bestaand lab is een gewoon lab.
        ("cluster_id", "INTEGER"),
        ("branch", "VARCHAR(255)"),
        ("poort_map", "TEXT"),
        ("chat_deelt_werker", "BOOLEAN NOT NULL DEFAULT 1"),
        ("azure_profile_id", "INTEGER"),
        ("extras", "TEXT"),
        ("setup_script", "TEXT"),
        ("provision_status", "VARCHAR(16)"),
        ("provision_log", "TEXT"),
        ("worker_count", "INTEGER NOT NULL DEFAULT 1"),
        ("min_workers", "INTEGER NOT NULL DEFAULT 1"),
        # Hoeveel sessies tegelijk in één werker. 1 = zoals het altijd was.
        ("sessies_per_werker", "INTEGER NOT NULL DEFAULT 1"),
        ("max_workers", "INTEGER NOT NULL DEFAULT 1"),
        ("security_profile", "VARCHAR(32) NOT NULL DEFAULT 'generiek'"),
        ("model", "VARCHAR(128)"),
        # NULL telt als "container": elk bestaand lab blijft wat het was.
        ("kind", "VARCHAR(16)"),
        # NULL = deze server, zoals elk bestaand lab.
        ("host_id", "INTEGER"),
        ("host_project_dir", "VARCHAR(1024)"),
        ("host_agent_token_encrypted", "TEXT"),
        # NULL = de meegeleverde standaard (zie services/lab/resources.py).
        ("claim_resources", "TEXT"),
    ],
    "schedules": [
        ("json_schema", "TEXT"),
        ("kind", "VARCHAR(16) NOT NULL DEFAULT 'prompt'"),
        ("board_id", "INTEGER"),
        ("board_column", "VARCHAR(64)"),
        ("board_max_tickets", "INTEGER NOT NULL DEFAULT 1"),
        # De parameterwaarden waarmee deze schedule zijn workflow start.
        ("parameters_json", "TEXT"),
    ],
    "threads": [
        ("model", "VARCHAR(128)"),
        ("effort", "VARCHAR(16)"),
        ("source", "VARCHAR(16) NOT NULL DEFAULT 'chat'"),
        # Uit de lijst, niet weg. Bestaande chats beginnen leeg (= actief); de
        # opruimtaak zet ze daarna vanzelf opzij als ze oud genoeg zijn.
        ("archived_at", "VARCHAR(64)"),
    ],
    "boards": [
        # Een bord kan bij een cluster horen. Eén bord per cluster; het
        # "labbord" is dit bord gefilterd op één branch, want een bord per
        # branch-lab zou verdwijnen zodra je dat wegwerplab opruimt.
        ("cluster_id", "INTEGER"),
        # Kolom waar een ticket heen gaat zodra de agent eraan begint.
        ("agent_busy_column", "VARCHAR(64)"),
        # Automatisch archiveren: in welke kolom, en na hoeveel dagen.
        ("archive_column", "VARCHAR(64)"),
        ("archive_days", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "tickets": [
        # Bij welke branch dit ticket hoort. De branch en niet het lab-id:
        # het lab komt en gaat, de branch blijft.
        ("branch", "VARCHAR(200)"),
        ("acceptance_criteria", "TEXT"),
        ("depends_on", "TEXT"),
        # Wat de bron als laatste zei; het ijkpunt voor "welk veld is hier
        # veranderd". NULL voor bestaande rijen: de eerste sync vult hem, en
        # tot dat moment gedraagt de push zich als vroeger (alles mee).
        ("external_snapshot", "TEXT"),
        # Het soort werkitem in de bron (Jira-issuetype / ADO work item type).
        ("item_type", "VARCHAR(64)"),
        # Bestaande tickets synchroniseerden al, dus 1 is de juiste standaard.
        ("sync_naar_bron", "BOOLEAN NOT NULL DEFAULT 1"),
        # Opzij gezet; leeg = gewoon op het bord.
        ("archived_at", "VARCHAR(64)"),
        # Geparkeerd met `board__wait_until` buiten een planning: wanneer de
        # agent hier weer mag beginnen, en waarop hij wachtte. Leeg voor elk
        # bestaand ticket, en dat is precies goed — die wachten nergens op.
        ("agent_resume_at", "VARCHAR(64)"),
        ("agent_wait_reason", "TEXT"),
        # Waar dit ticket bij hoort voor de urenregistratie. Het bord is de
        # KLANT; dit is het traject daarbinnen (DataPlatform, doelgroepenvervoer,
        # werk-en-inkomen). Leeg voor bestaande tickets — de agent vult hem in
        # en jij corrigeert.
        ("project", "VARCHAR(128)"),
    ],
    "guard_audit": [
        ("intent", "VARCHAR(32)"),
        ("intent_mismatch", "JSON"),
    ],
    "workflows": [
        # De graaf. Leeg voor een workflow van vóór deze versie; die wordt uit
        # zijn stappen afgeleid zodra hij bewerkt of uitgevoerd wordt.
        ("nodes_json", "TEXT"),
        ("edges_json", "TEXT"),
        # De invoerparameters. Leeg voor een bestaande workflow: die verwacht
        # geen invoer en blijft dus draaien zoals hij deed.
        ("parameters_json", "TEXT"),
    ],
    "workflow_run_steps": [
        # In welke werker deze activiteit draaide; nodig om een lopende bubbel
        # mee te tellen in de bezetting.
        ("worker_id", "INTEGER"),
    ],
    "workflow_runs": [
        ("worker_id", "INTEGER"),
        ("trigger_ref", "VARCHAR(64)"),
        ("thread_id", "VARCHAR(64)"),
        ("cli_session_id", "VARCHAR(128)"),
        ("input_json", "TEXT"),
        ("totals_json", "TEXT"),
        ("started_at", "VARCHAR(64)"),
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
        claim_resource,
        guard_audit,
        guard_rule,
        lab,
        lab_extra,
        cluster,
        cluster_secret,
        lab_host,
        model_runner,
        secret,
        lab_secret,
        lab_worker,
        mcp_server,
        message,
        notification,
        plan,
        schedule,
        skill,
        skill_tool,
        thread,
        time_entry,
        voice,
        tool,
        workflow,
    )

    Base.metadata.create_all(bind=engine)
    _run_backfills(_ensure_columns())
    log.info("db_initialised")
