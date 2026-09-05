"""
services/mcp/mcp_client.py

Real MCP protocol client for calling a tool on an MCPServer row, using the
official `mcp` SDK (a dependency of fastmcp). Two locations (the fix for
"MCP in Lab EN/OF extern in Claude zelf"):

- location="host": the server runs on/reachable from the LabX backend itself
  — http (streamable-http), sse, or a local stdio subprocess.
- location="lab": the server is a stdio process INSIDE a lab container. We
  don't re-implement a persistent bridge/process manager for the POC — the
  validation for this plan flagged buffering/lifecycle/concurrency as real
  complexity there, and the mcp SDK's stdio_client already spawns a process
  and pumps stdio correctly. So a lab-side server is just another stdio
  transport whose "command" happens to be `docker exec -i <container> <cmd>`:
  each tool call gets its own short-lived exec + session (simplest correct
  thing; no shared process, no lock, no restart logic needed). `-i` only
  (never `-t` — a tty would corrupt JSON-RPC framing by echoing input and
  turning `\\n` into `\\r\\n`, exactly as the validation warned).
"""
from __future__ import annotations

import shlex
from typing import Any, Dict, Optional

from component_logging import get_logger
from models.mcp_server import MCPServer

log = get_logger(__name__)


def _extract_result(result: Any) -> Any:
    """A CallToolResult's `.content` is a list of content blocks; flatten text
    blocks to a string (the common case), otherwise return the raw list."""
    content = getattr(result, "content", None)
    is_error = bool(getattr(result, "is_error", None) or getattr(result, "isError", False))
    if content is None:
        return result
    texts = []
    other = []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
        else:
            other.append(block)
    payload = "\n".join(texts) if texts and not other else (texts or other)
    if is_error:
        raise RuntimeError(f"MCP tool gaf een fout terug: {payload}")
    return payload


async def _resolve_auth_headers(server: MCPServer, *, db: Optional[Any] = None,
                                lab_id: Optional[str] = None,
                                purpose: str = "runtime") -> Dict[str, str]:
    """An Azure profile (lab-assigned, or the server's own default) wins over
    the server's static pasted token — a static Bearer token expires and a
    profile can mint a fresh one. Falls back to the static token otherwise,
    same as before this existed.

    `purpose="sync"` neemt de aparte sync-inloggegevens: het ophalen van de
    toolslijst is werk van LabX zelf, niet van een lab of een gebruiker."""
    if db is not None:
        from services.azure.azure_mcp_auth import bearer_header_for_profile, resolve_profile
        profile = resolve_profile(db, server, lab_id, purpose=purpose)
        if profile is not None:
            headers = await bearer_header_for_profile(profile)
            if headers:
                return headers
    return _static_auth_headers(server, purpose=purpose)


def _static_auth_headers(server: MCPServer, *, purpose: str = "runtime") -> Dict[str, str]:
    """Decrypt the server's auth config (Fernet, like an Azure profile
    secret) and build the header it implies. A real host MCP server behind
    auth — e.g. Nectar/HiveMind, which 400s with "Missing Bearer token
    (HIVE_TOKEN)" without this — needs this to ever answer a call."""
    raw = ((server.sync_auth_config_encrypted if purpose == "sync" else None)
           or server.auth_config_encrypted)
    if not raw:
        return {}
    try:
        import json
        from utils.crypto import decrypt
        cfg = json.loads(decrypt(raw))
    except Exception as exc:  # noqa: BLE001 — a bad/rotated key must not crash the call
        log.warningx("kon auth_config niet ontsleutelen", server=server.slug, error=str(exc)[:200])
        return {}
    auth_type = (cfg.get("type") or "").lower()
    if auth_type == "bearer" and cfg.get("token"):
        return {"Authorization": f"Bearer {cfg['token']}"}
    if auth_type == "header" and cfg.get("header") and cfg.get("value"):
        return {cfg["header"]: cfg["value"]}
    return {}


def _streamable_http_ctx(url: str, headers: Dict[str, str]):
    """The streamable-http transport, across the mcp SDK's 1.x/2.x split.

    mcp 2.x renamed `streamablehttp_client` to `streamable_http_client`, dropped
    its `headers=` parameter (headers now ride on an httpx client you hand in)
    and yields a 2-tuple instead of (read, write, get_session_id). Without this
    shim an unpinned `fastmcp` upgrade silently breaks every http MCP server —
    exactly how "cannot import name 'streamablehttp_client'" killed the Nectar
    sync. Callers take the streams positionally (`streams[0], streams[1]`) so
    both arities work.
    """
    import mcp.client.streamable_http as sh
    new_client = getattr(sh, "streamable_http_client", None)
    if new_client is not None:
        return new_client(url, http_client=sh.create_mcp_http_client(headers=headers))
    return sh.streamablehttp_client(url, headers=headers)


async def call_tool(server: MCPServer, remote_name: str, args: Dict[str, Any], *,
                    lab_container_id: Optional[str] = None, timeout: float = 120.0,
                    db: Optional[Any] = None, lab_id: Optional[str] = None) -> Any:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if server.location == "lab":
        if not lab_container_id:
            raise RuntimeError(
                f"MCP-server '{server.name}' draait in een lab, maar er is geen actief lab gebonden.")
        if not server.stdio_command:
            raise RuntimeError(f"MCP-server '{server.name}' heeft geen stdio_command geconfigureerd.")
        parts = shlex.split(server.stdio_command)
        params = StdioServerParameters(
            command="docker",
            args=["exec", "-i", "-w", "/workspace", lab_container_id, *parts],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(remote_name, args)
                return _extract_result(result)

    if server.server_type == "stdio":
        if not server.stdio_command:
            raise RuntimeError(f"MCP-server '{server.name}' heeft geen stdio_command geconfigureerd.")
        parts = shlex.split(server.stdio_command)
        env = await _stdio_env(server, db=db, lab_id=lab_id)
        params = StdioServerParameters(command=parts[0], args=parts[1:], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(remote_name, args)
                return _extract_result(result)

    if server.server_type == "sse":
        from mcp.client.sse import sse_client
        if not server.base_url:
            raise RuntimeError(f"MCP-server '{server.name}' heeft geen base_url.")
        headers = await _resolve_auth_headers(server, db=db, lab_id=lab_id)
        async with sse_client(server.base_url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(remote_name, args)
                return _extract_result(result)

    # default: http (streamable-http)
    if not server.base_url:
        raise RuntimeError(f"MCP-server '{server.name}' heeft geen base_url.")
    headers = await _resolve_auth_headers(server, db=db, lab_id=lab_id)
    async with _streamable_http_ctx(server.base_url, headers) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(remote_name, args)
            return _extract_result(result)


async def _stdio_env(server: MCPServer, *, db: Optional[Any] = None,
                     lab_id: Optional[str] = None,
                     purpose: str = "runtime") -> Optional[Dict[str, str]]:
    """None means "inherit the backend's own environment unchanged" (the mcp
    SDK's default when env=None) — only override with an isolated
    AZURE_CONFIG_DIR when an Azure profile actually resolves for this call,
    so a plain stdio server (no Azure involved) is untouched."""
    if db is None:
        return None
    from services.azure.azure_mcp_auth import resolve_profile, stdio_env_for_profile
    profile = resolve_profile(db, server, lab_id, purpose=purpose)
    if profile is None:
        return None
    import os
    overrides = await stdio_env_for_profile(profile)
    if not overrides:
        return None
    return {**os.environ, **overrides}


async def sync_tools(server: MCPServer, *,
                    lab_container_id: Optional[str] = None) -> Dict[str, Any]:
    """List a server's tools and upsert them as Tool rows — the picker's data
    source for the Skill Wizard (issue 3: show the tool's input schema).

    Voor een lab-gebonden server is `lab_container_id` verplicht: die draait
    niet op de host maar als proces IN een labcontainer, en er is geen andere
    manier om te weten wélke tools hij heeft dan hem daar even starten. Zonder
    dit bleef zo'n server voorgoed leeg — hij liet zich registreren en
    toestaan, maar de agent kreeg nooit een tool te zien, want de gateway leest
    uit de Tool-rijen die alleen een sync kan vullen."""
    from datetime import datetime, timezone
    from sqlalchemy.orm import Session as _Session
    from db.database import SessionLocal
    from models.tool import Tool

    db: _Session = SessionLocal()
    now = datetime.now(timezone.utc).isoformat()
    try:
        from mcp import ClientSession, StdioServerParameters
        if server.location == "lab":
            if not lab_container_id:
                return {"ok": False,
                        "error": "Deze server draait in een lab: start een lab dat hem toestaat "
                                 "en synchroniseer vanuit dat lab."}
            parts = shlex.split(server.stdio_command or "")
            if not parts:
                return {"ok": False, "error": "Geen stdio_command geconfigureerd."}
            from mcp.client.stdio import stdio_client
            # Zelfde vorm als call_tool: `-i` zonder `-t`, want een tty zou de
            # JSON-RPC-framing bederven.
            ctx = stdio_client(StdioServerParameters(
                command="docker",
                args=["exec", "-i", "-w", "/workspace", lab_container_id, *parts]))
        elif server.server_type == "stdio":
            from mcp.client.stdio import stdio_client
            parts = shlex.split(server.stdio_command or "")
            if not parts:
                return {"ok": False, "error": "Geen stdio_command geconfigureerd."}
            env = await _stdio_env(server, db=db, lab_id=None, purpose="sync")
            params = StdioServerParameters(command=parts[0], args=parts[1:], env=env)
            ctx = stdio_client(params)
        elif server.server_type == "sse":
            from mcp.client.sse import sse_client
            headers = await _resolve_auth_headers(server, db=db, lab_id=None, purpose="sync")
            ctx = sse_client(server.base_url, headers=headers)
        else:
            headers = await _resolve_auth_headers(server, db=db, lab_id=None, purpose="sync")
            ctx = _streamable_http_ctx(server.base_url, headers)

        async with ctx as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
        seen = set()
        for t in listed.tools:
            row = (db.query(Tool)
                   .filter(Tool.mcp_server_id == server.id, Tool.remote_name == t.name)
                   .one_or_none())
            if not row:
                row = Tool(mcp_server_id=server.id, remote_name=t.name, name=t.name,
                          created_at=now, updated_at=now)
                db.add(row)
            row.name = t.name
            row.description = t.description or ""
            # mcp 2.x renamed Tool.inputSchema to input_schema.
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
            row.argument = schema or {"type": "object", "properties": {}}
            row.updated_at = now
            seen.add(t.name)
        # `server` belongs to the CALLER's session (e.g. the router's
        # request-scoped one), not this function's own local `db` — mutating
        # it directly and committing `db` would silently persist nothing
        # (this function's commit only flushes objects that are members of
        # ITS session), and the caller doing `db.refresh(server)` afterwards
        # would then overwrite the in-memory mutation right back to whatever
        # was already in the database. Load/mutate this session's own copy.
        own_server = db.get(MCPServer, server.id)
        own_server.last_synced_at = now
        own_server.last_sync_status = "ok"
        own_server.last_sync_error = None
        db.commit()
        return {"ok": True, "tool_count": len(seen)}
    except Exception as exc:  # noqa: BLE001
        own_server = db.get(MCPServer, server.id)
        own_server.last_sync_status = "error"
        own_server.last_sync_error = str(exc)[:2000]
        db.commit()
        log.warningx("MCP-server sync mislukt", server=server.slug, error=str(exc)[:300])
        return {"ok": False, "error": str(exc)[:500]}
    finally:
        db.close()
