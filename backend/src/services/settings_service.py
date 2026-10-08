"""services/settings_service.py — get/update the singleton AppSettings row,
merged over the config.py env defaults so the app works out of the box and
the Settings page only needs to override what the operator actually changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from config import settings as env
from models.setting import AppSettings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(db: Session) -> AppSettings:
    row = db.get(AppSettings, 1)
    if not row:
        row = AppSettings(id=1, updated_at=_now_iso())
        db.add(row)
        db.commit()
    return row


class ResolvedSettings:
    """Effective settings: DB override where set, else the env default."""

    def __init__(self, row: AppSettings):
        self.cli_path = row.cli_path or env.CLI_PATH
        self.oauth_token: Optional[str] = None  # decrypted lazily by the caller
        self.default_model = row.default_model or env.CLI_DEFAULT_MODEL
        self.max_turns = row.max_turns or env.CLI_MAX_TURNS
        self.timeout_seconds = row.timeout_seconds
        self.extra_args = list(row.extra_args or [])
        self.enable_tool_search = row.enable_tool_search if row.enable_tool_search is not None else env.CLI_ENABLE_TOOL_SEARCH
        self.data_guard_default = row.data_guard_default
        self.llm_guard_default = row.llm_guard_default
        self.guard_llm_url = row.guard_llm_url or env.DATA_GUARD_LLM_URL
        self.guard_llm_model = row.guard_llm_model or env.DATA_GUARD_LLM_MODEL
        self.default_image = row.default_image or env.LAB_DEFAULT_IMAGE
        self.default_ttl_hours = row.default_ttl_hours or 14
        self.auto_recall_enabled = row.auto_recall_enabled
        # Geen standaardnaam: welke tool dit is, hangt van je eigen opstelling af.
        self.auto_recall_tool_name = row.auto_recall_tool_name or ""
        self.auto_recall_query_template = row.auto_recall_query_template
        self.auto_recall_instruction = row.auto_recall_instruction
        # Effective hook list: the JSON list wins; fall back to the legacy
        # single-hook fields so an existing configured hook keeps working
        # without a migration step.
        if isinstance(row.auto_hooks, list):
            self.auto_hooks = row.auto_hooks
        elif row.auto_recall_enabled and (row.auto_recall_tool_name or "").strip():
            self.auto_hooks = [{
                "tool_name": row.auto_recall_tool_name.strip(),
                "query_template": row.auto_recall_query_template,
                "instruction": row.auto_recall_instruction,
                "enabled": True,
            }]
        else:
            self.auto_hooks = []
        self.chat_archive_days = row.chat_archive_days
        self.eigen_auteurs = getattr(row, "eigen_auteurs", None) or []
        # Spraakassistent. Deze moeten hier staan: de hele spraaklaag leest
        # zijn instellingen via get_settings(), en wat hier ontbreekt is daar
        # stilzwijgend de standaardwaarde -- inclusief "geen sleutel" en
        # "geen bevestigingswoord".
        self.openai_key_encrypted = getattr(row, "openai_key_encrypted", None)
        self.voice_enabled = bool(getattr(row, "voice_enabled", False))
        self.voice_brein = getattr(row, "voice_brein", None) or "pipeline"
        self.voice_microfoon = getattr(row, "voice_microfoon", None) or "ptt"
        self.voice_bevestiging = getattr(row, "voice_bevestiging", None) or "beide"
        self.voice_woord = getattr(row, "voice_woord", None)
        self.voice_meld_runs = bool(getattr(row, "voice_meld_runs", False))
        self.voice_sessie_minuten = getattr(row, "voice_sessie_minuten", None) or 30
        self.voice_dag_limiet_usd = getattr(row, "voice_dag_limiet_usd", None)
        if self.voice_dag_limiet_usd is None:
            self.voice_dag_limiet_usd = 5.0
        self.voice_realtime_model = getattr(row, "voice_realtime_model", None)
        self.voice_stt_model = getattr(row, "voice_stt_model", None)
        self.voice_brein_model = getattr(row, "voice_brein_model", None)
        self.voice_tts = getattr(row, "voice_tts", None) or "browser"
        self.voice_tts_stem = getattr(row, "voice_tts_stem", None)
        self.voice_tts_model = getattr(row, "voice_tts_model", None)
        self.fish_key_encrypted = getattr(row, "fish_key_encrypted", None)
        self.voice_stt = getattr(row, "voice_stt", None) or "openai"
        self.voice_fish_stem = getattr(row, "voice_fish_stem", None)
        self.voice_fish_tts_model = getattr(row, "voice_fish_tts_model", None)
        self.voice_fish_stt_model = getattr(row, "voice_fish_stt_model", None)
        self.default_effort = row.default_effort
        self.fallback_model = row.fallback_model
        self.max_budget_usd = row.max_budget_usd
        self.autocompact = row.autocompact
        self.custom_agents_json = row.custom_agents_json
        self.default_agent = row.default_agent


def get_settings(db: Session) -> ResolvedSettings:
    row = _row(db)
    resolved = ResolvedSettings(row)
    if row.oauth_token_encrypted:
        try:
            from utils.crypto import decrypt
            resolved.oauth_token = decrypt(row.oauth_token_encrypted)
        except Exception:  # noqa: BLE001 — bad/rotated key: fall back to env token
            resolved.oauth_token = env.CLAUDE_CODE_OAUTH_TOKEN or None
    else:
        resolved.oauth_token = env.CLAUDE_CODE_OAUTH_TOKEN or None
    return resolved


def get_public_settings(db: Session) -> Dict[str, Any]:
    """Same as get_settings but for the API response: no token value, just
    whether one is configured — same write-only pattern as Azure profiles."""
    row = _row(db)
    resolved = get_settings(db)
    return {
        "cli_path": resolved.cli_path,
        "oauth_token_configured": bool(row.oauth_token_encrypted or env.CLAUDE_CODE_OAUTH_TOKEN),
        "default_model": resolved.default_model,
        "max_turns": resolved.max_turns,
        "timeout_seconds": resolved.timeout_seconds,
        "extra_args": resolved.extra_args,
        "enable_tool_search": resolved.enable_tool_search,
        "data_guard_default": resolved.data_guard_default,
        "llm_guard_default": resolved.llm_guard_default,
        "guard_llm_url": resolved.guard_llm_url,
        "guard_llm_model": resolved.guard_llm_model,
        "default_image": resolved.default_image,
        "default_ttl_hours": resolved.default_ttl_hours,
        "auto_recall_enabled": resolved.auto_recall_enabled,
        "auto_recall_tool_name": resolved.auto_recall_tool_name,
        "auto_recall_query_template": resolved.auto_recall_query_template,
        "auto_recall_instruction": resolved.auto_recall_instruction,
        "chat_archive_days": resolved.chat_archive_days,
        "eigen_auteurs": resolved.eigen_auteurs,
        # Spraakassistent. De sleutel zelf komt NOOIT terug — alleen of er een
        # staat, zelfde patroon als het oauth-token.
        "openai_key_configured": bool(row.openai_key_encrypted),
        "voice_enabled": bool(row.voice_enabled),
        "voice_brein": row.voice_brein,
        "voice_microfoon": row.voice_microfoon,
        "voice_bevestiging": row.voice_bevestiging,
        "voice_woord": row.voice_woord,
        "voice_meld_runs": bool(row.voice_meld_runs),
        "voice_sessie_minuten": row.voice_sessie_minuten,
        "voice_dag_limiet_usd": row.voice_dag_limiet_usd,
        "fish_key_configured": bool(row.fish_key_encrypted),
        "voice_stt": resolved.voice_stt,
        "voice_fish_stem": resolved.voice_fish_stem,
        "voice_tts": resolved.voice_tts,
        "voice_tts_stem": resolved.voice_tts_stem,
        "voice_realtime_model": row.voice_realtime_model,
        "voice_stt_model": row.voice_stt_model,
        "voice_brein_model": row.voice_brein_model,
        "default_effort": resolved.default_effort,
        "fallback_model": resolved.fallback_model,
        "max_budget_usd": resolved.max_budget_usd,
        "autocompact": resolved.autocompact,
        "custom_agents_json": resolved.custom_agents_json,
        "default_agent": resolved.default_agent,
        "auto_hooks": resolved.auto_hooks,
    }


def update_settings(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    row = _row(db)
    plain_fields = (
        "cli_path", "default_model", "max_turns", "timeout_seconds", "extra_args",
        "enable_tool_search", "data_guard_default", "llm_guard_default",
        "guard_llm_url", "guard_llm_model", "default_image", "default_ttl_hours",
        "auto_recall_enabled", "auto_recall_tool_name", "auto_recall_query_template",
        "auto_recall_instruction",
        "chat_archive_days",
        "eigen_auteurs",
        "voice_enabled", "voice_brein", "voice_microfoon", "voice_bevestiging",
        "voice_woord", "voice_meld_runs", "voice_sessie_minuten",
        "voice_dag_limiet_usd", "voice_realtime_model", "voice_stt_model",
        "voice_brein_model", "voice_tts", "voice_tts_stem", "voice_tts_model",
        "voice_stt", "voice_fish_stem", "voice_fish_tts_model",
        "voice_fish_stt_model",
        "default_effort", "fallback_model", "max_budget_usd", "autocompact",
        "custom_agents_json", "default_agent", "auto_hooks",
    )
    for field in plain_fields:
        if field in payload:
            setattr(row, field, payload[field])
    if "oauth_token" in payload:
        token = (payload["oauth_token"] or "").strip()
        if token:
            from utils.crypto import encrypt
            row.oauth_token_encrypted = encrypt(token)
        else:
            row.oauth_token_encrypted = None
    if "openai_key" in payload:
        sleutel = (payload["openai_key"] or "").strip()
        if sleutel:
            from utils.crypto import encrypt
            row.openai_key_encrypted = encrypt(sleutel)
        else:
            row.openai_key_encrypted = None
    if "fish_key" in payload:
        sleutel = (payload["fish_key"] or "").strip()
        if sleutel:
            from utils.crypto import encrypt
            row.fish_key_encrypted = encrypt(sleutel)
        else:
            row.fish_key_encrypted = None
    # Het realtime-brein draait bij OpenAI, dus zonder sleutel kan dat niet.
    # Het pijplijn-brein denkt met de Claude-CLI en heeft de sleutel alleen
    # nodig om spraak te verstaan -- typen werkt dan gewoon. Daarom valt hij
    # terug op de pijplijn in plaats van de hele assistent uit te zetten.
    if row.voice_enabled and not row.openai_key_encrypted:
        row.voice_brein = "pipeline"
    row.updated_at = _now_iso()
    db.commit()
    return get_public_settings(db)
