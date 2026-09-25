"""De skills-lijst geeft de gekoppelde tools mee.

Aanleiding: het scherm toont per skill "N gekoppelde tool(s)", maar de
lijst-endpoint stuurde dat veld niet mee. Met nul skills viel dat niemand op —
die regel draait dan nooit — en bij de eerste skill die iemand aanmaakt werd
het een wit scherm. Precies het soort fout dat pas opvalt als je het echt doet.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.mcp_server import MCPServer  # noqa: E402
from models.skill import Skill  # noqa: E402
from models.skill_tool import SkillTool  # noqa: E402
from models.tool import Tool  # noqa: E402
from routers.skill_router import _linked_tools_per_skill, list_skills  # noqa: E402


@pytest.fixture()
def db():
    from db.database import Base
    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[
        Skill.__table__, Tool.__table__, SkillTool.__table__, MCPServer.__table__,
    ])
    sessie = sessionmaker(bind=motor)()
    yield sessie
    sessie.close()


def _vul(db):
    db.add(Skill(id=1, name="fabric", description="", instructions="",
                 created_at="nu", updated_at="nu"))
    db.add(Skill(id=2, name="kaal", description="", instructions="",
                 created_at="nu", updated_at="nu"))
    db.add(Tool(id=10, name="run_query", remote_name="run_query", description="",
                created_at="nu", updated_at="nu"))
    db.add(SkillTool(id=100, skill_id=1, tool_id=10, is_enabled=True, instructions="doe"))
    db.commit()


def test_elke_skill_in_de_lijst_heeft_een_tools_veld(db):
    """Het scherm rekent erop. Ontbreekt het, dan is het geen lege lijst maar
    een crash — en dus een wit scherm."""
    _vul(db)
    uit = list_skills(db=db)
    assert all("tools" in s for s in uit)
    per_naam = {s["name"]: s for s in uit}
    assert len(per_naam["fabric"]["tools"]) == 1
    assert per_naam["kaal"]["tools"] == []


def test_de_gekoppelde_tool_staat_er_bruikbaar_in(db):
    _vul(db)
    tool = [s for s in list_skills(db=db) if s["name"] == "fabric"][0]["tools"][0]
    assert tool["tool_name"] == "run_query"
    assert tool["instructions"] == "doe"
    assert tool["argument"] == {"type": "object", "properties": {}}


def test_zonder_skills_valt_er_niets_om(db):
    assert list_skills(db=db) == []
    assert _linked_tools_per_skill(db, []) == {}
