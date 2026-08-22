"""routers/tool_router.py — read-mostly tool catalog for the Skill Wizard's
tool/MCP picker (issue 3): each tool's `argument` IS its input schema, shown
verbatim so the user has a guide for what input to give. `annotations` holds
LabX's `provenance` label for lab-located tools (control|data), consulted by
the data-guard chokepoint (see tool_execution_service.py)."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.mcp_server import MCPServer
from models.tool import Tool

router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(require_user)])


def _to_dict(t: Tool, server: MCPServer | None) -> Dict[str, Any]:
    return {
        "id": t.id, "name": t.name, "remote_name": t.remote_name, "description": t.description,
        "argument": t.argument or {"type": "object", "properties": {}},
        "output_schema": t.output_schema, "annotations": t.annotations or {},
        "is_enabled": t.is_enabled,
        "mcp_server_id": t.mcp_server_id,
        "mcp_server": ({"id": server.id, "name": server.name, "slug": server.slug,
                       "location": server.location} if server else None),
    }


@router.get("")
def list_tools(db: Session = Depends(get_db)):
    from repository.tool_repository import ToolRepository
    rows = ToolRepository(db).get_all_with_relations()
    return [_to_dict(t, getattr(t, "mcp_server", None)) for t in rows]


@router.post("/bulk")
def bulk_update_tools(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Bulk edit — per selection ('per tool uitvoeren is onhandig'): apply
    is_enabled and/or annotations.provenance to a list of tool_ids, or to
    every tool of one mcp_server_id."""
    from datetime import datetime, timezone
    tool_ids = payload.get("tool_ids") or []
    server_id = payload.get("mcp_server_id")
    if not tool_ids and not server_id:
        raise HTTPException(status_code=400, detail="Geef tool_ids of mcp_server_id op")
    q = db.query(Tool)
    if tool_ids:
        q = q.filter(Tool.id.in_([int(i) for i in tool_ids]))
    if server_id:
        q = q.filter(Tool.mcp_server_id == int(server_id))
    rows = q.all()
    now = datetime.now(timezone.utc).isoformat()
    for t in rows:
        if "is_enabled" in payload:
            t.is_enabled = bool(payload["is_enabled"])
        if payload.get("provenance") in ("control", "data"):
            t.annotations = {**(t.annotations or {}), "provenance": payload["provenance"]}
        t.updated_at = now
    db.commit()
    return {"ok": True, "updated": len(rows)}


@router.get("/{tool_id}")
def get_tool(tool_id: int, db: Session = Depends(get_db)):
    t = db.get(Tool, tool_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tool niet gevonden")
    server = db.get(MCPServer, t.mcp_server_id) if t.mcp_server_id else None
    return _to_dict(t, server)


@router.patch("/{tool_id}")
def update_tool(tool_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Mainly for setting `annotations.provenance` (control|data) on a
    lab-located tool — the operator's call when a tool's own description
    doesn't make it obvious to the guard."""
    from datetime import datetime, timezone
    t = db.get(Tool, tool_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tool niet gevonden")
    if "is_enabled" in payload:
        t.is_enabled = bool(payload["is_enabled"])
    if "annotations" in payload:
        t.annotations = {**(t.annotations or {}), **(payload["annotations"] or {})}
    t.updated_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    server = db.get(MCPServer, t.mcp_server_id) if t.mcp_server_id else None
    return _to_dict(t, server)
