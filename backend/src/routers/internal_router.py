"""routers/internal_router.py — loopback endpoint the MCP gateway subprocess
calls back into (see services/mcp/gateway.py's `_delegate_execute`). Not
reachable from outside: guarded by a per-process shared secret, not JWT."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy.orm import Session

from db.database import SessionLocal
from services.mcp.internal_auth import INTERNAL_MCP_TOKEN
from services.mcp.tool_execution_service import ToolExecutionService

router = APIRouter(prefix="/internal/mcp", tags=["internal"])


async def _task_start_background(db: Session, payload: Dict[str, Any],
                                 lab_id: Optional[str], args: Dict[str, Any]) -> Dict[str, Any]:
    """Model-initiated background task (the gateway's task__start_background
    builtin). The recursion guard is the hard backstop behind the gateway's
    enforcement-by-absence: a background run's gateway doesn't even register
    the task tools, but a hand-crafted call must ALSO be refused — unbounded
    task-spawning trees are the failure mode."""
    if payload.get("is_background"):
        return {"error": "Een achtergrondtaak mag zelf geen nieuwe achtergrondtaken starten."}
    thread_id = (payload.get("thread_id") or "").strip()
    prompt = str(args.get("prompt") or "").strip()
    if not lab_id or not thread_id:
        return {"error": "task__start_background vereist een lab- en threadgebonden chatsessie."}
    if not prompt:
        return {"error": "task__start_background vereist een niet-lege prompt."}
    from models.thread import Thread
    from routers.chat_router import _history_for_prompt
    from services.agent import background_runs
    t = db.get(Thread, thread_id)
    if not t:
        return {"error": f"Thread {thread_id} niet gevonden."}
    history = _history_for_prompt(db, thread_id)
    history.append({"role": "user", "content": prompt})
    run = background_runs.start(db, thread_id=thread_id, lab_id=lab_id,
                                history=history, prompt=prompt,
                                model=t.model, effort=t.effort)
    return {"result": (f"Achtergrondtaak gestart (id {run.id[:8]}). De taak draait zelfstandig "
                       f"verder; controleer de voortgang later met task__check_background.")}


def _task_check_background(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    thread_id = (payload.get("thread_id") or "").strip()
    if not thread_id:
        return {"error": "task__check_background vereist een threadgebonden chatsessie."}
    from models.background_run import BackgroundRun
    rows = (db.query(BackgroundRun)
            .filter(BackgroundRun.thread_id == thread_id)
            .order_by(BackgroundRun.created_at.desc()).limit(10).all())
    if not rows:
        return {"result": "Geen achtergrondtaken voor dit gesprek."}
    lines = []
    for r in rows:
        line = (f"- {r.id[:8]} [{r.status}] {len(r.steps or [])} stappen — "
                f"{(r.prompt or '')[:80]}")
        if r.status == "completed" and r.answer:
            line += f"\n  Resultaat: {r.answer[:600]}"
        elif r.error:
            line += f"\n  Fout: {r.error[:300]}"
        lines.append(line)
    return {"result": "\n".join(lines)}


def _board_tool(db: Session, tool_name: str, lab_id: Optional[str],
                args: Dict[str, Any]) -> Dict[str, Any]:
    """De board__*-builtins uit de gateway. Het bord volgt uit het lab waar de
    run aan hangt — de agent kiest dus nooit zelf een ander bord."""
    from services.boards.board_service import BoardService
    if not lab_id:
        return {"error": "Board-tools vereisen een labgebonden run."}
    svc = BoardService(db)
    board = svc.board_for_lab(lab_id)
    if board is None:
        return {"error": "Aan dit lab hangt geen board."}

    def _resolve(key: str):
        ticket = svc.ticket_by_key(board.id, key)
        if ticket is None:
            raise ValueError(f"Ticket '{key}' bestaat niet op board '{board.name}'.")
        return ticket

    try:
        if tool_name == "board__list_tickets":
            limit = int(args.get("limit") or 50)
            rows = svc.list_tickets(board.id, status=args.get("status") or None, limit=limit)
            if not rows:
                return {"result": "Geen tickets gevonden."}
            lines = [f"Board '{board.name}' — {len(rows)} ticket(s):"]
            for t in rows:
                lines.append(f"- {t.key} [{t.status}] ({t.priority}) {t.title}"
                             + (f" — extern: {t.external_key}" if t.external_key else ""))
            return {"result": "\n".join(lines)}

        if tool_name == "board__get_ticket":
            t = _resolve(str(args.get("key") or ""))
            lines = [f"{t.key} — {t.title}",
                     f"Kolom: {t.status} | Prioriteit: {t.priority}"
                     + (f" | Toegewezen: {t.assignee}" if t.assignee else ""),
                     f"Labels: {', '.join(str(x) for x in (t.labels or [])) or '-'}"]
            if t.external_key:
                lines.append(f"Extern: {t.external_provider} {t.external_key} {t.external_url or ''}")
            lines += ["", "Omschrijving (de opdracht):", (t.description or "(leeg)")]
            lines += ["", "Acceptatiecriteria:", (t.acceptance_criteria or "(nog niet ingevuld)")]
            comments = svc.list_comments(t.id)
            if comments:
                lines += ["", "Opmerkingen:"]
                for c in comments:
                    lines.append(f"- [{c.kind}/{c.author}] {(c.body or '')[:1000]}")
            return {"result": "\n".join(lines)}

        if tool_name == "board__create_ticket":
            t = svc.create_ticket(board.id, {
                "title": args.get("title"), "description": args.get("description"),
                "acceptance_criteria": args.get("acceptance_criteria"),
                "status": args.get("status"), "priority": args.get("priority"),
                "labels": args.get("labels"),
            }, author="agent")
            return {"result": f"Ticket {t.key} aangemaakt in kolom '{t.status}'."}

        if tool_name == "board__update_ticket":
            t = _resolve(str(args.get("key") or ""))
            payload = {k: v for k, v in args.items()
                       if k in ("title", "description", "acceptance_criteria", "status",
                                "priority", "assignee", "labels")
                       and v is not None}
            if not payload:
                return {"error": "Geef minstens één veld op om bij te werken."}
            updated = svc.update_ticket(t.id, payload, author="agent")
            return {"result": f"{updated.key} bijgewerkt ({', '.join(payload)}); "
                              f"kolom is nu '{updated.status}'."}

        if tool_name == "board__comment_ticket":
            t = _resolve(str(args.get("key") or ""))
            body = str(args.get("body") or "").strip()
            if not body:
                return {"error": "body mag niet leeg zijn."}
            svc.add_comment(t.id, body=body, author="agent")
            return {"result": f"Opmerking geplaatst op {t.key}."}
    except ValueError as exc:
        return {"error": str(exc)}
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    return {"error": f"Onbekende board-tool: {tool_name}"}


def _root_error(exc: BaseException) -> BaseException:
    """The mcp SDK's client context managers wrap a tool failure in (nested)
    ExceptionGroups on exit, so str(exc) is the useless 'unhandled errors in
    a TaskGroup (1 sub-exception)' — the actual, actionable message (e.g. a
    pydantic validation error naming the wrong argument) is the innermost
    leaf. Unwrap to that leaf before showing anything to the agent."""
    seen = 0
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions and seen < 10:
        exc = exc.exceptions[0]
        seen += 1
    return exc


@router.post("/execute")
async def execute(payload: Dict[str, Any], x_labx_internal_token: Optional[str] = Header(default=None)):
    if not x_labx_internal_token or x_labx_internal_token != INTERNAL_MCP_TOKEN:
        raise HTTPException(status_code=403, detail="Ongeldig intern token")
    lab_id = payload.get("lab_id")
    args = payload.get("args") or {}
    db: Session = SessionLocal()
    try:
        svc = ToolExecutionService(db)
        if payload.get("tool_name") == "lab__shell_exec":
            if not lab_id:
                raise HTTPException(status_code=400, detail="lab_id is verplicht voor lab__shell_exec")
            result = await svc.execute_builtin_shell(
                lab_id=lab_id, command=str(args.get("command") or ""),
                timeout=float(args.get("timeout") or 60))
            return {"result": result.get("output")}
        if payload.get("tool_name") == "lab__write_file":
            if not lab_id:
                raise HTTPException(status_code=400, detail="lab_id is verplicht voor lab__write_file")
            from services.lab.lab_service import LabService
            try:
                res = await LabService(db).write_file(
                    lab_id, str(args.get("path") or ""), str(args.get("content") or ""))
            except HTTPException as exc:
                return {"error": f"lab__write_file mislukt: {exc.detail}"}
            return {"result": f"Geschreven: {res['path']} ({res['bytes']} bytes)"}
        if payload.get("tool_name") == "task__start_background":
            return await _task_start_background(db, payload, lab_id, args)
        if payload.get("tool_name") == "task__check_background":
            return _task_check_background(db, payload)
        if str(payload.get("tool_name") or "").startswith("board__"):
            return _board_tool(db, str(payload["tool_name"]), lab_id, args)
        tool_id = payload.get("tool_id")
        if tool_id is None:
            raise HTTPException(status_code=400, detail="tool_id of tool_name is verplicht")
        try:
            result = await svc.execute_tool(int(tool_id), args, lab_id=lab_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            # Return the tool's real error MESSAGE instead of a bare 500: the
            # agent must be able to read "skill_uid: Missing required
            # argument" and fix its own call — an opaque "500 Internal Server
            # Error" reads as a broken server and makes it give up entirely
            # (which is exactly what happened with Nectar's skill_get: the
            # agent guessed parameter name `uid`, the validation error naming
            # the right field never reached it, and it reported the server
            # as systemically down).
            return {"error": str(_root_error(exc))[:4000]}
        return {"result": result}
    finally:
        db.close()
