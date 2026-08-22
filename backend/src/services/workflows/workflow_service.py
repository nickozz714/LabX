"""
services/workflows/workflow_service.py

A LabX Workflow is a Markdown file describing steps the agent should
execute — NOT ND3X's DAG-of-operations model. `markdown` is the source of
truth; `steps_json` is a derived, structured view the visual step editor
reads/writes. Whichever side changed last wins on save (the router decides:
edit steps -> re-render markdown; edit markdown -> re-parse steps).

Markdown shape:

    ## Stap 1 — Korte titel
    Vrije instructietekst voor deze stap. Kan meerdere regels beslaan.

    ## Stap 2 — Volgende titel
    ...

A step heading is `## Stap <n> — <title>` (the "Stap n —" prefix is cosmetic;
parsing only requires a `## ` heading — the title is everything after it,
with an optional leading "Stap N — " stripped for round-tripping).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

_HEADING_RE = re.compile(r"^##\s+(?:Stap\s+\d+\s*[—\-:]\s*)?(.+?)\s*$", re.IGNORECASE)


def parse_markdown_to_steps(markdown: str) -> List[Dict[str, Any]]:
    """Split the markdown into ordered {title, instruction} steps."""
    lines = (markdown or "").splitlines()
    steps: List[Dict[str, Any]] = []
    current_title = None
    current_body: List[str] = []

    def _flush():
        if current_title is not None:
            steps.append({
                "title": current_title.strip(),
                "instruction": "\n".join(current_body).strip(),
            })

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            _flush()
            current_title = m.group(1)
            current_body = []
        elif current_title is not None:
            current_body.append(line)
    _flush()
    for i, s in enumerate(steps, start=1):
        s["index"] = i
    return steps


def render_steps_to_markdown(steps: List[Dict[str, Any]]) -> str:
    blocks = []
    for i, s in enumerate(steps, start=1):
        title = (s.get("title") or f"Stap {i}").strip()
        instruction = (s.get("instruction") or "").strip()
        blocks.append(f"## Stap {i} — {title}\n{instruction}".rstrip())
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def steps_as_agent_instructions(steps: List[Dict[str, Any]]) -> str:
    """Render steps as an instruction block to hand the chat agent when a
    workflow is run against a lab."""
    if not steps:
        return ""
    lines = ["Voer de volgende stappen in volgorde uit:"]
    for s in steps:
        lines.append(f"{s.get('index')}. {s.get('title')}: {s.get('instruction')}")
    return "\n".join(lines)
