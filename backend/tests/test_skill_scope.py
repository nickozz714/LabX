"""Waar een skill geldt: alleen in een sessie, alleen in een lab, of beide.

Zonder deze keuze praatte élke ingeschakelde skill in élk gesprek mee, ook een
die over één omgeving gaat. Met de keuze gaat het als volgt:

- `sessie` — bij het gesprek: altijd mee, en nooit achter de lijst van een lab.
- `lab`    — bij een lab: alleen als dat lab hem toelaat.
- `beide`  — allebei, en dat is de standaard, zodat er aan bestaande skills
             niets verandert.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.lab import Lab  # noqa: E402
from models.skill import Skill  # noqa: E402
from services.agent.chat_agent import ChatAgent  # noqa: E402


@pytest.fixture()
def db():
    from db.database import Base
    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[Skill.__table__, Lab.__table__])
    sessie = sessionmaker(bind=motor)()
    sessie.add(Lab(id="lab-1", name="Fabric", status="running", image="x",
                   allowed_skills=["Alleen hier"], created_at="nu", updated_at="nu"))
    sessie.add(Lab(id="lab-2", name="Ander", status="running", image="x",
                   allowed_skills=[], created_at="nu", updated_at="nu"))
    sessie.commit()
    yield sessie
    sessie.close()


def _skill(db, naam, scope):
    db.add(Skill(name=naam, description="", instructions="doe iets",
                 usage_scope=scope, is_enabled=True, is_system=False,
                 created_at="nu", updated_at="nu"))
    db.commit()


def test_een_sessieskill_gaat_altijd_mee(db):
    _skill(db, "Schrijfstijl", "sessie")
    agent = ChatAgent(db)
    assert agent._enabled_domain_skill_names("lab-1") == ["Schrijfstijl"]
    assert agent._enabled_domain_skill_names("lab-2") == ["Schrijfstijl"]
    assert agent._enabled_domain_skill_names(None) == ["Schrijfstijl"]


def test_een_labskill_telt_alleen_waar_hij_is_aangevinkt(db):
    _skill(db, "Alleen hier", "lab")
    agent = ChatAgent(db)
    assert agent._enabled_domain_skill_names("lab-1") == ["Alleen hier"]
    assert agent._enabled_domain_skill_names("lab-2") == []
    assert agent._enabled_domain_skill_names(None) == []


def test_zonder_scope_verandert_er_niets(db):
    """Alles wat er al stond heeft geen scope. Dat moet zich gedragen als
    'beide' — anders verdwijnen bestaande skills stilletjes uit gesprekken."""
    db.add(Skill(name="Oud", description="", instructions="x", is_enabled=True,
                 is_system=False, created_at="nu", updated_at="nu"))
    db.commit()
    agent = ChatAgent(db)
    assert agent._enabled_domain_skill_names("lab-2") == ["Oud"]
    assert agent._enabled_domain_skill_names(None) == ["Oud"]


def test_beide_gedraagt_zich_als_vroeger(db):
    _skill(db, "Overal", "beide")
    assert ChatAgent(db)._enabled_domain_skill_names("lab-2") == ["Overal"]


def test_de_tools_van_een_sessieskill_blijven_buiten_de_lablijst(db):
    """Een lab met een lijst verbergt tools die aan een niet-toegelaten skill
    hangen. Voor een sessieskill mag dat niet gelden — dan zou 'alleen in een
    sessie' in de praktijk 'nergens' betekenen."""
    from models.mcp_server import MCPServer
    from models.skill_tool import SkillTool
    from models.tool import Tool
    from services.mcp.gateway import _skill_scoped_tool_ids
    from db.database import Base

    Base.metadata.create_all(db.get_bind(), tables=[
        MCPServer.__table__, Tool.__table__, SkillTool.__table__])
    _skill(db, "Sessieding", "sessie")
    _skill(db, "Labding", "lab")
    per_naam = {s.name: s.id for s in db.query(Skill).all()}
    db.add(SkillTool(skill_id=per_naam["Sessieding"], tool_id=10, is_enabled=True))
    db.add(SkillTool(skill_id=per_naam["Labding"], tool_id=11, is_enabled=True))
    db.commit()

    gekoppeld = _skill_scoped_tool_ids(db)
    assert 10 not in gekoppeld, "een sessieskill hoort zijn tools niet aan de lablijst te hangen"
    assert gekoppeld.get(11) == {"Labding"}
