"""repository/tool_repository.py — DB access for Tool rows, with the linked
MCPServer eager-loaded (the gateway and the Skill Wizard's tool picker both
need `tool.mcp_server` without an extra query per tool)."""
from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from models.mcp_server import MCPServer
from models.tool import Tool


class ToolRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_all_with_relations(self, *, skip: int = 0, limit: int = 2000) -> List[Tool]:
        rows = self.db.query(Tool).offset(skip).limit(limit).all()
        # Attach the server as a plain attribute (Tool has no ORM relationship
        # defined — LabX keeps models flat/explicit-FK, see models/tool.py) so
        # callers can do `tool.mcp_server` without a relationship mapping.
        server_ids = {t.mcp_server_id for t in rows if t.mcp_server_id}
        servers = {}
        if server_ids:
            for s in self.db.query(MCPServer).filter(MCPServer.id.in_(server_ids)).all():
                servers[s.id] = s
        for t in rows:
            t.mcp_server = servers.get(t.mcp_server_id) if t.mcp_server_id else None
        return rows
