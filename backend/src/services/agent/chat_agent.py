"""
services/agent/chat_agent.py

Runs one chat turn as a full autonomous Claude Code agent bound to a lab.
Ported from ND3X-public/src/services/assistants/claude_code_chat_agent.py,
simplified: LabX has exactly one agent runtime (no provider registry, no
"Agent mode vs orchestration" split) — every chat turn takes this path,
which is the fix for issue 2 (tool-search stays on; skills contribute
how-to guidance, never gate which tools exist).
"""
from __future__ import annotations

import os
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from services.agent.claude_cli_provider import ClaudeCliProvider, claude_code_model
from services.lab.execution_context import reset_active_lab, set_active_lab

log = get_logger(__name__)

AGENT_PREAMBLE = (
    "You are the LabX assistant, running as an autonomous agent bound to one "
    "Docker lab. LabX's own capabilities — the lab's shell, any registered MCP "
    "servers (host-side or running inside the lab), and skills — are exposed "
    "to you as tools prefixed `mcp__labx__`. Your own built-in tools (Bash, "
    "Read, Edit, WebFetch) are disabled; the ONLY way to read or execute "
    "anything is through those `mcp__labx__` tools, so that all I/O passes "
    "through the data-egress guard.\n\n"
    "The lab container IS your sandbox, and `lab__shell_exec` gives you a "
    "full root shell in it. That means:\n"
    "- You MAY install any tool you're missing (`apt-get install`, `pip "
    "install`, `npm install`, download binaries) — being able to shape the "
    "environment is the point of the sandbox. Base tools (git, curl, jq, "
    "unzip, python, pip) are pre-installed.\n"
    "- You CAN call external REST APIs directly from the lab with curl/"
    "python (the lab has network access unless its settings say otherwise). "
    "A predefined MCP tool is a convenience, never the only path: if no tool "
    "covers an API endpoint you need, call the endpoint yourself from the "
    "shell.\n"
    "- If the lab has an assigned Azure identity, an `az` CLI session is "
    "synced into the container: `az account get-access-token --resource "
    "<resource-url>` mints tokens for Azure/Fabric/Power BI REST APIs. Check "
    "with `az account show` before assuming it's absent.\n"
    "- Large files (API specs, datasets) are best downloaded INTO the lab "
    "and inspected there with shell tools (jq/grep/python) instead of pulled "
    "through your context window.\n\n"
    "Installing skills/scripts into the lab: org skills (e.g. from Nectar via "
    "`skill_get`) often ship FILES — a SKILL.md plus scripts like a Python "
    "client. USE them, don't just read them: write each file into the lab "
    "with `lab__write_file` (convention: /workspace/.skills/<SkillName>/...), "
    "install any dependencies the SKILL.md names (pip/apt via the shell), and "
    "then run the scripts from the shell as the skill describes. Once "
    "installed they persist on the lab's volume — check whether a skill is "
    "already present before re-installing. The same applies to workflows or "
    "any other supporting scripts you need: the lab is yours to provision.\n\n"
    "Background tasks: when work will CLEARLY take minutes or longer (waiting "
    "on a pipeline/job to finish, monitoring something, a large batch run), "
    "use `task__start_background` with a complete, self-contained prompt — "
    "the task runs as an independent agent with the same tools and the "
    "conversation context, and its result lands in this chat automatically "
    "when done. Answer the user IMMEDIATELY with what you started and the "
    "task id; do not wait for it. Use `task__check_background` when the user "
    "asks about progress or when you need a task's result. Do NOT background "
    "quick work that finishes within a minute — just do that directly.\n\n"
    "There can be many `mcp__labx__` tools and they load on demand: use tool "
    "search to find the right one by name or purpose, then call it directly. "
    "You never need a skill to be pre-selected to reach a tool — every "
    "enabled tool is already available to you; skills below are guidance on "
    "HOW to use specific tools, not a gate on WHETHER you may.\n\n"
    "Chain tool calls as needed to reach a complete result, and never ask the "
    "user to approve a tool run — just do the work with the tools you have. "
    "Answer in the same language the user used.\n\n"
    "When you have done the work, give the user a clear, direct answer in "
    "natural language — that final message is what LabX shows in the chat."
)

_SKILL_GUIDE_BUDGET = 16_000


class ChatAgent:
    def __init__(self, db: Session):
        self.db = db

    def _enabled_domain_skill_names(self) -> List[str]:
        from models.skill import Skill
        rows = (self.db.query(Skill)
                .filter(Skill.is_enabled == True, Skill.is_system == False)  # noqa: E712
                .order_by(Skill.name.asc()).all())
        return [s.name for s in rows if s.name]

    def _skill_instructions_block(self, skill_names: List[str]) -> str:
        """How-to guidance for enabled skills, including each linked tool's
        per-tool instructions (issue 3: instructions collected per tool in the
        Skill Wizard land here, alongside the tool's own description)."""
        if not skill_names:
            return ""
        from models.skill import Skill
        from models.skill_tool import SkillTool
        from models.tool import Tool

        rows = self.db.query(Skill).filter(Skill.name.in_(skill_names)).all()
        parts: List[str] = []
        used = 0
        truncated = False
        for s in rows:
            block_lines = [f"### Skill: {s.name}"]
            if (s.instructions or "").strip():
                block_lines.append(s.instructions.strip())
            links = (self.db.query(SkillTool, Tool)
                    .join(Tool, Tool.id == SkillTool.tool_id)
                    .filter(SkillTool.skill_id == s.id, SkillTool.is_enabled == True)  # noqa: E712
                    .all())
            for link, tool in links:
                if link.instructions and link.instructions.strip():
                    block_lines.append(f"- Tool `{tool.name}`: {link.instructions.strip()}")
            block = "\n".join(block_lines)
            if used + len(block) > _SKILL_GUIDE_BUDGET:
                truncated = True
                continue
            parts.append(block)
            used += len(block)
        if not parts:
            return ""
        head = ("LabX skill guidance — how to use specific mcp__labx tools. "
               "Discover a tool's schema with tool search, then call it directly:")
        if truncated:
            head += "\n(Some skills' guidance was omitted to stay within budget.)"
        return head + "\n\n" + "\n\n".join(parts)

    def _build_provider(self, model: Optional[str], mcp_config_path: Optional[str], settings) -> ClaudeCliProvider:
        cc_model = claude_code_model(model or settings.default_model, default=settings.default_model)
        return ClaudeCliProvider(
            default_model=cc_model,
            oauth_token=settings.oauth_token,
            cli_path=settings.cli_path,
            max_turns=settings.max_turns,
            timeout=settings.timeout_seconds,
            extra_args=settings.extra_args,
            enable_tool_search=settings.enable_tool_search,
            mcp_config_path=mcp_config_path,
            fallback_model=settings.fallback_model,
            max_budget_usd=settings.max_budget_usd,
            autocompact=settings.autocompact,
            custom_agents_json=settings.custom_agents_json,
            default_agent=settings.default_agent,
        ), cc_model

    def _prompt_from_history(self, user_input: Any) -> str:
        """A plain string is the turn verbatim; a message list is flattened
        with the last user turn marked as the current request (older turns as
        already-handled context) — same anti-repeat rule as ND3X."""
        if isinstance(user_input, str):
            return user_input
        msgs = list(user_input or [])
        if not msgs:
            return ""

        def _flatten(ms: List[Dict[str, Any]]) -> str:
            lines = []
            for m in ms:
                role = (m.get("role") or "user").strip().lower()
                text = m.get("content") or ""
                label = {"assistant": "Assistant", "system": "[system]"}.get(role, "User")
                lines.append(f"{label}:\n{text}")
            return "\n\n".join(lines)

        last_idx = next((i for i in range(len(msgs) - 1, -1, -1)
                         if (msgs[i].get("role") or "user").strip().lower() == "user"), None)
        if last_idx is None:
            return _flatten(msgs)
        current = _flatten([msgs[last_idx]])
        if current.startswith("User:\n"):
            current = current[len("User:\n"):]
        history = msgs[:last_idx]
        if not history:
            return current
        return (
            "## Conversation so far — context only. These turns are ALREADY "
            "handled; do NOT repeat or re-run any of their actions.\n\n"
            f"{_flatten(history)}\n\n"
            "## Current request — the ONLY thing to act on now:\n\n"
            f"{current}"
        )

    @staticmethod
    def _latest_user_text(user_input: Any) -> str:
        if isinstance(user_input, str):
            return user_input
        msgs = list(user_input or [])
        for m in reversed(msgs):
            if (m.get("role") or "user").strip().lower() == "user":
                return str(m.get("content") or "")
        return ""

    async def _auto_hook_blocks(self, *, settings, user_input: Any) -> List[Dict[str, Any]]:
        """The 'automatische hook' feature, multi-hook edition: run every
        configured+enabled hook tool BEFORE the model sees the turn and
        inject each result — recall is not left to the model's discretion.
        Returns [{name, block, chars, error}] so the caller can BOTH extend
        the instructions and emit a visible step per hook ('je ziet niet dat
        er keihard iets gedaan wordt met de hook' — nu wel)."""
        results: List[Dict[str, Any]] = []
        hooks = [h for h in (settings.auto_hooks or [])
                 if isinstance(h, dict) and h.get("enabled", True) and (h.get("tool_name") or "").strip()]
        if not hooks:
            return results
        from models.tool import Tool
        from services.mcp.tool_execution_service import ToolExecutionService
        for h in hooks:
            name = h["tool_name"].strip()
            tool = (self.db.query(Tool)
                    .filter(Tool.name == name, Tool.is_enabled == True)  # noqa: E712
                    .first())
            if not tool:
                log.warningx("auto-hook tool niet gevonden", tool=name)
                results.append({"name": name, "block": "", "chars": 0,
                                "error": "tool niet gevonden of uitgeschakeld"})
                continue
            query = (h.get("query_template") or "").strip() or self._latest_user_text(user_input)
            try:
                result = await ToolExecutionService(self.db).execute_tool(tool.id, {"query": query})
            except Exception as exc:  # noqa: BLE001 — a hook must never break the turn
                log.warningx("auto-hook mislukt", tool=name, error=str(exc)[:200])
                results.append({"name": name, "block": "", "chars": 0, "error": str(exc)[:200]})
                continue
            text = result if isinstance(result, str) else str(result)
            block = f"## Automatische context ({name})\n{text}"
            if (h.get("instruction") or "").strip():
                block += f"\n\n{h['instruction'].strip()}"
            results.append({"name": name, "block": block, "chars": len(text), "error": None})
        return results

    async def _prepare(self, *, lab_id: str, model: Optional[str], user_input: Any = None,
                       thread_id: Optional[str] = None, is_background: bool = False):
        mcp_config_path = self._write_gateway_config(lab_id, thread_id=thread_id,
                                                     is_background=is_background)
        from services.settings_service import get_settings
        settings = get_settings(self.db)
        provider, cc_model = self._build_provider(model, mcp_config_path, settings)
        effort = settings.default_effort
        instructions = AGENT_PREAMBLE
        from services.lab.governed_policy import PLANNER_POLICY
        instructions += "\n\n" + PLANNER_POLICY
        domain_skills = self._enabled_domain_skill_names()
        skills_block = self._skill_instructions_block(domain_skills)
        if skills_block:
            instructions += "\n\n" + skills_block
        hook_results = await self._auto_hook_blocks(settings=settings, user_input=user_input)
        for h in hook_results:
            if h["block"]:
                instructions += "\n\n" + h["block"]
        return provider, instructions, mcp_config_path, cc_model, effort, hook_results

    @staticmethod
    def _write_gateway_config(lab_id: str, *, thread_id: Optional[str] = None,
                              is_background: bool = False) -> Optional[str]:
        import json
        import tempfile
        from services.mcp.gateway import mcp_config_for_cli
        try:
            fd, path = tempfile.mkstemp(prefix="labx-mcp-", suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(mcp_config_for_cli(lab_id=lab_id, thread_id=thread_id,
                                             is_background=is_background), f)
            return path
        except Exception as exc:  # noqa: BLE001 — run still proceeds, just tool-less
            log.warningx("MCP-gateway config schrijven mislukt", error=str(exc))
            return None

    async def run_stream_events(self, *, lab_id: str, user_input: Any,
                                model: Optional[str] = None,
                                resume_session_id: Optional[str] = None,
                                effort: Optional[str] = None,
                                json_schema: Optional[str] = None,
                                thread_id: Optional[str] = None,
                                is_background: bool = False,
                                ) -> AsyncIterator[Dict[str, Any]]:
        provider, instructions, mcp_config_path, cc_model, default_effort, hook_results = await self._prepare(
            lab_id=lab_id, model=model, user_input=user_input,
            thread_id=thread_id, is_background=is_background)
        # With a live --resume, the CLI session ALREADY holds the prior turns
        # — re-sending the flattened history block on top would double the
        # context every beurt (observed: it was a large chunk of a 175k-token
        # turn). Resume → only the new message; fresh session (no id, or the
        # resume-fallback below) → the full flattened history, which is then
        # genuinely needed.
        prompt = (self._latest_user_text(user_input) if resume_session_id
                  else self._prompt_from_history(user_input))
        # Een beurt in dit lab is gebruik, ook als de agent er uiteindelijk geen
        # commando in draait: anders verloopt een lab waarin net een uur is
        # gewerkt alsnog, omdat alleen exec en bestandsacties meetelden.
        _mark_lab_used(lab_id)
        token = set_active_lab(lab_id)
        got_real_progress = False
        # Make hook execution VISIBLE: one step per hook, before the model's
        # own stream starts, so the user sees the hook actually fired.
        for h in hook_results:
            if h.get("error"):
                yield {"kind": "thinking",
                      "text": f"⚙️ Automatische hook {h['name']} mislukt: {h['error']}"}
            else:
                yield {"kind": "thinking",
                      "text": f"⚙️ Automatische hook {h['name']}: {h['chars']} tekens context geïnjecteerd"}
        try:
            try:
                async for ev in provider.chat_stream_events(
                    prompt, instructions=instructions, model=cc_model,
                    resume_session_id=resume_session_id,
                    effort=effort or default_effort, json_schema=json_schema,
                ):
                    if ev["kind"] in ("thinking", "tool", "delta", "answer"):
                        got_real_progress = True
                    yield ev
            except Exception as exc:  # noqa: BLE001
                # The CLI's own session state lives in the container's local
                # filesystem (~/.claude), NOT the durable /data volume — a
                # backend restart wipes it while Thread.cli_session_id (in the
                # database) still points at a session the CLI no longer has,
                # so `--resume` fails immediately with "No conversation found
                # with session ID: ...". Recognizable, recoverable: retry once
                # as a fresh session instead of hard-failing the whole turn —
                # but only if the failed attempt never got anywhere (no
                # tool/answer yet), so a genuine mid-turn failure still
                # surfaces instead of silently double-running tool calls.
                if resume_session_id and not got_real_progress:
                    log.warningx("CLI-resume mislukt, opnieuw als nieuwe sessie",
                                 resume_session_id=resume_session_id, error=str(exc)[:200])
                    # Fresh session: the CLI has no history anymore, so NOW
                    # the flattened conversation block is needed.
                    async for ev in provider.chat_stream_events(
                        self._prompt_from_history(user_input),
                        instructions=instructions, model=cc_model,
                        resume_session_id=None,
                        effort=effort or default_effort, json_schema=json_schema,
                    ):
                        yield ev
                else:
                    raise
        finally:
            reset_active_lab(token)
            if mcp_config_path:
                try:
                    os.unlink(mcp_config_path)
                except Exception:  # noqa: BLE001
                    pass


def _mark_lab_used(lab_id: str) -> None:
    """Best-effort: het bijhouden van gebruik mag een beurt nooit laten falen."""
    try:
        from db.database import SessionLocal
        from services.lab.lab_service import LabService
        db = SessionLocal()
        try:
            LabService(db).mark_used(lab_id)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        log.warningx("Lab-gebruik bijwerken overgeslagen", lab_id=lab_id, error=str(exc)[:200])
