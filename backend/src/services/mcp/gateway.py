"""
services/mcp/gateway.py

An MCP server that re-exposes LabX's own tools (every enabled MCP server,
host- or lab-located) to the autonomous Claude Code CLI. Ported from
ND3X-public/src/services/mcp/mcp_gateway.py, renamed nd3x -> labx. LabX stays
the source of truth and the auth owner: the gateway LISTS tools from the DB
and DELEGATES every call back to the main server over a loopback HTTP
endpoint (authenticated with an in-process shared secret) — the DB session
and, for lab-located servers, the running container all live in the main
process, not this stdio subprocess.

Transport to the CLI is stdio: the CLI spawns this module as a subprocess
and talks over stdin/stdout (`--mcp-config` with a `command` entry). Tools
show up to the CLI as `mcp__labx__<tool>`.

This is the fix for issue 2 ("tool-keuze kost te veel tokens"): the gateway
registers every allowed tool eagerly, but the CLI runs with
ENABLE_TOOL_SEARCH=true (see claude_cli_provider.py), so only names +
descriptions sit in context; a tool's schema loads on demand. Skills are not
a gate here — every enabled tool is reachable whether or not its skill was
mentioned, exactly like the ND3X CLI-agent route already worked.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from component_logging import get_logger

log = get_logger(__name__)

SERVER_NAME = "labx"
_EXCLUDED_TOOL_NAMES = {"web_search", "web_fetch"}  # the CLI has its own


async def _delegate_execute(url: str, token: str, *, tool_id: Optional[int] = None,
                            tool_name: Optional[str] = None, args: Dict[str, Any],
                            lab_id: Optional[str] = None) -> Any:
    import httpx
    endpoint = url.rstrip("/") + "/api/internal/mcp/execute"
    body: Dict[str, Any] = {"args": args}
    if tool_name:
        body["tool_name"] = tool_name
    else:
        body["tool_id"] = tool_id
    if lab_id:
        body["lab_id"] = lab_id
    thread_id = os.environ.get("LABX_GATEWAY_THREAD")
    if thread_id:
        body["thread_id"] = thread_id
    if os.environ.get("LABX_GATEWAY_IS_BG"):
        body["is_background"] = True
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(endpoint, headers={"X-LabX-Internal-Token": token}, json=body)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        # A tool-level failure travels back as a MESSAGE, not a bare 500 —
        # raising here makes fastmcp return it as a tool error the model can
        # actually read and act on (fix a wrong argument name, retry, pick
        # another tool) instead of concluding the whole server is down.
        raise RuntimeError(str(data["error"]))
    return data.get("result") if isinstance(data, dict) else data


async def _execute_tool(tool_id: int, args: Dict[str, Any]) -> Any:
    url = os.environ.get("LABX_INTERNAL_URL")
    token = os.environ.get("LABX_INTERNAL_TOKEN")
    lab_id = os.environ.get("LABX_GATEWAY_LAB")
    if url and token:
        return await _delegate_execute(url, token, tool_id=tool_id, args=args, lab_id=lab_id)
    # Non-delegated fallback (tests): execute locally in this process.
    from db.database import SessionLocal
    from services.mcp.tool_execution_service import ToolExecutionService
    with SessionLocal() as db:
        return await ToolExecutionService(db).execute_tool(tool_id, args, lab_id=lab_id)


async def _execute_shell(command: str, timeout: float = 60) -> Any:
    url = os.environ.get("LABX_INTERNAL_URL")
    token = os.environ.get("LABX_INTERNAL_TOKEN")
    lab_id = os.environ.get("LABX_GATEWAY_LAB")
    if not lab_id:
        raise RuntimeError("Geen lab gebonden aan deze chat-run")
    if url and token:
        return await _delegate_execute(url, token, tool_name="lab__shell_exec",
                                       args={"command": command, "timeout": timeout}, lab_id=lab_id)
    from db.database import SessionLocal
    from services.mcp.tool_execution_service import ToolExecutionService
    with SessionLocal() as db:
        return await ToolExecutionService(db).execute_builtin_shell(
            lab_id=lab_id, command=command, timeout=timeout)


def _tool_to_schema(argument: Any) -> Dict[str, Any]:
    if isinstance(argument, dict) and argument.get("type") == "object":
        return argument
    if isinstance(argument, dict) and "properties" in argument:
        return {"type": "object", **argument}
    return {"type": "object", "properties": {}}


def _args_signature(schema: Dict[str, Any]) -> str:
    """Compact 'Args: skill_uid* (string), limit (integer)' line for the tool
    DESCRIPTION. With tool-search on, only name+description sit in context —
    the full schema loads on demand, and a model that skips that load will
    guess argument names (exactly how `uid` vs `skill_uid` happened). Putting
    the names in the always-visible description removes the guesswork."""
    props = schema.get("properties") or {}
    if not props:
        return ""
    required = set(schema.get("required") or [])
    parts = []
    for pname, pdef in list(props.items())[:12]:
        ptype = pdef.get("type", "any") if isinstance(pdef, dict) else "any"
        parts.append(f"{pname}{'*' if pname in required else ''} ({ptype})")
    return "Args: " + ", ".join(parts)


def _validate_args(kwargs: Dict[str, Any], schema: Dict[str, Any], tool_name: str) -> None:
    """Cheap, dependency-free gate at the gateway: reject unknown/missing
    argument names with a message that spells out the expected signature —
    BEFORE the call travels to the remote server. Name-level only (types are
    left to the server): the failure mode this exists for is a guessed
    argument name, and the fix is telling the model the right names."""
    props = schema.get("properties") or {}
    if not props:
        return
    required = set(schema.get("required") or [])
    unknown = [k for k in kwargs if k not in props]
    missing = [k for k in required if k not in kwargs]
    if unknown or missing:
        sig = _args_signature(schema)
        problems = []
        if unknown:
            problems.append(f"onbekende argumenten: {', '.join(unknown)}")
        if missing:
            problems.append(f"ontbrekende verplichte argumenten: {', '.join(missing)}")
        raise ValueError(f"Ongeldige aanroep van {tool_name} — {'; '.join(problems)}. "
                         f"Verwacht — {sig or 'geen argumenten'}.")


def _skill_scoped_tool_ids(db) -> Dict[int, set]:
    from models.skill import Skill
    from models.skill_tool import SkillTool
    out: Dict[int, set] = {}
    rows = (db.query(SkillTool, Skill)
            .join(Skill, Skill.id == SkillTool.skill_id)
            .filter(SkillTool.is_enabled == True, Skill.is_enabled == True)  # noqa: E712
            .all())
    for link, skill in rows:
        if skill.name:
            out.setdefault(link.tool_id, set()).add(skill.name)
    return out


def _lab_allowlist(db, lab_id: Optional[str]) -> tuple[set, set, set]:
    if not lab_id:
        return (set(), set(), set())
    try:
        from models.lab import Lab
        p = db.get(Lab, lab_id)
        if p is None:
            return (set(), set(), set())
        allow_mcp = {str(s).strip().lower() for s in (p.allowed_mcp or [])}
        allow_tools = {str(t).strip() for t in (p.allowed_tools or [])}
        allow_skills = {str(s).strip() for s in (p.allowed_skills or [])}
        return (allow_mcp, allow_tools, allow_skills)
    except Exception as exc:  # noqa: BLE001 — the allowlist must never break the gateway
        log.warningx("gateway: allowlist lezen mislukt", error=str(exc))
        return (set(), set(), set())


def _list_gateway_tools(db, lab_id: Optional[str]) -> List[Any]:
    """Every enabled DB tool, minus excluded web tools. A tool whose server is
    HOST-located and NOT on the lab's allowlist is dropped once a lab is
    bound — same rule as ND3X: host-side tools run with the backend's own
    identity, outside the container and the guard, so a bound chat only
    reaches them when explicitly allowlisted.

    Exception: a HOST server marked `always_allowed` skips that gate
    entirely, in every lab — for a standing org-knowledge tool (Nectar/
    HiveMind) that is the agent's own capability, not lab data, and
    shouldn't need per-lab permission ("moet de agent gewoon in zijn eigen
    omgeving draaien en niet in het lab")."""
    from fastmcp.tools.function_tool import FunctionTool
    from repository.tool_repository import ToolRepository

    allow_mcp, allow_tools, allow_skills = _lab_allowlist(db, lab_id)
    skill_scoped = _skill_scoped_tool_ids(db) if allow_skills else {}

    tools = ToolRepository(db).get_all_with_relations(skip=0, limit=2000)
    out: List[Any] = []
    for t in tools:
        if not getattr(t, "is_enabled", True):
            continue
        server = getattr(t, "mcp_server", None)
        if server is not None and not getattr(server, "is_enabled", True):
            continue
        if lab_id and server is not None and getattr(server, "location", "host") == "host":
            # usage_scope decides the gate (see MCP_USAGE_SCOPES in the
            # model): "session" = always available to the agent; "lab" =
            # ONLY via the lab's allowlist (always_allowed does not bypass);
            # "both"/legacy-null = allowlist-gated with always_allowed as
            # the bypass (the pre-scope behavior, unchanged).
            scope = (getattr(server, "usage_scope", None) or
                     ("session" if getattr(server, "always_allowed", False) else "both"))
            if scope != "session":
                slug = str(getattr(server, "slug", "") or "").lower()
                tname = (t.name or "").strip()
                allowlisted = slug in allow_mcp or tname in allow_tools
                bypass = scope == "both" and getattr(server, "always_allowed", False)
                if not (allowlisted or bypass):
                    continue
        name = (t.name or "").strip()
        if not name or name in _EXCLUDED_TOOL_NAMES:
            continue
        if allow_skills:
            linked = skill_scoped.get(t.id)
            if linked and not (linked & allow_skills):
                continue
        tool_id = t.id
        server_name = getattr(server, "name", None)
        schema = _tool_to_schema(t.argument)

        def _make_handler(_tool_id: int, _schema: Dict[str, Any], _name: str):
            async def _handler(**kwargs: Any) -> Any:
                _validate_args(kwargs or {}, _schema, _name)
                return await _execute_tool(_tool_id, kwargs or {})
            return _handler

        description = (t.description or name).strip()
        sig = _args_signature(schema)
        if sig:
            description = f"{description}\n{sig}"

        out.append(FunctionTool(
            name=name,
            description=description[:1024],
            parameters=schema,
            fn=_make_handler(tool_id, schema, name),
            meta={"labx_tool_id": tool_id, "labx_server": server_name},
        ))
    return out


def build_server():
    """Build the FastMCP stdio server with the current LabX tool set."""
    from fastmcp import FastMCP
    from fastmcp.tools.function_tool import FunctionTool
    from db.database import SessionLocal

    mcp = FastMCP(name="LabX Gateway")
    lab_id = os.environ.get("LABX_GATEWAY_LAB")
    with SessionLocal() as db:
        for tool in _list_gateway_tools(db, lab_id):
            mcp.add_tool(tool)

    # The one always-on tool once a lab is bound: the guarded container shell.
    # A stripped-down CLI (no native Bash) has no other way to work in the lab.
    if lab_id:
        async def _shell_handler(command: str, timeout: float = 60) -> Any:
            return await _execute_shell(command, timeout)

        async def _write_file_handler(path: str, content: str) -> Any:
            url = os.environ.get("LABX_INTERNAL_URL")
            token = os.environ.get("LABX_INTERNAL_TOKEN")
            return await _delegate_execute(url, token, tool_name="lab__write_file",
                                           args={"path": path, "content": content}, lab_id=lab_id)

        mcp.add_tool(FunctionTool(
            name="lab__write_file",
            description=("Schrijf een bestand IN de lab-container, byte-exact (betrouwbaarder dan "
                         "heredocs via de shell voor scripts/code/configs — bv. om een skill-script "
                         "uit Nectar te installeren). Mappen worden automatisch aangemaakt.\n"
                         "Args: path* (string, onder /workspace), content* (string)"),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Doelpad in het lab, bv. /workspace/.skills/FabricClient/fabric_client.py"},
                    "content": {"type": "string", "description": "De volledige bestandsinhoud"},
                },
                "required": ["path", "content"],
            },
            fn=_write_file_handler,
            meta={"labx_builtin": "lab__write_file"},
        ))

        mcp.add_tool(FunctionTool(
            name="lab__shell_exec",
            description=("Voer een bash-commando uit IN de gekoppelde lab-container "
                         "(werkmap /workspace). Output gaat door de LabX data-egress-guard. "
                         "Dit is het enige uitvoeringspad in het lab."),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Het bash-commando"},
                    "timeout": {"type": "number", "description": "Timeout in seconden (standaard 60)"},
                },
                "required": ["command"],
            },
            fn=_shell_handler,
            meta={"labx_builtin": "lab__shell_exec"},
        ))

    # Background-task tools — ONLY for a foreground, thread-bound chat turn.
    # A background run gets no thread env and carries LABX_GATEWAY_IS_BG, so
    # these tools simply don't exist there: a background task cannot spawn
    # further background tasks (unbounded process-tree guard, enforced by
    # absence here + a hard refusal in internal_router as backstop).
    thread_id = os.environ.get("LABX_GATEWAY_THREAD")
    is_bg = bool(os.environ.get("LABX_GATEWAY_IS_BG"))
    if lab_id and thread_id and not is_bg:
        async def _task_start_handler(prompt: str) -> Any:
            url = os.environ.get("LABX_INTERNAL_URL")
            token = os.environ.get("LABX_INTERNAL_TOKEN")
            return await _delegate_execute(url, token, tool_name="task__start_background",
                                           args={"prompt": prompt}, lab_id=lab_id)

        async def _task_check_handler() -> Any:
            url = os.environ.get("LABX_INTERNAL_URL")
            token = os.environ.get("LABX_INTERNAL_TOKEN")
            return await _delegate_execute(url, token, tool_name="task__check_background",
                                           args={}, lab_id=lab_id)

        mcp.add_tool(FunctionTool(
            name="task__start_background",
            description=("Zet LANGLOPEND werk (een pipeline volgen, een lange job, een grote "
                         "scan — alles dat duidelijk minuten of langer duurt) als LabX-"
                         "achtergrondtaak weg, zodat de chat direct bruikbaar blijft. De taak "
                         "draait als zelfstandige agent met dezelfde tools en gesprekscontext. "
                         "Geef de gebruiker meteen antwoord met wat er gestart is en het "
                         "taak-id. NIET gebruiken voor werk dat binnen een minuut klaar is.\n"
                         "Args: prompt* (string)"),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",
                              "description": "De volledige, zelfstandige opdracht voor de achtergrondtaak"},
                },
                "required": ["prompt"],
            },
            fn=_task_start_handler,
            meta={"labx_builtin": "task__start_background"},
        ))
        mcp.add_tool(FunctionTool(
            name="task__check_background",
            description=("Status van de achtergrondtaken van dit gesprek: per taak het id, "
                         "de status, voortgang en (indien klaar) het resultaat. Roep dit aan "
                         "wanneer de gebruiker naar voortgang vraagt of wanneer je eerder een "
                         "taak startte en het resultaat nodig hebt.\nArgs: geen"),
            parameters={"type": "object", "properties": {}},
            fn=_task_check_handler,
            meta={"labx_builtin": "task__check_background"},
        ))
    log.infox("MCP gateway (stdio) gebouwd", lab_bound=bool(lab_id),
              thread_bound=bool(thread_id), background=is_bg)
    return mcp


def mcp_config_for_cli(*, python: Optional[str] = None, cwd: Optional[str] = None,
                       lab_id: Optional[str] = None, thread_id: Optional[str] = None,
                       is_background: bool = False) -> Dict[str, Any]:
    """The --mcp-config object the chat runner writes for the CLI."""
    from config import settings
    from services.mcp.internal_auth import INTERNAL_MCP_TOKEN

    py = python or sys.executable
    src_root = cwd or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env: Dict[str, str] = {"PYTHONPATH": src_root}
    if lab_id:
        env["LABX_GATEWAY_LAB"] = str(lab_id)
    if thread_id:
        env["LABX_GATEWAY_THREAD"] = str(thread_id)
    if is_background:
        env["LABX_GATEWAY_IS_BG"] = "1"
    env["LABX_INTERNAL_URL"] = settings.INTERNAL_URL
    env["LABX_INTERNAL_TOKEN"] = INTERNAL_MCP_TOKEN
    env["LABX_DB_PATH"] = settings.DB_PATH
    return {
        "mcpServers": {
            SERVER_NAME: {
                "command": py,
                "args": ["-m", "services.mcp.gateway"],
                "cwd": src_root,
                "env": env,
            }
        }
    }


def main() -> None:
    src_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if src_root not in sys.path:
        sys.path.insert(0, src_root)
    # stdout IS the JSON-RPC wire in stdio transport — any log line written
    # there corrupts the framing (the client skips the bad line, but every
    # skip is a "Failed to parse JSONRPC message" and one wire-read wasted).
    # Force ALL logging (component_logging's handler included) to stderr.
    import logging
    logging.basicConfig(stream=sys.stderr, force=True)
    for handler in logging.root.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.stream = sys.stderr
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
