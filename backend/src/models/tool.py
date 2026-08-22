# models/tool.py — a callable tool, synced from an MCP server (or builtin).
# `argument` mirrors ND3X's Tool.argument: the MCP inputSchema verbatim, so the
# UI can render it as a guide for what input a tool expects.
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mcp_server_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=True
    )
    remote_name: Mapped[str] = mapped_column(String(255), nullable=False)  # name as the MCP server exposes it
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # display/registered name
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    argument: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # inputSchema
    output_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    annotations: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("mcp_server_id", "remote_name", name="uq_tool_server_remote_name"),
        Index("idx_tools_server", "mcp_server_id"),
    )
