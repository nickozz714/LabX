# models/audit.py — append-only trace of guard decisions & tool executions,
# ported in spirit from ND3X's AuditTraceEvent. Queried by the guard-audit
# endpoint (per lab, CSV-exportable).
from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class AuditTraceEvent(Base):
    __tablename__ = "audit_trace_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "lab_guard_exec"
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (Index("idx_audit_type_ts", "type", "ts"),)

    def to_dict(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.data_json or "{}")
        except Exception:  # noqa: BLE001
            data = {}
        return {"ts": self.ts, "level": self.level, "type": self.type, "data": data}
