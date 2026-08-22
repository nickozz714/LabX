# models/skill_tool.py
#
# Link table skill <-> tool. `instructions` is LabX's addition over ND3X's
# skill_tool (which has only `is_enabled`) — the per-tool guidance the Skill
# Wizard collects (issue 3: "Per Tool moet ik ook instructies kunnen opgeven").
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


class SkillTool(Base):
    __tablename__ = "skill_tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[int] = mapped_column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    tool_id: Mapped[int] = mapped_column(Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # How THIS skill wants the agent to use this tool — e.g. "Always pass
    # project=nectar-labx; never write secrets through this tool." Rendered
    # into the skill's guidance block alongside the tool's own description.
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("skill_id", "tool_id", name="uq_skill_tool"),)
