# models/background_run.py
#
# A non-blocking chat task: same agent run as a normal turn
# (ChatAgent.run_stream_events), but fired via asyncio.create_task so the
# HTTP request returns immediately, tracked here so status/steps survive
# independently of any open browser tab. Runs get their OWN fresh CLI
# session (never --resume the thread's cli_session_id: the CLI's session
# file has no multi-writer safety, and the foreground chat may be using it
# at the same time). See services/agent/background_runs.py.
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base

BACKGROUND_RUN_STATUSES = ("running", "completed", "failed", "cancelled", "interrupted")


class BackgroundRun(Base):
    __tablename__ = "background_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # "background" = a task the chat fired off non-blocking; "foreground" = a
    # normal chat turn. Foreground turns run through this same table since
    # the fix for "wegnavigeren stopt de stream": execution is server-side
    # and any client can (re)attach to the live event stream at any moment.
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="background")
    # Same shape as Message.steps: a list of {kind, ...} chat events,
    # appended live as the run progresses.
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cli_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Set once the completed run is persisted as a normal assistant Message
    # in the thread transcript.
    message_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_background_runs_thread", "thread_id"),
        Index("idx_background_runs_status", "status"),
    )
