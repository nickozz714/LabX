# models/workflow.py
#
# Deliberately NOT ND3X's DAG-of-operations model. Nick asked for "een
# Markdown bestand dat eventueel stappen beschrijft die de agent moet
# uitvoeren" — `markdown` is the source of truth; `steps_json` is a derived,
# structured view for the visual step editor (kept in sync by
# services/workflows/workflow_service.py's parser/serializer).
from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_workflows_enabled", "is_enabled"),)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lab_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual|cron
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("idx_workflow_runs_workflow", "workflow_id"),)
