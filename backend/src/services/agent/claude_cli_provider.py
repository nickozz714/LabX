"""
services/agent/claude_cli_provider.py

Runs the Claude Code CLI in headless agentic mode (`claude -p
--permission-mode bypassPermissions`). Ported and slimmed down from
ND3X-public/src/services/providers/claude_code_provider.py: LabX has no
"plain-chat/planner-brain" mode and no provider registry — chat ALWAYS runs
the CLI as a full autonomous agent restricted to the `mcp__labx` gateway
(issue 2's fix: tool-search stays on, so tool schemas load on demand instead
of a skill gating which tools even exist).

Auth is subscription-based: the stored "token" is the long-lived OAuth token
from `claude setup-token`, injected as CLAUDE_CODE_OAUTH_TOKEN.
ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are stripped from the subprocess env
so the CLI can't silently bill the API instead of the subscription.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from component_logging import get_logger

log = get_logger(__name__)

# Env vars stripped from the subprocess so the CLI bills the subscription
# (not an API key) and doesn't inherit a host dev session's harness.
_STRIPPED_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE")
_STRIPPED_ENV_PREFIXES = ("CLAUDE_CODE_",)

# Every native CLI tool except WebSearch and Task — hard-disallowed so the
# CLI has no I/O path of its own; everything routes through mcp__labx and
# the guard. WebSearch is allowed (query -> search engine, low exfiltration
# risk); WebFetch stays closed (arbitrary URL fetch = an exfiltration
# channel). Task (subagents) is allowed: verified live (see the subagent
# smoke test run during implementation) that a Task-spawned subagent
# inherits the parent session's --disallowedTools rather than getting its
# own unrestricted tool profile — a subagent's tool list genuinely does not
# contain Bash/Edit/etc, so allowing Task does not reopen the native I/O
# path the rest of this list exists to close.
_NATIVE_TOOLS_MINUS_WEBSEARCH = "Bash,Edit,Write,NotebookEdit,Read,Glob,Grep,WebFetch,TodoWrite"

_STDOUT_LIMIT = 64 * 1024 * 1024  # a stream-json line can carry a big tool result


def claude_code_model(model: Optional[str], *, default: str) -> str:
    """Coerce a requested model to one the CLI can run; unrecognized ids fall
    back to the configured default rather than making the CLI exit."""
    m = (model or "").strip().lower()
    if m in ("fable", "opus", "sonnet", "haiku") or m.startswith("claude"):
        return model
    return default


@dataclass
class ChatResult:
    text: str
    session_id: str
    raw: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, Any] = field(default_factory=dict)


class ClaudeCliProvider:
    def __init__(
        self,
        *,
        default_model: str,
        oauth_token: Optional[str] = None,
        cli_path: str = "claude",
        max_turns: int = 250,
        timeout: Optional[float] = None,
        extra_args: Optional[List[str]] = None,
        enable_tool_search: bool = True,
        mcp_config_path: Optional[str] = None,
        fallback_model: Optional[str] = None,
        max_budget_usd: Optional[float] = None,
        autocompact: Optional[str] = None,
        custom_agents_json: Optional[str] = None,
        default_agent: Optional[str] = None,
    ):
        self._default_model = default_model
        # Strip all whitespace: a pasted token can carry an embedded newline.
        self._oauth_token = re.sub(r"\s", "", oauth_token or "") or None
        self._cli_path = (cli_path or "claude").strip() or "claude"
        self._max_turns = max_turns
        self._timeout = float(timeout) if timeout else 7200.0  # skill-driven runs take an hour+
        self._extra_args = list(extra_args or [])
        self._enable_tool_search = enable_tool_search
        self._mcp_config_path = mcp_config_path
        self._fallback_model = (fallback_model or "").strip() or None
        self._max_budget_usd = max_budget_usd
        self._autocompact = (autocompact or "").strip() or None
        self._custom_agents_json = (custom_agents_json or "").strip() or None
        self._default_agent = (default_agent or "").strip() or None

    def _build_env(self) -> Dict[str, str]:
        env = {
            k: v for k, v in os.environ.items()
            if k not in _STRIPPED_ENV_VARS and not k.startswith(_STRIPPED_ENV_PREFIXES)
        }
        if self._oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self._oauth_token
        # --permission-mode bypassPermissions as root requires IS_SANDBOX; the
        # backend container runs as root and the CLI's own tools are disabled
        # anyway (nd3x/labx-only I/O path), so this is safe here.
        if env.get("IS_SANDBOX") is None and hasattr(os, "geteuid") and os.geteuid() == 0:
            env["IS_SANDBOX"] = "1"
        if self._enable_tool_search:
            env.setdefault("ENABLE_TOOL_SEARCH", "true")
        return env

    def _build_cmd(self, model_id: str, instructions: Optional[str],
                   resume_session_id: Optional[str] = None, *,
                   effort: Optional[str] = None,
                   json_schema: Optional[str] = None) -> List[str]:
        cmd = [self._cli_path, "-p", "--model", model_id]
        if resume_session_id:
            cmd += ["--resume", resume_session_id]
        if instructions:
            cmd += ["--append-system-prompt", instructions]
        cmd += ["--permission-mode", "bypassPermissions"]
        cmd += ["--allowedTools", "mcp__labx WebSearch Task"]
        cmd += ["--disallowedTools", _NATIVE_TOOLS_MINUS_WEBSEARCH]
        if self._max_turns:
            cmd += ["--max-turns", str(int(self._max_turns))]
        if effort:
            cmd += ["--effort", effort]
        if self._fallback_model:
            cmd += ["--fallback-model", self._fallback_model]
        if self._max_budget_usd:
            cmd += ["--max-budget-usd", str(self._max_budget_usd)]
        if self._autocompact:
            cmd += ["--autocompact", self._autocompact]
        if self._custom_agents_json:
            cmd += ["--agents", self._custom_agents_json]
        if self._default_agent:
            cmd += ["--agent", self._default_agent]
        if json_schema:
            cmd += ["--json-schema", json_schema]
        if self._mcp_config_path:
            cmd += ["--mcp-config", self._mcp_config_path]
            # Only ever load the labx gateway's MCP server — never a stray
            # entry from the operator's own global ~/.claude.json (mounted in
            # via labx_claude_home so CLI session state survives a restart).
            # Without this, a host-account MCP server the operator has
            # configured for their own everyday Claude Code use would be
            # silently available to every lab-bound chat too, bypassing the
            # mcp__labx gateway + data-guard entirely.
            cmd += ["--strict-mcp-config"]
        cmd += self._extra_args
        return cmd

    async def _spawn(self, cmd: List[str]) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
                limit=_STDOUT_LIMIT,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Claude Code CLI niet gevonden ('{self._cli_path}') — installeer "
                "@anthropic-ai/claude-code of zet cli_path in Instellingen."
            ) from exc

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        try:
            proc.kill()
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _parse_result(stdout: bytes) -> Dict[str, Any]:
        text = (stdout or b"").decode("utf-8", errors="replace").strip()
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            pass
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(obj, dict) and obj.get("type") == "result":
                return obj
        raise RuntimeError(f"Claude Code output niet parsebaar als JSON: {text[:400]!r}")

    async def chat(self, prompt: str, *, model: Optional[str] = None,
                   instructions: Optional[str] = None,
                   resume_session_id: Optional[str] = None) -> ChatResult:
        model_id = model or self._default_model
        cmd = self._build_cmd(model_id, instructions, resume_session_id) + ["--output-format", "json"]
        proc = await self._spawn(cmd)
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=self._timeout)
        except asyncio.TimeoutError:
            await self._kill(proc)
            raise RuntimeError(f"Claude Code run overschreed de timeout ({self._timeout:.0f}s)")
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            try:
                data = self._parse_result(stdout)
                detail = f"{data.get('subtype')}: {str(data.get('result') or '')[:400]}"
            except Exception:  # noqa: BLE001
                out = (stdout or b"").decode("utf-8", errors="replace").strip()
                detail = err[-400:] or out[-400:] or "geen stderr/stdout"
            raise RuntimeError(f"Claude Code CLI faalde (exit {proc.returncode}): {detail}")
        data = self._parse_result(stdout)
        if data.get("is_error"):
            raise RuntimeError(f"Claude Code gaf een fout terug ({data.get('subtype')}): "
                               f"{str(data.get('result') or '')[:400]}")
        return ChatResult(text=str(data.get("result") or ""),
                          session_id=str(data.get("session_id") or ""),
                          raw=data, usage=data.get("usage") or {})

    async def chat_stream_events(self, prompt: str, *, model: Optional[str] = None,
                                 instructions: Optional[str] = None,
                                 resume_session_id: Optional[str] = None,
                                 effort: Optional[str] = None,
                                 json_schema: Optional[str] = None,
                                 ) -> AsyncIterator[Dict[str, Any]]:
        """Typed events for the chat SSE stream:
          {"kind": "session", "id": ...}     Claude Code session id to persist
          {"kind": "thinking", "text": ...}  interim assistant text alongside a tool step
          {"kind": "tool", "name": ..., "input": ...}  a tool the agent called
          {"kind": "delta", "text": ...}     an incremental answer-text chunk
          {"kind": "answer", "text": ...}    the final answer
        """
        model_id = model or self._default_model
        cmd = self._build_cmd(model_id, instructions, resume_session_id,
                              effort=effort, json_schema=json_schema) + [
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            # Without this, a Task subagent's own work is entirely invisible
            # until its final tool_result lands back in the parent — with it,
            # the subagent's text/thinking stream through as regular
            # assistant events (tagged with parent_tool_use_id) so the chat
            # UI's live step list actually shows what a subagent is doing.
            "--forward-subagent-text"]
        proc = await self._spawn(cmd)
        answer_fallback: List[str] = []
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise RuntimeError(f"Claude Code stream overschreed de timeout ({self._timeout:.0f}s)")
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                otype = obj.get("type")
                if otype == "system":
                    sid = obj.get("session_id")
                    if sid:
                        yield {"kind": "session", "id": str(sid)}
                    continue
                if otype == "stream_event":
                    # --include-partial-messages wraps raw Anthropic API
                    # streaming events here (verified against the live CLI —
                    # this shape isn't documented in `claude --help`, only in
                    # the actual stream-json output): a text content block
                    # streams as message_start -> content_block_start ->
                    # repeated content_block_delta{delta:{type:text_delta}}
                    # -> content_block_stop -> message_delta -> message_stop,
                    # with the same full text also arriving afterwards as a
                    # whole "assistant" event (handled below) — deltas are a
                    # purely additive live-preview signal, never the only
                    # source of the final text.
                    inner = obj.get("event") or {}
                    if inner.get("type") == "content_block_delta":
                        delta = inner.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            text = delta.get("text") or ""
                            if text:
                                yield {"kind": "delta", "text": text}
                    continue
                if otype == "assistant":
                    content = (obj.get("message") or {}).get("content") or []
                    has_tool = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text":
                            text = b.get("text") or ""
                            if not text:
                                continue
                            if has_tool:
                                yield {"kind": "thinking", "text": text}
                            else:
                                answer_fallback.append(text)
                        elif b.get("type") == "tool_use":
                            yield {"kind": "tool", "name": b.get("name"), "input": b.get("input")}
                elif otype == "result":
                    if obj.get("is_error"):
                        raise RuntimeError(f"Claude Code gaf een fout terug ({obj.get('subtype')}): "
                                           f"{str(obj.get('result') or '')[:400]}")
                    sid = obj.get("session_id")
                    if sid:
                        yield {"kind": "session", "id": str(sid)}
                    usage = obj.get("usage") or {}
                    yield {"kind": "usage",
                          "input_tokens": (usage.get("input_tokens") or 0)
                          + (usage.get("cache_read_input_tokens") or 0)
                          + (usage.get("cache_creation_input_tokens") or 0),
                          "output_tokens": usage.get("output_tokens") or 0,
                          "cost_usd": obj.get("total_cost_usd"),
                          "duration_ms": obj.get("duration_ms"),
                          "num_turns": obj.get("num_turns")}
                    answer = obj.get("result")
                    if isinstance(answer, str):
                        answer_text = answer
                    elif answer is not None:
                        # --json-schema mode: `result` is the structured JSON
                        # object itself, not text — surface it as JSON rather
                        # than silently falling back to (empty) accumulated
                        # plain-text blocks, which is what schema mode
                        # produces none of.
                        answer_text = json.dumps(answer)
                    else:
                        answer_text = "".join(answer_fallback)
                    yield {"kind": "answer", "text": answer_text}

            rc = await proc.wait()
            if rc != 0:
                errtext = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
                raise RuntimeError(f"Claude Code CLI faalde (exit {rc}): {errtext.strip()[-800:]}")
        finally:
            if proc.returncode is None:
                await self._kill(proc)
