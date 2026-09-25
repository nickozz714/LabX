# models/skill.py — a named capability: description (for the CLI's catalog),
# instructions (how-to guidance loaded for enabled skills), and a link to the
# tools it uses (see skill_tool.py for the per-tool instruction, issue 3).
from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.database import Base


# Waar een skill mag gelden. Zelfde drieslag als bij een MCP-server
# (models/mcp_server.py), en met opzet dezelfde woorden — het is voor wie het
# instelt hetzelfde begrip:
#
#   sessie — hij hoort bij het GESPREK: zijn instructies gaan in elke beurt
#            mee, ongeacht aan welk lab de chat hangt. Denk aan een manier van
#            werken of een schrijfstijl.
#   lab    — hij hoort bij een LAB: zijn instructies (en de tools eronder)
#            tellen alleen in een lab dat hem in zijn lijst heeft staan. Denk
#            aan iets dat afhangt van wat er in die container staat.
#   beide  — allebei, en dat is de standaard: precies het gedrag van vóór deze
#            keuze, zodat bestaande skills niet ineens ergens verdwijnen.
SKILL_USAGE_SCOPES = ("sessie", "lab", "beide")


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # sessie | lab | beide. NULL = "beide" (zie SKILL_USAGE_SCOPES): een skill
    # van vóór deze kolom geldt overal, net als voorheen.
    usage_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("idx_skills_enabled", "is_enabled"),)
