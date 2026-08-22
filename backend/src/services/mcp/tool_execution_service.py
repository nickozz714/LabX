"""
services/mcp/tool_execution_service.py

Executes one Tool row's call and applies the data-guard chokepoint for
lab-origin results — the design gap the sibling-Docker validation flagged:
`data_guard.classify_command` needs a shell command string to classify
provenance, which an MCP tool call doesn't have. Every in-lab tool therefore
carries a static `provenance` label (in `Tool.annotations`), defaulting to
"data" (strict) when unset, per that validation's recommendation.

Host-located servers are NOT guarded here — same as ND3X: they run with the
backend's own identity, outside the lab, so their output is not customer
data flowing out of a container. Guarding is specifically about labs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.audit import AuditTraceEvent
from models.mcp_server import MCPServer
from models.tool import Tool
from services.mcp import mcp_client

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolExecutionService:
    def __init__(self, db: Session):
        self.db = db

    def _lab(self, lab_id: Optional[str]):
        if not lab_id:
            return None
        from models.lab import Lab
        return self.db.get(Lab, lab_id)

    def _lab_container_id(self, lab_id: Optional[str]) -> Optional[str]:
        lab = self._lab(lab_id)
        return lab.container_id if lab and lab.status == "running" else None

    async def _second_opinion(self, *, lab, guarded: Dict[str, Any]) -> Dict[str, Any]:
        """Track B: once the rules ALLOW an output, ask the local guard model
        for a second opinion (catches derived aggregates the rules can't see —
        see data_guard_llm.py). Only runs when the rules didn't already block
        and the lab has llm_guard on."""
        if not lab or not lab.llm_guard or guarded.get("guarded"):
            return guarded
        from services.lab.data_guard import GUARD_MESSAGE
        from services.lab.data_guard_llm import llm_second_opinion
        opinion = await llm_second_opinion(guarded.get("output") or "")
        if opinion and not opinion.get("allowed"):
            return {
                **guarded,
                "output": GUARD_MESSAGE.format(reason=opinion.get("reason") or "lokaal model"),
                "guarded": True,
                "guard_reason": opinion.get("reason"),
            }
        return guarded

    def _audit(self, *, lab_id: Optional[str], command: Optional[str],
              guarded_result: Dict[str, Any]) -> None:
        if not lab_id:
            return
        facts = guarded_result.get("guard_facts") or {}
        ev = AuditTraceEvent(
            id=str(uuid4()), ts=_now_iso(), type="lab_guard_exec",
            level="warning" if guarded_result.get("guarded") else "info",
            data_json=__import__("json").dumps({
                "lab_id": lab_id,
                "command": command,
                "blocked": bool(guarded_result.get("guarded")),
                "guard_reason": guarded_result.get("guard_reason"),
                "guard_facts": facts,
                "exit_code": guarded_result.get("exit_code"),
                "model_output_bytes": len(guarded_result.get("output") or ""),
            }),
        )
        self.db.add(ev)
        self.db.commit()

    async def execute_tool(self, tool_id: int, args: Dict[str, Any], *,
                           lab_id: Optional[str] = None) -> Any:
        tool = self.db.get(Tool, tool_id)
        if not tool or not tool.is_enabled:
            raise RuntimeError(f"Tool {tool_id} niet gevonden of uitgeschakeld")
        server: Optional[MCPServer] = (
            self.db.get(MCPServer, tool.mcp_server_id) if tool.mcp_server_id else None
        )
        if not server:
            raise RuntimeError(f"Tool '{tool.name}' heeft geen MCP-server")

        cid = self._lab_container_id(lab_id) if server.location == "lab" else None
        result = await mcp_client.call_tool(server, tool.remote_name, args, lab_container_id=cid,
                                            db=self.db, lab_id=lab_id)

        if server.location != "lab":
            return result  # host-side: not customer data leaving a container

        provenance = ((tool.annotations or {}).get("provenance") or "data").strip().lower()
        from services.lab.data_guard import guard_lab_output
        text_result = result if isinstance(result, str) else __import__("json").dumps(result, default=str)
        lab = self._lab(lab_id)
        guarded = guard_lab_output(
            {"exit_code": 0, "output": text_result, "truncated": False},
            enabled=bool(lab.data_guard) if lab else True, lab_id=lab_id,
            provenance_override=("control" if provenance == "control" else "data"),
        )
        guarded = await self._second_opinion(lab=lab, guarded=guarded)
        self._audit(lab_id=lab_id, command=f"mcp:{server.slug}:{tool.remote_name}", guarded_result=guarded)
        if guarded.get("guarded"):
            return guarded["output"]
        return result

    async def execute_builtin_shell(self, *, lab_id: str, command: str,
                                    timeout: float = 60.0) -> Dict[str, Any]:
        """The one always-available in-lab tool: run a shell command in the
        bound lab, guarded. This is the chokepoint the chat agent's
        `lab__shell_exec` gateway tool routes through."""
        from services.lab.data_guard import guard_lab_output
        from services.lab.lab_service import LabService

        result = await LabService(self.db).exec_command(lab_id, command, timeout=timeout)
        lab = self._lab(lab_id)
        guarded = guard_lab_output(result, enabled=bool(lab.data_guard) if lab else True,
                                   command=command, lab_id=lab_id)
        guarded = await self._second_opinion(lab=lab, guarded=guarded)
        self._audit(lab_id=lab_id, command=command, guarded_result=guarded)
        return guarded
