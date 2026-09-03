"""
config.py

App settings, read from env once at import time. Deliberately a plain class
(not pydantic-settings) — LabX has a handful of knobs, not a provider registry.
"""
from __future__ import annotations

import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # Storage
    LABX_HOME: str = os.environ.get("LABX_HOME", "/data")
    DB_PATH: str = os.environ.get("LABX_DB_PATH", str(Path(os.environ.get("LABX_HOME", "/data")) / "labx.db"))

    # Auth
    ADMIN_USERNAME: str = os.environ.get("LABX_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.environ.get("LABX_ADMIN_PASSWORD", "")
    JWT_SECRET: str = os.environ.get("LABX_JWT_SECRET", "")
    JWT_TTL_MINUTES: int = int(os.environ.get("LABX_JWT_TTL_MINUTES", "60"))

    # Secrets-at-rest (Azure profiles)
    FERNET_KEY: str = os.environ.get("LABX_FERNET_KEY", "")

    # Internal MCP-gateway <-> backend loopback
    INTERNAL_TOKEN: str = os.environ.get("LABX_INTERNAL_TOKEN", "")
    INTERNAL_URL: str = os.environ.get("LABX_INTERNAL_URL", "http://127.0.0.1:8090")

    # Docker sibling-runtime
    DOCKER_BIN: str = os.environ.get("LABX_DOCKER_BIN", "docker")
    DOCKER_HOST: str = os.environ.get("LABX_DOCKER_HOST", os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"))
    LAB_NETWORK: str = os.environ.get("LABX_LAB_NETWORK", "labx-labs")
    LAB_DEFAULT_IMAGE: str = os.environ.get("LABX_LAB_DEFAULT_IMAGE", "python:3-bookworm")
    LAB_PULL_ON_CREATE: bool = _bool("LABX_LAB_PULL_ON_CREATE", True)
    LAB_PROVISION_AZ: bool = _bool("LABX_LAB_PROVISION_AZ", True)
    LAB_REAPER_INTERVAL_SECONDS: int = int(os.environ.get("LABX_LAB_REAPER_INTERVAL_SECONDS", "300"))

    # Claude Code CLI
    CLI_PATH: str = os.environ.get("LABX_CLI_PATH", "claude")
    # Hoe lang de CLI een MCP-server mag geven om op te starten resp. een tool
    # te beantwoorden (milliseconden). De CLI staat standaard op 30s; de
    # labx-gateway importeert de halve backend en haalt dat koud niet altijd.
    MCP_STARTUP_TIMEOUT_MS: int = int(os.environ.get("LABX_MCP_STARTUP_TIMEOUT_MS", "120000"))
    MCP_TOOL_TIMEOUT_MS: int = int(os.environ.get("LABX_MCP_TOOL_TIMEOUT_MS", "300000"))
    CLAUDE_CODE_OAUTH_TOKEN: str = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    CLI_DEFAULT_MODEL: str = os.environ.get("LABX_CLI_DEFAULT_MODEL", "claude-sonnet-5")
    CLI_MAX_TURNS: int = int(os.environ.get("LABX_CLI_MAX_TURNS", "250"))
    CLI_ENABLE_TOOL_SEARCH: bool = _bool("LABX_CLI_ENABLE_TOOL_SEARCH", True)

    # Data-guard
    DATA_GUARD_LLM_ENABLED: bool = _bool("DATA_GUARD_LLM_ENABLED", True)
    DATA_GUARD_LLM_MODEL: str = os.environ.get("DATA_GUARD_LLM_MODEL", "qwen2.5:1.5b")
    DATA_GUARD_LLM_URL: str = os.environ.get("DATA_GUARD_LLM_URL", "")  # resolved at runtime if empty

    # CORS (frontend served separately)
    CORS_ORIGINS: list[str] = [
        o.strip() for o in os.environ.get("LABX_CORS_ORIGINS", "*").split(",") if o.strip()
    ]


settings = Settings()

Path(settings.LABX_HOME).mkdir(parents=True, exist_ok=True)
