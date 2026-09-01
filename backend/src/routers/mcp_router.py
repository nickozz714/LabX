"""routers/mcp_router.py — MCP server registry (host- or lab-located, the fix
for "MCP in Lab EN/OF extern in Claude zelf") + tool sync."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.mcp_server import MCP_SERVER_LOCATIONS, MCP_SERVER_TYPES, MCPServer

router = APIRouter(prefix="/mcp-servers", tags=["mcp"], dependencies=[Depends(require_user)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dict(s: MCPServer) -> Dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "slug": s.slug, "description": s.description,
        "server_type": s.server_type, "location": s.location, "base_url": s.base_url,
        "stdio_command": s.stdio_command, "is_enabled": s.is_enabled,
        "always_allowed": s.always_allowed,
        "usage_scope": s.usage_scope or ("session" if s.always_allowed else "both"),
        "azure_profile_id": s.azure_profile_id,
        "has_auth": bool(s.auth_config_encrypted),
        # Aparte inloggegevens voor het synchroniseren; leeg = de gewone.
        "sync_azure_profile_id": s.sync_azure_profile_id,
        "has_sync_auth": bool(s.sync_auth_config_encrypted),
        "last_synced_at": s.last_synced_at, "last_sync_status": s.last_sync_status,
        "last_sync_error": s.last_sync_error,
    }


def _set_sync_auth(s: MCPServer, payload: Dict[str, Any]) -> None:
    """`sync_auth`, zelfde vorm en zelfde schrijf-alleen-regel als `auth`, maar
    alleen gebruikt bij het ophalen van de toolslijst."""
    if "sync_auth" not in payload:
        return
    auth = payload.get("sync_auth")
    if not auth or (auth.get("type") or "none") == "none":
        s.sync_auth_config_encrypted = None
        return
    from utils.crypto import encrypt
    s.sync_auth_config_encrypted = encrypt(json.dumps(auth))


def _set_auth(s: MCPServer, payload: Dict[str, Any]) -> None:
    """`auth` in the payload is write-only, same pattern as an Azure profile
    secret: {"type": "bearer", "token": "..."} or
    {"type": "header", "header": "X-Api-Key", "value": "..."}, or
    {"type": "none"} / null to clear it. Never echoed back — see `has_auth`
    in _to_dict instead."""
    if "auth" not in payload:
        return
    auth = payload.get("auth")
    if not auth or (auth.get("type") or "none") == "none":
        s.auth_config_encrypted = None
        return
    from utils.crypto import encrypt
    s.auth_config_encrypted = encrypt(json.dumps(auth))


@router.get("/catalog")
def list_catalog(db: Session = Depends(get_db)):
    """The built-in standard library of well-known MCP servers (one-click
    install) — see services/mcp/catalog.py. Each entry also reports whether
    it's already installed (matched by slug)."""
    from services.mcp.catalog import CATALOG
    installed = {s.slug for s in db.query(MCPServer.slug).all()}
    return [{**c, "installed": c["key"] in {s for s in installed}} for c in CATALOG]


@router.post("/catalog/{key}/install")
def install_from_catalog(key: str, db: Session = Depends(get_db)):
    from services.mcp.catalog import find
    entry = find(key)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Onbekende catalogus-entry '{key}'")
    if db.query(MCPServer).filter(MCPServer.slug == key).first():
        raise HTTPException(status_code=409, detail=f"'{entry['name']}' is al geïnstalleerd")
    now = _now_iso()
    if entry["kind"] == "http":
        s = MCPServer(
            name=entry["name"], slug=key, description=entry["description"],
            server_type="http", location=entry["suggested_location"],
            base_url=entry["base_url"],
            is_enabled=True, created_at=now, updated_at=now,
        )
    else:
        runner = entry.get("runner", "npx")
        install_flag = "-y " if runner == "npx" else ""
        args = f" {entry['suggested_args']}" if entry.get("suggested_args") else ""
        s = MCPServer(
            name=entry["name"], slug=key, description=entry["description"],
            server_type="stdio", location=entry["suggested_location"],
            stdio_command=f"{runner} {install_flag}{entry['package']}{args}",
            is_enabled=True, created_at=now, updated_at=now,
        )
    db.add(s)
    db.commit()
    return _to_dict(s)


@router.get("")
def list_servers(db: Session = Depends(get_db)):
    return [_to_dict(s) for s in db.query(MCPServer).order_by(MCPServer.name.asc()).all()]


@router.post("")
def create_server(payload: Dict[str, Any], db: Session = Depends(get_db)):
    slug = (payload.get("slug") or "").strip().lower()
    name = (payload.get("name") or "").strip()
    if not slug or not name:
        raise HTTPException(status_code=400, detail="name en slug zijn verplicht")
    server_type = payload.get("server_type") or "http"
    location = payload.get("location") or "host"
    if server_type not in MCP_SERVER_TYPES:
        raise HTTPException(status_code=400, detail=f"Onbekend server_type '{server_type}'")
    if location not in MCP_SERVER_LOCATIONS:
        raise HTTPException(status_code=400, detail=f"Onbekende location '{location}'")
    if db.query(MCPServer).filter(MCPServer.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"Slug '{slug}' bestaat al")
    now = _now_iso()
    s = MCPServer(
        name=name, slug=slug, description=payload.get("description"),
        server_type=server_type, location=location,
        base_url=payload.get("base_url"), stdio_command=payload.get("stdio_command"),
        stdio_install_command=payload.get("stdio_install_command"),
        is_enabled=bool(payload.get("is_enabled", True)),
        always_allowed=bool(payload.get("always_allowed", False)),
        azure_profile_id=payload.get("azure_profile_id"),
        sync_azure_profile_id=payload.get("sync_azure_profile_id"),
        created_at=now, updated_at=now,
    )
    _set_auth(s, payload)
    _set_sync_auth(s, payload)
    db.add(s)
    db.commit()
    return _to_dict(s)


@router.patch("/{server_id}")
def update_server(server_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    s = db.get(MCPServer, server_id)
    if not s:
        raise HTTPException(status_code=404, detail="MCP-server niet gevonden")
    for field in ("name", "description", "server_type", "location", "base_url",
                  "stdio_command", "stdio_install_command", "is_enabled",
                  "always_allowed", "azure_profile_id", "usage_scope",
                  "sync_azure_profile_id"):
        if field in payload:
            setattr(s, field, payload[field])
    _set_auth(s, payload)
    _set_sync_auth(s, payload)
    s.updated_at = _now_iso()
    db.commit()
    return _to_dict(s)


@router.delete("/{server_id}")
def delete_server(server_id: int, db: Session = Depends(get_db)):
    s = db.get(MCPServer, server_id)
    if not s:
        raise HTTPException(status_code=404, detail="MCP-server niet gevonden")
    from models.tool import Tool
    db.query(Tool).filter(Tool.mcp_server_id == server_id).delete(synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/{server_id}/sync")
async def sync_server(server_id: int, db: Session = Depends(get_db)):
    """List the server's tools and upsert them as Tool rows. Only meaningful
    for a HOST server (a lab server can't be reached until a lab runs it —
    sync a lab-located server from within the lab's own detail screen once
    running; not implemented in this POC pass, see plan Fase 4)."""
    s = db.get(MCPServer, server_id)
    if not s:
        raise HTTPException(status_code=404, detail="MCP-server niet gevonden")
    from services.mcp.mcp_client import sync_tools
    result = await sync_tools(s)
    db.refresh(s)
    return {**result, "server": _to_dict(s)}
