# models/setting.py
#
# Singleton row (id=1) holding the operator-configurable settings the plan's
# Settings page covers: the Claude Code CLI ("zoals de Claude Code CLI") and
# guard/lab defaults. Env vars in config.py are the fallback until this row
# is edited — see services/settings_service.py for the merge.
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Account (first-time setup in de UI). When set, these WIN over the
    # LABX_ADMIN_USERNAME/PASSWORD env vars — env stays as bootstrap/fallback
    # for existing deployments. Hash format: see authentication.hash_password.
    admin_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    admin_password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Claude Code CLI
    cli_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Fernet-encrypted; never returned by the API (write-only, like an Azure profile secret).
    oauth_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    max_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_args: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enable_tool_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Claude Code CLI feature-parity additions (`--effort`, `--fallback-model`,
    # `--max-budget-usd`, `--autocompact`) — see claude_cli_provider.py
    # ClaudeCliProvider._build_cmd for how each becomes a flag.
    # Na hoeveel dagen zonder activiteit een chat vanzelf het archief in gaat.
    # 0 (of leeg) zet het uit. Standaard 3 — zie services/chat/archief.py.
    chat_archive_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Onder welke namen JIJ in de bron staat (Jira, Azure DevOps). De
    # urenregistratie schat je eigen tijd uit de opmerkingen die je schreef, en
    # moet daarvoor weten welke dat zijn. Zonder deze lijst telde elke
    # opmerking mee die niet van de agent kwam — dus ook die van collega's en
    # klantmedewerkers, wat de uren van de ene klant opblies en die van de
    # andere liet verdampen. Leeg = alleen wat je in LabX zelf schreef.
    eigen_auteurs: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # --- spraakassistent -----------------------------------------------------
    # De hele functie hangt aan `voice_enabled`: staat die uit, dan is het
    # tabblad onzichtbaar en bestaan de endpoints voor de interface niet.
    # Versleuteld, net als het oauth-token: nooit terug via de API.
    openai_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "realtime" (spraakmodel van OpenAI doet intentie én stem) of "pipeline"
    # (transcriberen, Claude als brein via de bestaande CLI, lokaal voorlezen).
    voice_brein: Mapped[str] = mapped_column(String(16), nullable=False, default="pipeline")
    voice_microfoon: Mapped[str] = mapped_column(String(16), nullable=False, default="ptt")
    # Hoe je een schrijfactie bevestigt: "klik", "spraak" of "beide".
    voice_bevestiging: Mapped[str] = mapped_column(String(16), nullable=False, default="beide")
    # Het woord dat bevestigt. Bewust NIET "ja": een omstander die toevallig
    # ja zegt moet niets kunnen. Moet het hele antwoord zijn, niet een woord
    # erin — zie services/voice/bevestiging.py.
    voice_woord: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Uit zichzelf melden dat een run klaar is. Standaard UIT: anders word je
    # onderbroken door werk waar je niet naar vroeg.
    voice_meld_runs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Kostenbeheersing. 0 = geen limiet.
    voice_sessie_minuten: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    voice_dag_limiet_usd: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    # Modelkeuzes per onderdeel, zodat je kunt schuiven zonder code.
    voice_realtime_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    voice_stt_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    voice_brein_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    default_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    autocompact: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Subagents (`--agents`/`--agent`): a raw-JSON escape hatch, same
    # precedent as extra_args, rather than a dedicated CRUD subsystem.
    custom_agents_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_agent: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Guard defaults for new labs
    data_guard_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    llm_guard_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    guard_llm_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    guard_llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Lab defaults
    default_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_ttl_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Auto-recall "hook": run one tool automatically before every chat turn
    # and inject its result into context — the LabX equivalent of a Claude
    # Code CLI UserPromptSubmit hook that calls hive_recall (see Nectar's own
    # recall hook, which this mirrors). Deliberately simple: one tool, one
    # query template, called directly (not through the CLI's own tool-search
    # loop) so it always runs regardless of what the model decides to do.
    auto_recall_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_recall_tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auto_recall_query_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_recall_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Multi-hook successor of the single auto_recall_* fields above: a JSON
    # list [{tool_name, query_template, instruction, enabled}]. When set,
    # this wins; the legacy single-hook fields remain as fallback for
    # databases that predate it (see settings_service.ResolvedSettings).
    auto_hooks: Mapped[list | None] = mapped_column(JSON, nullable=True)

    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
