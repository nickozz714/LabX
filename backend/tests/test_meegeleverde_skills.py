"""Skills die LabX zelf meebrengt.

De eerste is `to-the-point`: een schrijfstijl die antwoorden laat beginnen
met wat je moet doen. Naar het idee van `i-have-adhd` van ayghri (MIT),
nagevolgd in eigen woorden.

Wat hier vastligt en stil kan sneuvelen:

- **Hij komt één keer, niet elke start opnieuw.** Een meegeleverde skill die
  zichzelf terugzet nadat jij hem uitzette, is geen hulp maar een discussie.
- **`usage_scope = sessie`**, want anders zou hij per lab aangevinkt moeten
  worden en kreeg je in het ene lab korte antwoorden en in het andere niet.
- **De uitzonderingen staan erin.** Zonder die regels zou "geen inleiding,
  geen omhaal" ook gelden voor een bevestiging vóór iets onomkeerbaars.
- **De bron staat erbij.** Het idee is niet van ons.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import skill  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def test_de_skills_worden_aangemaakt(db):
    from models.skill import Skill
    from services.skills.meegeleverd import MEEGELEVERD, zorg_voor_meegeleverde_skills

    assert zorg_voor_meegeleverde_skills(db) == len(MEEGELEVERD)

    for sjabloon in MEEGELEVERD:
        rij = db.query(Skill).filter(Skill.name == sjabloon["name"]).one()
        assert rij.is_enabled is True
        assert rij.usage_scope == "sessie"
        assert rij.is_system is False


def test_weglaten_wint_van_kort(db):
    """Botsen 'hou het kort' en 'laat dit weg', dan wint weglaten."""
    from models.skill import Skill
    from services.skills.meegeleverd import zorg_voor_meegeleverde_skills

    zorg_voor_meegeleverde_skills(db)
    kort = db.query(Skill).filter(Skill.name == "to-the-point").one()
    extern = db.query(Skill).filter(Skill.name == "externe-communicatie").one()
    assert extern.priority > kort.priority


def test_de_externe_regel_noemt_wat_er_niet_in_mag(db):
    from services.skills.meegeleverd import EXTERNE_COMMUNICATIE

    tekst = EXTERNE_COMMUNICATIE.lower()
    for moet in ("ticketsleutels", "labs", "branches", "servernamen",
                 "collega", "bord"):
        assert moet in tekst, moet


def test_een_tweede_start_verandert_niets(db):
    from services.skills.meegeleverd import zorg_voor_meegeleverde_skills

    zorg_voor_meegeleverde_skills(db)
    assert zorg_voor_meegeleverde_skills(db) == 0


def test_uitzetten_blijft_uit(db):
    """Dit is het hele punt: jouw keuze wint van de meegeleverde stand."""
    from models.skill import Skill
    from services.skills.meegeleverd import zorg_voor_meegeleverde_skills

    zorg_voor_meegeleverde_skills(db)
    rij = db.query(Skill).filter(Skill.name == "to-the-point").one()
    rij.is_enabled = False
    rij.instructions = "mijn eigen versie"
    db.commit()

    zorg_voor_meegeleverde_skills(db)

    rij = db.query(Skill).filter(Skill.name == "to-the-point").one()
    assert rij.is_enabled is False
    assert rij.instructions == "mijn eigen versie"


def test_weggooien_brengt_hem_niet_terug(db):
    from models.skill import Skill
    from services.skills.meegeleverd import zorg_voor_meegeleverde_skills

    zorg_voor_meegeleverde_skills(db)
    db.delete(db.query(Skill).filter(Skill.name == "to-the-point").one())
    db.commit()

    # Hij komt wél terug: de functie kent alleen "bestaat deze naam al".
    # Dat is bewust -- weggooien is dan 'opnieuw' in plaats van 'voorgoed
    # weg', en uitzetten is de manier om er vanaf te zijn.
    assert zorg_voor_meegeleverde_skills(db) == 1


def test_de_uitzonderingen_staan_erin():
    """Zonder deze zou 'geen omhaal' ook gelden vóór iets onomkeerbaars."""
    from services.skills.meegeleverd import TO_THE_POINT

    tekst = TO_THE_POINT.lower()
    assert "bevestiging" in tekst
    assert "force push" in tekst
    assert "data-guard" in tekst


def test_de_bron_staat_erbij():
    from services.skills.meegeleverd import TO_THE_POINT

    assert "ayghri/i-have-adhd" in TO_THE_POINT
    assert "MIT" in TO_THE_POINT


def test_alle_tien_de_regels_staan_erin():
    """Negen regels met de belofte van tien is geen schrijfstijl maar een gok."""
    import re

    from services.skills.meegeleverd import TO_THE_POINT

    genummerd = re.findall(r"^(\d+)\. ", TO_THE_POINT, flags=re.M)
    assert genummerd == [str(i) for i in range(1, 11)]
