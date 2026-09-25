"""LabX-skills als echte Claude Code-skills op schijf.

Aanleiding: "ik heb een skill toegevoegd, maar de chat kan hem niet vinden als
hij zoekt". Hij zát in de systeemprompt — als losse tekst — maar er bestond
geen skills-map en de `Skill`-tool stond niet eens in de lijst van toegestane
tools. Zoeken kon dus niet lukken.

Wat hier vastligt: een geplakte SKILL.md blijft intact (die heeft zijn eigen
frontmatter al), een gewone skill krijgt er zelf een, skills die bij een lab
horen komen hier niet terecht, en het opruimen blijft van andermans mappen af.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.skill import Skill  # noqa: E402
from services.skills import cli_skills  # noqa: E402


@pytest.fixture()
def db():
    from db.database import Base
    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[Skill.__table__])
    sessie = sessionmaker(bind=motor)()
    yield sessie
    sessie.close()


@pytest.fixture()
def map_(tmp_path, monkeypatch):
    doel = tmp_path / "skills"
    monkeypatch.setattr(cli_skills, "skills_dir", lambda: doel)
    return doel


def _skill(db, naam, instructies, **velden):
    basis = dict(name=naam, description=f"beschrijving van {naam}",
                 instructions=instructies, is_enabled=True, is_system=False,
                 created_at="nu", updated_at="nu")
    basis.update(velden)
    rij = Skill(**basis)
    db.add(rij)
    db.commit()
    return rij


SKILL_MD = """---
name: teams-webhook-post
description: Post naar een Teams-kanaal via een webhook
---

Doe dit en dat.
"""


def test_een_geplakte_skill_md_blijft_zoals_hij_is(db, map_):
    """Wie een echte SKILL.md plakt, heeft zijn frontmatter al. Er een tweede
    kop omheen zetten maakt hem juist kapot."""
    _skill(db, "Send incident information to Teams", SKILL_MD)
    cli_skills.sync_cli_skills(db)
    geschreven = (map_ / "send-incident-information-to-teams" / "SKILL.md").read_text()
    assert geschreven.startswith("---\nname: teams-webhook-post")
    assert geschreven.count("---") == 2, "geen tweede frontmatter erbovenop"


def test_een_gewone_skill_krijgt_zelf_frontmatter(db, map_):
    """Zonder `description` in de frontmatter weet de agent niet wanneer deze
    skill aan de beurt is — en dan vindt hij hem alsnog niet."""
    _skill(db, "Fabric laden", "Gebruik de fab-CLI.")
    cli_skills.sync_cli_skills(db)
    tekst = (map_ / "fabric-laden" / "SKILL.md").read_text()
    assert tekst.startswith("---\nname: fabric-laden\n")
    assert "description: beschrijving van Fabric laden" in tekst
    assert "Gebruik de fab-CLI." in tekst


def test_een_lab_skill_komt_hier_niet_te_staan(db, map_):
    """De skills-map geldt voor de hele instantie. Een skill die alleen in een
    bepaald lab hoort te gelden, zou daar in élk gesprek vindbaar zijn."""
    _skill(db, "Alleen hier", "x", usage_scope="lab")
    _skill(db, "Overal", "y", usage_scope="beide")
    cli_skills.sync_cli_skills(db)
    assert (map_ / "overal").exists()
    assert not (map_ / "alleen-hier").exists()


def test_uitgezette_en_systeemskills_tellen_niet_mee(db, map_):
    _skill(db, "Uit", "x", is_enabled=False)
    _skill(db, "Systeem", "y", is_system=True)
    res = cli_skills.sync_cli_skills(db)
    assert res["totaal"] == 0
    assert list(map_.iterdir()) == []


def test_een_verwijderde_skill_verdwijnt_ook_van_schijf(db, map_):
    """Een skill die weg is maar die de agent nog kan vinden, is erger dan een
    die er nooit was."""
    rij = _skill(db, "Tijdelijk", "x")
    cli_skills.sync_cli_skills(db)
    assert (map_ / "tijdelijk").exists()
    db.delete(rij)
    db.commit()
    cli_skills.sync_cli_skills(db)
    assert not (map_ / "tijdelijk").exists()


def test_een_map_die_niet_van_ons_is_blijft_staan(db, map_):
    """Iemand kan zelf een skill in die map hebben gezet. Die is niet van ons
    en gaat er dus niet aan."""
    map_.mkdir(parents=True)
    vreemd = map_ / "met-de-hand"
    vreemd.mkdir()
    (vreemd / "SKILL.md").write_text("van iemand anders")
    cli_skills.sync_cli_skills(db)
    assert (vreemd / "SKILL.md").read_text() == "van iemand anders"


def test_tweede_keer_schrijft_niets_opnieuw(db, map_):
    """Anders verspringt bij elke herstart de tijdstempel van elk bestand."""
    _skill(db, "Stabiel", "x")
    cli_skills.sync_cli_skills(db)
    tweede = cli_skills.sync_cli_skills(db)
    assert tweede["geschreven"] == 0


def test_een_onschrijfbare_map_houdt_niets_tegen(db, monkeypatch, tmp_path):
    """De instructies gaan sowieso mee in de prompt; dit mag nooit een beurt of
    een herstart laten falen."""
    monkeypatch.setattr(cli_skills, "skills_dir",
                        lambda: tmp_path / "bestaat" / "niet" / "\0ongeldig")
    _skill(db, "Iets", "x")
    res = cli_skills.sync_cli_skills(db)
    assert res["ok"] is False
