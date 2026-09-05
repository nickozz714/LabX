# models/lab.py
#
# Ported from ND3X-public/src/models/playground.py, minus org_id/project_id
# (LabX is single-tenant). A Lab is an isolated Docker workspace: the agent
# works INSIDE the container, /workspace sits on a named volume so stop/start
# never loses work. The TTL-reaper cleans up expired labs.
from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # creating | running | stopped | error | expired
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="creating")
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    volume_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # docker-network alias the sibling container gets, so the backend can reach
    # a published lab port over DNS on the labx-labs bridge (see docker_runtime.py).
    network_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)

    cpu_limit: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    mem_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    allow_network: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ttl_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    expires_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Repos cloned into /workspace at creation:
    # [{name, url, source: registry|url, authenticated}] — tokens are NEVER stored.
    repos: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Container ports published on localhost (for APIs/MCP servers isolated in
    # the lab): [8000, ...].
    ports: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Data-egress-guard: container output heading to the LLM is screened
    # (services/lab/data_guard.py). Default ON.
    data_guard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Local-model second opinion (data_guard_llm.py) on top of the rules —
    # catches the subtle aggregation tail. Per lab, default ON.
    llm_guard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Per-lab ALLOWLIST: when a chat binds to this lab, the gateway strips the
    # CLI down to builtins + the container shell tool. These lists name
    # specific EXTERNAL MCP servers (slugs), tools (names) and skills (names)
    # to allow anyway. Empty = default (fully stripped).
    allowed_mcp: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    allowed_tools: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    allowed_skills: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    # The Azure identity a chat bound to this lab uses when it calls a HOST
    # MCP server that authenticates via Azure (Azure MCP Server, Fabric MCP,
    # ...) — takes priority over that server's own azure_profile_id, since a
    # chat is bound to one lab and "which Azure identity" is naturally a
    # per-lab choice. See services/azure/azure_mcp_auth.py.
    azure_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── inrichting bovenop het basis-image (zie models/lab_extra.py) ─────────
    # Keys uit `lab_extras` die in dit lab geïnstalleerd worden (Playwright +
    # Chromium, Node, ...). Zonder dit kon alleen een code-change bepalen wat
    # er in een lab zat.
    extras: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    # Vrij shell-script dat NA de extra's draait — voor het ene pakket waar
    # geen catalogus-entry voor is. Draait bij elk inrichten opnieuw, dus
    # schrijf het idempotent.
    setup_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | running | ok | error | skipped — het inrichten loopt op de
    # achtergrond (een browser binnenhalen duurt minuten), dus de UI moet
    # kunnen zien hoe het ervoor staat.
    provision_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Per stap: {key, label, status, exit_code, output} van de laatste ronde.
    provision_log: Mapped[list] = mapped_column(JSON, nullable=True, default=list)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_labs_status", "status"),
        Index("idx_labs_expires", "expires_at"),
    )
