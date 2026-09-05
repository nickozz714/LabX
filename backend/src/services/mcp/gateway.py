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
    # Twee servers kunnen dezelfde toolnaam leveren — de Playwright-server op de
    # host en die in het lab heten allebei browser_navigate. Dan wint de
    # LAB-variant: die draait in de sandbox, achter de guard, mét de browser die
    # in dat lab geïnstalleerd is. De host-variant zou dezelfde aanroep
    # aannemen vanuit een container waar niets van dat alles staat, en klagen
    # over een ontbrekende browser terwijl hij aantoonbaar in het lab staat.
    by_name: Dict[str, int] = {}
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

        is_lab = getattr(server, "location", "host") == "lab" if server is not None else False
        tool = FunctionTool(
            name=name,
            description=description[:1024],
            parameters=schema,
            fn=_make_handler(tool_id, schema, name),
            meta={"labx_tool_id": tool_id, "labx_server": server_name, "labx_lab_tool": is_lab},
        )
        eerder = by_name.get(name)
        if eerder is None:
            by_name[name] = len(out)
            out.append(tool)
            continue
        if is_lab and not out[eerder].meta.get("labx_lab_tool"):
            log.infox("gateway: lab-tool gaat voor op de host-variant",
                      tool=name, server=server_name)
            out[eerder] = tool
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

        async def _lab_start_handler() -> Any:
            url = os.environ.get("LABX_INTERNAL_URL")
            token = os.environ.get("LABX_INTERNAL_TOKEN")
            return await _delegate_execute(url, token, tool_name="lab__start",
                                           args={}, lab_id=lab_id)

        def _lab_env_handler(tool_name: str):
            async def _handler(**kwargs: Any) -> Any:
                url = os.environ.get("LABX_INTERNAL_URL")
                token = os.environ.get("LABX_INTERNAL_TOKEN")
                return await _delegate_execute(url, token, tool_name=tool_name,
                                               args=kwargs, lab_id=lab_id)
            return _handler

        mcp.add_tool(FunctionTool(
            name="lab__start",
            description=("Start de gekoppelde lab-container als die uit staat. Een lab gaat "
                         "vanzelf uit na een tijd zonder gebruik; dit zet hem weer aan, met "
                         "behoud van alles in /workspace. Je hebt dit zelden nodig — een "
                         "shell-commando of bestandsactie start het lab zelf al — maar gebruik "
                         "het als je expliciet wilt weten of het lab draait.\n"
                         "Args: geen"),
            parameters={"type": "object", "properties": {}},
            fn=_lab_start_handler,
            meta={"labx_builtin": "lab__start"},
        ))

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

        # De agent mag zijn eigen omgeving bijwerken. Hij kan met de shell al
        # van alles installeren, maar dat blijft in de containerlaag hangen:
        # onzichtbaar voor de volgende, en weg zodra het lab opnieuw opgebouwd
        # wordt. Deze twee tools leggen het VAST op het lab, zodat het
        # terugkomt — dat is het verschil, niet het installeren zelf.
        mcp.add_tool(FunctionTool(
            name="lab__packages",
            description=("Toon wat er in dit lab geïnstalleerd kan worden, wat er aan staat en hoe "
                         "de laatste installatie liep. Gebruik dit vóór lab__install_packages (voor "
                         "de juiste sleutels) en erna (om te zien of het gelukt is — installeren "
                         "loopt op de achtergrond).\n"
                         "Args: geen"),
            parameters={"type": "object", "properties": {}},
            fn=_lab_env_handler("lab__packages"),
            meta={"labx_builtin": "lab__packages"},
        ))

        mcp.add_tool(FunctionTool(
            name="lab__install_packages",
            description=("Zet pakketten aan voor dit lab en installeer ze (bv. Playwright + "
                         "Chromium om een browser te kunnen aansturen). Gebruik dit in plaats van "
                         "los apt/pip/npm via de shell wanneer het gereedschap moet BLIJVEN: wat "
                         "hier binnenkomt staat op het lab en komt terug na een herstart of een "
                         "opnieuw opgebouwde container. Zit wat je nodig hebt niet in de lijst, "
                         "geef dan setup_script mee — dat draait als root, na de pakketten, en "
                         "opnieuw bij elke herstart, dus schrijf het zo dat een tweede keer geen "
                         "kwaad kan. Het installeren draait op de achtergrond; controleer met "
                         "lab__packages.\n"
                         "Args: packages (array van sleutels uit lab__packages), setup_script "
                         "(string, vervangt het huidige script)"),
            parameters={
                "type": "object",
                "properties": {
                    "packages": {"type": "array", "items": {"type": "string"},
                                 "description": "Sleutels uit lab__packages, bv. [\"playwright-python\"]"},
                    "setup_script": {"type": "string",
                                     "description": "Eigen shell-script voor wat niet in de catalogus staat"},
                },
            },
            fn=_lab_env_handler("lab__install_packages"),
            meta={"labx_builtin": "lab__install_packages"},
        ))

        mcp.add_tool(FunctionTool(
            name="lab__rebuild",
            description=("Bouw dit lab opnieuw op, eventueel op een ander image — de enige manier "
                         "om het image van een bestaand lab te wijzigen of bij te werken (zonder "
                         "image: dezelfde, in zijn nieuwste versie). /workspace blijft staan en de "
                         "aangezette pakketten worden opnieuw geïnstalleerd; alles wat je verder "
                         "buiten /workspace in de container had gezet is weg. Zet dus eerst veilig "
                         "wat je wilt houden, en gebruik dit alleen als een ander image echt nodig "
                         "is — een pakket erbij kan met lab__install_packages. Duurt minuten; het "
                         "lab is intussen niet bruikbaar.\n"
                         "Args: image (string, optioneel — bv. node:lts-bookworm)"),
            parameters={
                "type": "object",
                "properties": {
                    "image": {"type": "string",
                              "description": "Docker-image; leeg laten = huidige image bijwerken"},
                },
            },
            fn=_lab_env_handler("lab__rebuild"),
            meta={"labx_builtin": "lab__rebuild"},
        ))

        mcp.add_tool(FunctionTool(
            name="lab__shell_exec",
            description=("Voer een bash-commando uit IN de gekoppelde lab-container "
                         "(werkmap /workspace). Output gaat door de LabX data-egress-guard. "
                         "Dit is het enige uitvoeringspad in het lab. Staat het lab uit, dan "
                         "wordt het automatisch gestart."),
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

    # Board-tools — alleen als er een agent board aan dit lab hangt. Dit is
    # wat "het ticket aanvullen" mogelijk maakt: de agent leest en schrijft het
    # bord zelf, tijdens de run, in plaats van dat LabX achteraf een antwoord
    # ergens neerplakt. Ze staan óók in een achtergrondrun aan (een
    # ticket-run IS een achtergrondrun).
    board = None
    if lab_id:
        with SessionLocal() as db:
            from services.boards.board_service import BoardService
            board = BoardService(db).board_for_lab(lab_id)
            board_name = board.name if board else None
            board_columns = [f"{c.get('key')} ({c.get('name')})"
                             for c in (board.columns or [])] if board else []
    if board is not None:
        def _board_tool(tool_name: str):
            async def _handler(**kwargs: Any) -> Any:
                url = os.environ.get("LABX_INTERNAL_URL")
                token = os.environ.get("LABX_INTERNAL_TOKEN")
                return await _delegate_execute(url, token, tool_name=tool_name,
                                               args=kwargs or {}, lab_id=lab_id)
            return _handler

        columns_hint = ", ".join(board_columns) or "geen kolommen"
        board_specs = [
            ("board__list_tickets",
             f"Toon de tickets op het board '{board_name}' dat aan dit lab hangt. "
             f"Kolommen: {columns_hint}.\nArgs: status (string, kolom-key), limit (integer)",
             {"type": "object", "properties": {
                 "status": {"type": "string", "description": "Filter op kolom-key"},
                 "limit": {"type": "integer", "description": "Maximum aantal (standaard 50)"},
             }}),
            ("board__get_ticket",
             "Haal één ticket op met alle opmerkingen. Werkt met de LabX-sleutel "
             "(bv. LAB-12) of de externe sleutel (bv. een Jira-key).\nArgs: key* (string)",
             {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}),
            ("board__create_ticket",
             "Maak een nieuw ticket op het board — bv. vervolgwerk dat je tijdens "
             "een run tegenkomt. `description` is de OPDRACHT (wat er moet gebeuren), "
             "`acceptance_criteria` is wanneer het klaar is.\n"
             "Args: title* (string), description (string), acceptance_criteria (string), "
             "status (string), priority (string: low|normal|high|urgent), labels (array)",
             {"type": "object", "properties": {
                 "title": {"type": "string"},
                 "description": {"type": "string",
                                 "description": "De opdracht in Markdown — niet een verslag"},
                 "acceptance_criteria": {"type": "string",
                                         "description": "Toetsbare criteria in Markdown, meestal een lijstje"},
                 "status": {"type": "string", "description": f"Kolom-key. Kolommen: {columns_hint}"},
                 "priority": {"type": "string"},
                 "labels": {"type": "array", "items": {"type": "string"}},
             }, "required": ["title"]}),
            ("board__update_ticket",
             "Wijzig de OPDRACHT van een ticket: titel, omschrijving, acceptatiecriteria, "
             "labels, prioriteit, of verplaats het naar een andere kolom. "
             "LET OP: `description` is de opdracht, GEEN verslag — zet je bevindingen, "
             "voortgang of resultaten nooit hier neer maar in `board__comment_ticket`. "
             "Werk de omschrijving alleen bij als de opdracht zelf onduidelijk of "
             "onvolledig blijkt.\n"
             "Args: key* (string), title (string), description (string), "
             "acceptance_criteria (string), status (string), priority (string), "
             "assignee (string), labels (array)",
             {"type": "object", "properties": {
                 "key": {"type": "string"},
                 "title": {"type": "string"},
                 "description": {"type": "string",
                                 "description": "De opdracht in Markdown. Vervangt de bestaande tekst — geen verslag hier"},
                 "acceptance_criteria": {"type": "string",
                                         "description": "Toetsbare criteria in Markdown, meestal een lijstje"},
                 "status": {"type": "string", "description": f"Kolom-key. Kolommen: {columns_hint}"},
                 "priority": {"type": "string"},
                 "assignee": {"type": "string"},
                 "labels": {"type": "array", "items": {"type": "string"}},
             }, "required": ["key"]}),
            ("board__comment_ticket",
             "Plaats een opmerking op een ticket. DIT is de plek voor je bevindingen, "
             "voortgang, resultaten, vragen en waarom je vastliep — het werklogboek. "
             "Markdown mag. Gebruik hiervoor nooit de omschrijving.\n"
             "Je opmerkingen zijn INTERN: ze blijven in LabX en gaan niet naar het "
             "bronsysteem (Jira/DevOps), tenzij een mens ze daar bewust naartoe "
             "promoveert. Schrijf dus vrijuit wat je tegenkwam.\n"
             "Args: key* (string), body* (string)",
             {"type": "object", "properties": {
                 "key": {"type": "string"}, "body": {"type": "string"},
             }, "required": ["key", "body"]}),
        ]
        for tool_name, description, schema in board_specs:
            mcp.add_tool(FunctionTool(
                name=tool_name, description=description, parameters=schema,
                fn=_board_tool(tool_name), meta={"labx_builtin": tool_name},
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
              thread_bound=bool(thread_id), background=is_bg,
              board_bound=board is not None)
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
