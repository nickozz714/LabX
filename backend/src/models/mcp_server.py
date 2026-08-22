# models/mcp_server.py
#
# `location` is LabX's own addition (not in ND3X): it decides WHERE a server
# runs — "host" (proxied from the backend, like ND3X's http/sse/stdio servers)
# or "lab" (a stdio process started per-lab via `docker exec -i`, see
# services/mcp/lab_stdio_bridge.py). This is the model behind issue "MCP in
# Lab EN/OF extern in Claude zelf".
from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

MCP_SERVER_TYPES = ("http", "sse", "stdio", "builtin")
MCP_SERVER_LOCATIONS = ("host", "lab")
# Where a HOST server's tools are usable from a chat:
#   session — altijd beschikbaar voor de agent, in elke chat, ongeacht de
#             lab-allowlist (eigen capaciteit, zoals Nectar)
#   lab     — alleen beschikbaar wanneer het gekoppelde lab de server
#             expliciet toestaat (Toegang-tab)
#   both    — standaard: via de lab-allowlist, met always_allowed als
#             legacy-uitzondering (zie gateway._list_gateway_tools)
MCP_USAGE_SCOPES = ("session", "lab", "both")


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    server_type: Mapped[str] = mapped_column(String(16), nullable=False, default="http")
    location: Mapped[str] = mapped_column(String(16), nullable=False, default="host")
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # http/sse @ host
    stdio_command: Mapped[str | None] = mapped_column(Text, nullable=True)  # shell command, either transport
    stdio_install_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Deprecated / unused: an early plain-JSON auth field with no encryption.
    # Real auth now lives in auth_config_encrypted (Fernet, like an Azure
    # profile secret) — see mcp_client._auth_headers(). Left in place rather
    # than dropped (SQLite migrations here are additive-only, see init_db.py).
    auth_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Fernet-encrypted JSON {"type": "bearer", "token": "..."} or
    # {"type": "header", "header": "X-Api-Key", "value": "..."}. Never
    # returned by the API — only whether one is configured (has_auth).
    auth_config_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # A HOST server's tools are hidden from a lab-bound chat by default (they
    # run with the backend's own identity, outside the container/guard — see
    # gateway.py) unless a lab specifically allowlists them. That default is
    # right for anything touching customer systems, but wrong for a standing
    # org-knowledge tool like Nectar/HiveMind: "die moet de agent gewoon in
    # zijn eigen omgeving draaien en niet in het lab" (Nick) — it isn't lab
    # data and shouldn't need per-lab permission. always_allowed opts a HOST
    # server out of the per-lab gate entirely; irrelevant for location="lab".
    always_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # See MCP_USAGE_SCOPES above. Null = legacy row: derive from
    # always_allowed ("session" when True, else "both").
    usage_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Default Azure identity for this server's own stdio/http calls (Azure MCP
    # Server, Fabric MCP, ...): used when no lab is bound (e.g. a sync/test
    # call) or as the fallback when the active lab has no azure_profile_id of
    # its own. See Lab.azure_profile_id for the per-lab override and
    # services/azure/azure_mcp_auth.py for how a profile becomes an isolated
    # AZURE_CONFIG_DIR (stdio) or a live Bearer header (http/sse) — a static
    # pasted token in auth_config_encrypted expires and can't refresh itself,
    # which is why Microsoft's Azure-auth'd servers need this instead.
    azure_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_mcp_servers_location", "location"),)
