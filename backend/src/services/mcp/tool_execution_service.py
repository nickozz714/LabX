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

    def _lab_container_id(self, lab_id: Optional[str],
                          worker_id: Optional[int] = None) -> Optional[str]:
        """De container waarin een lab-gebonden MCP-server moet draaien. Mét
        werker: die van deze run — een browser die bij run X hoort mag niet in
        de container van run Y opengaan."""
        lab = self._lab(lab_id)
        if not lab or lab.status != "running":
            return None
        if worker_id:
            from services.lab.lab_service import LabService
            return LabService(self.db).container_for(lab, worker_id)
        return lab.container_id

    async def _second_opinion(self, *, lab, guarded: Dict[str, Any]) -> Dict[str, Any]:
        """De DERDE laag: het lokale model.

        De regels vinden patronen — een BSN, een IBAN, een SELECT. Wat ze
        principieel niet kunnen zien is een AFGELEID AGGREGAAT: "PostNL 1142
        zendingen, DHL 738, gemiddeld 19,4 kg" bevat geen enkel patroon en is
        wél klantdata. Dat is precies waarvoor een klein lokaal model bestaat,
        en daarom is dit geen optioneel extraatje maar de laag die het gat
        dekt dat de andere twee openlaten.

        Hij draait pas als de regels de uitvoer hebben doorgelaten — dus op de
        al GEMASKEERDE tekst, niet op het origineel. Zo ziet het model nooit
        meer dan het model erna ook zou zien. Het oordeel gaat terug in het
        audit-spoor, zodat er niet in staat "doorgelaten" terwijl het model het
        alsnog tegenhield.
        """
        if not lab or not lab.llm_guard or guarded.get("guarded"):
            return guarded
        from services.lab.data_guard import GUARD_MESSAGE
        from services.lab.data_guard_llm import llm_second_opinion
        from services.lab import guard_audit_service as audit

        facts = guarded.get("guard_facts") or {}
        audit_id = facts.get("audit_id")
        opinion = await llm_second_opinion(guarded.get("output") or "")
        if opinion and not opinion.get("allowed"):
            audit.werk_bij(self.db, audit_id, outcome="geblokkeerd", geleverd="",
                           llm_verdict=opinion)
            return {
                **guarded,
                "output": GUARD_MESSAGE.format(reason=opinion.get("reason") or "lokaal model"),
                "guarded": True,
                "guard_reason": opinion.get("reason"),
                "guard_facts": {**facts, "llm": opinion, "fase": "lokaal model"},
            }
        if opinion is not None:
            audit.werk_bij(self.db, audit_id, llm_verdict=opinion)
            guarded = {**guarded, "guard_facts": {**facts, "llm": opinion}}
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

    def _audit_geheimen(self, *, tool: Tool, lab_id: Optional[str],
                        namen: list) -> None:
        """Vastleggen DAT er een geheim is ingevuld, met welke naam — nooit met
        welke waarde. Anders staat het geheim alsnog in het spoor dat juist
        bedoeld is om achteraf te kunnen kijken."""
        try:
            self.db.add(AuditTraceEvent(
                id=str(uuid4()), ts=_now_iso(), type="secret_used", level="info",
                data_json=__import__("json").dumps({
                    "lab_id": lab_id, "tool": tool.name, "secrets": list(namen),
                })))
            self.db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warningx("Geheim-gebruik niet vastgelegd", error=str(exc)[:200])

    def _maskeer_resultaat(self, result: Any, *, lab_id: Optional[str]) -> Any:
        from services.secrets.vault import VaultService
        kluis = VaultService(self.db)
        if isinstance(result, str):
            return kluis.maskeer(result, lab_id=lab_id)
        try:
            import json as _json
            tekst = _json.dumps(result, default=str)
            gemaskeerd = kluis.maskeer(tekst, lab_id=lab_id)
            return result if gemaskeerd == tekst else _json.loads(gemaskeerd)
        except Exception:  # noqa: BLE001
            return result

    async def execute_tool(self, tool_id: int, args: Dict[str, Any], *,
                           lab_id: Optional[str] = None,
                           worker_id: Optional[int] = None,
                           sessie: Optional[str] = None) -> Any:
        tool = self.db.get(Tool, tool_id)
        if not tool or not tool.is_enabled:
            raise RuntimeError(f"Tool {tool_id} niet gevonden of uitgeschakeld")
        server: Optional[MCPServer] = (
            self.db.get(MCPServer, tool.mcp_server_id) if tool.mcp_server_id else None
        )
        if not server:
            raise RuntimeError(f"Tool '{tool.name}' heeft geen MCP-server")

        # Geheimen invullen VLAK voor de aanroep. Het model schrijft
        # `{{secret:naam}}` in een argument — het kiest dus welk geheim, zonder
        # te weten wat erin zit — en hier komt de waarde er pas in. Wat er in
        # het audit-spoor en in de tool-invoer belandt, is de naam.
        from services.secrets.vault import VaultService
        kluis = VaultService(self.db)
        args, gebruikte_geheimen, onbekend = kluis.vul_in(args, lab_id=lab_id)
        if onbekend:
            # Niet stil laten staan: `{{secret:typefout}}` zou letterlijk naar
            # een externe dienst vertrekken, die er niets van begrijpt of hem
            # opslaat. Het model hoort te weten wat er wél is.
            namen = ", ".join(r.name for r in kluis.beschikbaar(lab_id)) or "geen"
            raise RuntimeError(
                f"Onbekend geheim: {', '.join(onbekend)}. Beschikbaar: {namen}. "
                f"Gebruik lab__secret_list om te zien welke er zijn.")
        if gebruikte_geheimen:
            self._audit_geheimen(tool=tool, lab_id=lab_id, namen=gebruikte_geheimen)

        cid = self._lab_container_id(lab_id, worker_id) if server.location == "lab" else None
        result = await mcp_client.call_tool(server, tool.remote_name, args, lab_container_id=cid,
                                            db=self.db, lab_id=lab_id, sessie=sessie)
        if gebruikte_geheimen:
            # De andere kant: een dienst die je sleutel terugspiegelt in een
            # foutmelding, of een tool die zijn eigen aanroep echoot.
            result = self._maskeer_resultaat(result, lab_id=lab_id)

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
            # Een tool die als control-plane is aangemerkt, verklaart daarmee
            # metadata op te halen. Dezelfde toets geldt: komt er een dataset
            # uit, dan gaat het alsnog dicht.
            intent=("metadata" if provenance == "control" else None),
            db=self.db, lab_name=(lab.name if lab else None),
            command=f"mcp:{server.slug}:{tool.remote_name}",
        )
        guarded = await self._second_opinion(lab=lab, guarded=guarded)
        self._audit(lab_id=lab_id, command=f"mcp:{server.slug}:{tool.remote_name}", guarded_result=guarded)
        if guarded.get("guarded"):
            return guarded["output"]
        return result

    async def execute_builtin_shell(self, *, lab_id: str, command: str,
                                    timeout: float = 60.0,
                                    worker_id: Optional[int] = None,
                                    intent: Optional[str] = None) -> Dict[str, Any]:
        """The one always-available in-lab tool: run a shell command in the
        bound lab, guarded. This is the chokepoint the chat agent's
        `lab__shell_exec` gateway tool routes through."""
        from services.lab.data_guard import guard_lab_output
        from services.lab.lab_service import LabService

        svc = LabService(self.db)
        # Staat het lab uit (of is het na een tijd stilte vanzelf gestopt), dan
        # eerst aanzetten: de agent hoort niet stuk te lopen op iets dat hij zelf
        # kan oplossen.
        await svc.ensure_running(lab_id)
        result = await svc.exec_command(lab_id, command, timeout=timeout, worker_id=worker_id)
        lab = self._lab(lab_id)
        guarded = guard_lab_output(result, enabled=bool(lab.data_guard) if lab else True,
                                   command=command, lab_id=lab_id, db=self.db,
                                   lab_name=(lab.name if lab else None),
                                   worker_id=worker_id, intent=intent)
        guarded = await self._second_opinion(lab=lab, guarded=guarded)
        self._audit(lab_id=lab_id, command=command, guarded_result=guarded)
        return guarded
