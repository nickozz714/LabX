# models/thread.py
#
# A chat thread is bound to a Lab from the moment it's created — LabX's chat
# has no "unbound" mode (unlike ND3X, where a thread can run the planner
# without a playground). `lab_id` is therefore NOT NULL by design: the router
# refuses to create a thread without one.
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Nieuwe chat")
    lab_id: Mapped[str] = mapped_column(String(64), ForeignKey("labs.id", ondelete="CASCADE"), nullable=False)
    # Claude Code CLI session id, for --resume across turns in this thread.
    cli_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Per-chat overrides (null = fall back to the global default from
    # Settings). Deliberately only settable from the ChatPage itself — the
    # dropdowns/slash commands there, never a Settings-page field — chat
    # defaults are a chat concern, Settings is infrastructure config.
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_threads_lab", "lab_id"),)
