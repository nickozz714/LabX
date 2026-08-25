# models/schedule.py — cron-driven runs against a lab: a raw prompt, a
# workflow, or board work the agent picks up. Evaluated by
# services/scheduling/cron.py (croniter), ticked by the same scheduler that
# runs the lab TTL-reaper.
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

# prompt   — voer deze prompt uit tegen het lab
# workflow — voer de stappen van een workflow uit tegen het lab
# board    — pak de bovenste N tickets uit de agent-kolom van een board op
SCHEDULE_KINDS = ("prompt", "workflow", "board")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(128), nullable=False)
    lab_id: Mapped[str] = mapped_column(String(64), ForeignKey("labs.id", ondelete="CASCADE"), nullable=False)
    # Welk soort werk deze schedule uitvoert. Afgeleid voor rijen van vóór
    # deze kolom bestond (workflow_id gezet -> "workflow", anders "prompt").
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="prompt")
    # exactly one of prompt / workflow_id / board_id is used
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)
    # kind="board": welk board, uit welke kolom, en hoeveel tickets per keer.
    # De kolom is optioneel — leeg = de agent-kolom van het board zelf.
    board_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=True)
    board_column: Mapped[str | None] = mapped_column(String(64), nullable=True)
    board_max_tickets: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Optional JSON Schema (Claude Code CLI's --json-schema): when set, the
    # run's result is validated/shaped structured JSON instead of free text —
    # a schedule is exactly the "give me a machine-checkable periodic result"
    # case, unlike interactive chat.
    json_schema: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_schedules_enabled", "is_enabled"),)


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schedule_id: Mapped[int] = mapped_column(Integer, ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False)
    scheduled_for: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("idx_schedule_runs_schedule", "schedule_id"),)
