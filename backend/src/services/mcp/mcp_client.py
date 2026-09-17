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
from typing import Any, Dict, List, Optional

from component_logging import get_logger
from models.mcp_server import MCPServer

log = get_logger(__name__)


def _extract_result(result: Any) -> Any:
    """A CallToolResult's `.content` is a list of content blocks; flatten text
    blocks to a string (the common case), otherwise return the raw list."""
    content = getattr(result, "content", None)
    # Eerst de naam van mcp 2.x, en de oude ALLEEN als die nieuwe er niet is:
    # een `or` zou bij een geslaagde aanroep (is_error=False) alsnog het oude
    # veld aanraken, en dat waarschuwt luid bij elke tool-call.
    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = getattr(result, "isError", False)
    is_error = bool(is_error)
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
        from services.azure.azure_mcp_auth import (
            ProfielPastNiet, bearer_header_for_profile, resolve_profile,
        )
        profile = resolve_profile(db, server, lab_id, purpose=purpose)
        if profile is not None:
            scope = (server.token_scope or "").strip() or "https://management.azure.com/.default"
            try:
                headers = await bearer_header_for_profile(profile, scope=scope, db=db)
            except ProfielPastNiet:
                # Hier hangt alles aan de vraag of dit profiel de BEDOELDE
                # inlogweg is voor deze server.
                #
                # Heeft de server een eigen `token_scope`, dan wél: dan is het
                # profiel het enige pad naar een token, en stil terugvallen op
                # niets levert een 401 met een onbegrijpelijke melding. Dat was
                # de reden dat deze fout er kwam (Work IQ met een az-CLI-profiel).
                #
                # Heeft de server GEEN token_scope, dan is het profiel meegekomen
                # via het LAB — en dan is het geen instelfout maar toeval: het lab
                # heeft nu eenmaal een Azure-identiteit voor heel ander werk. Zo
                # sneuvelde Nectar in elk lab met een Azure-profiel, terwijl die
                # server gewoon een statisch token heeft dat prima werkt. Dus:
                # waarschuwen en doorlopen naar dat token, zoals het altijd ging.
                if (server.token_scope or "").strip():
                    raise
                log.warningx("Azure-profiel past niet bij deze server; statisch token gebruikt",
                             server=server.slug, profiel=profile.name, soort=profile.kind)
                headers = {}
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
                    db: Optional[Any] = None, lab_id: Optional[str] = None,
                    sessie: Optional[str] = None) -> Any:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if server.location == "lab":
        if not lab_container_id:
            raise RuntimeError(
                f"MCP-server '{server.name}' draait in een lab, maar er is geen actief lab gebonden.")
        # Via de sessiepool: één blijvend proces per lab-server in plaats van
        # een nieuw proces per aanroep. Zie services/mcp/lab_session_pool.py —
        # een server die iets vasthoudt (een browser met zijn ingelogde sessie)
        # kan anders niet bestaan.
        from services.mcp.lab_session_pool import call_lab_tool
        result = await call_lab_tool(server, remote_name, args, container_id=lab_container_id)
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
    # Welke sessie er belt, maar alleen bij een server die dat mag weten. Voor
    # Nectar is dit het verschil tussen werken en niet werken: zijn focus-banen
    # hangen aan een sessie, en zonder dit moest het MODEL zijn eigen
    # sessietoken onthouden en meegeven bij elke aanroep. Dat deed het niet — en
    # dan viel Nectar terug op de projectbrede focus, waardoor elke agent de
    # taak van een ander ingespoten kreeg.
    if sessie and getattr(server, "stuur_sessie", False):
        headers = {**headers, "X-Hive-Session": str(sessie)}
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


def _leesbare_fout(exc: BaseException) -> str:
    """De echte oorzaak uit een ExceptionGroup peuteren.

    De MCP-client draait zijn verbinding in een TaskGroup, en die verpakt elke
    fout in een groep. Wat er dan in de UI belandde was letterlijk "unhandled
    errors in a TaskGroup (1 sub-exception)" — een melding die niets zegt over
    wat er mis is. De onderliggende fout (een 401, een DNS-fout, een proces dat
    niet start) zit één laag dieper en is precies wat je moet weten.
    """
    gezien: List[str] = []

    def _plat(e: BaseException, diepte: int = 0) -> None:
        if diepte > 4:
            return
        sub = getattr(e, "exceptions", None)
        if sub:
            for s in sub:
                _plat(s, diepte + 1)
            return
        tekst = (str(e) or type(e).__name__).strip()
        if tekst and tekst not in gezien:
            gezien.append(f"{type(e).__name__}: {tekst}" if str(e) else type(e).__name__)

    _plat(exc)
    return " | ".join(gezien) or (str(exc) or type(exc).__name__)


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
            # Een tool die eerder was uitgezet omdat hij verdwenen leek, hoort
            # weer aan te gaan zodra hij terug is.
            if row.id is not None and not row.is_enabled:
                row.is_enabled = True
            row.updated_at = now
            seen.add(t.name)

        # Wat de server NIET meer aanbiedt, gaat uit.
        #
        # Dit ontbrak, en dat was de tweede helft van KRI-44. Microsoft
        # hernoemde in de Fabric MCP-server alle `onelake_*`-tools van
        # underscores naar streepjes (`onelake_list_tables` →
        # `onelake_list-tables`). De sync voegde de nieuwe namen toe en liet de
        # oude staan, dus LabX bleef 35 tools aanbieden die niet meer bestonden.
        # De agent kreeg ze netjes in zijn lijst en daarna "The tool was not
        # found" — terwijl `datafactory_*` en `core_*` in dezelfde sessie wél
        # werkten, want die waren niet hernoemd. Juist dat maakte het
        # raadselachtig: een verouderde toolslijst ziet er van buiten precies
        # hetzelfde uit als een actuele.
        #
        # UITZETTEN en niet verwijderen: skills en lab-allowlists verwijzen naar
        # tools op naam, en een rij weggooien zou die verwijzingen stil breken.
        # En alleen als er ÍETS teruggekomen is: een server die door een storing
        # nul tools opsomt, is geen server zonder tools.
        uitgezet = 0
        if seen:
            for row in (db.query(Tool)
                        .filter(Tool.mcp_server_id == server.id,
                                Tool.remote_name.notin_(seen),
                                Tool.is_enabled == True).all()):  # noqa: E712
                row.is_enabled = False
                row.updated_at = now
                uitgezet += 1
            if uitgezet:
                log.warningx("Tools uitgezet: de server biedt ze niet meer aan",
                             server=server.slug, aantal=uitgezet)

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
        return {"ok": True, "tool_count": len(seen), "disabled": uitgezet}
    except Exception as exc:  # noqa: BLE001
        own_server = db.get(MCPServer, server.id)
        own_server.last_sync_status = "error"
        own_server.last_sync_error = _leesbare_fout(exc)[:2000]
        db.commit()
        log.warningx("MCP-server sync mislukt", server=server.slug, error=_leesbare_fout(exc)[:300])
        return {"ok": False, "error": _leesbare_fout(exc)[:500]}
    finally:
        db.close()
