"""
services/lab/execution_context.py

Execution context for lab-bound runs. Ported verbatim from
ND3X-public/src/services/playground/execution_context.py. The chat-turn
runner sets the active lab id here; builtin tools consult it and route their
execution into the container. Contextvar so concurrent runs never cross
(each asyncio task inherits its own copy).
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_active_lab: ContextVar[Optional[str]] = ContextVar("labx_active_lab", default=None)


def set_active_lab(lab_id: Optional[str]) -> Token:
    return _active_lab.set(lab_id)


def reset_active_lab(token: Token) -> None:
    _active_lab.reset(token)


def active_lab_id() -> Optional[str]:
    return _active_lab.get()


# ── Data-egress session-taint ────────────────────────────────────────────────
# Once a data-plane read (customer data) is detected in a lab, that lab is
# flagged so the guard judges later — possibly aggregated — output from the
# same session more strictly. Process-global: the guard runs in the main
# process, all exec calls of a session share this state.
_data_plane_tainted: set[str] = set()


def mark_data_plane_touched(lab_id: Optional[str]) -> None:
    if lab_id:
        _data_plane_tainted.add(str(lab_id))


def is_data_plane_tainted(lab_id: Optional[str]) -> bool:
    return bool(lab_id) and str(lab_id) in _data_plane_tainted


def clear_data_plane_taint(lab_id: Optional[str]) -> None:
    _data_plane_tainted.discard(str(lab_id or ""))
