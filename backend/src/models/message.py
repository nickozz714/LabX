# models/message.py — one chat turn's messages (user + assistant + tool steps).
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Tool-step trace for the "steps" view: [{type: thinking|tool, ...}]
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_messages_thread", "thread_id"),)
