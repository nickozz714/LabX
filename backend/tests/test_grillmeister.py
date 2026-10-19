"""De grillmeister: iemand die je ticket op de rooster legt.

Een ticket dat je zelf schrijft lijkt altijd duidelijk. Wat erin ontbreekt
zie je pas als iemand anders het leest -- of pas als het half gebouwd is.

Wat hier vastligt en stil kan sneuvelen:

- **Hij schrijft niet ongevraagd naar het ticket.** Grillen levert vragen op,
  geen nieuwe tekst. Een agent die meteen je ticket herschrijft haalt juist
  het denkwerk weg dat het ticket nodig heeft.
- **De klantversie gaat langs de controle.** Glipt er toch een interne
  verwijzing doorheen, dan zegt het antwoord dat -- in plaats van hem stil
  door te laten.
- **De buren tellen mee.** Zonder de andere tickets kan hij niet zien dat
  iets al ergens anders belegd is.
- **De prompts renderen.** Ze bevatten JSON-voorbeelden met accolades.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import board, cluster, lab  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    nu = "2026-10-19T15:00:00+00:00"
    b = board.Board(name="IntelligentHive", key_prefix="IH", columns=[],
                    description="Werk aan het cluster", created_at=nu, updated_at=nu)
    s.add(b)
    s.commit()
    s.add(board.Ticket(board_id=b.id, key="IH-4", title="Bulk in Zilver en Goud",
                       description="Meerdere nodes tegelijk publiceren.",
                       status="todo", priority="normal", labels=["bulk"],
                       created_at=nu, updated_at=nu))
    s.add(board.Ticket(board_id=b.id, key="IH-5", title="Cross-domein relaties",
                       description="Dimensies lenen.", status="todo",
                       priority="normal", labels=[], created_at=nu, updated_at=nu))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _ticket(db, key="IH-4"):
    from models.board import Ticket

    return db.query(Ticket).filter(Ticket.key == key).one()


def _meister(db, antwoord: str):
    from services.boards.grillmeister import GrillMeister

    svc = GrillMeister(db)
    gezien = {}

    async def nep(prompt):
        gezien["prompt"] = prompt
        return antwoord

    svc._vraag = nep
    return svc, gezien


# ── grillen ──────────────────────────────────────────────────────────────────

def test_grillen_levert_vragen_op_en_geen_nieuwe_tekst(db):
    svc, _ = _meister(db, '{"antwoord": "Rammelt op randgevallen.", '
                          '"vragen": ["Wat als het al gepubliceerd is?"], '
                          '"gaten": ["geen bovengrens"], '
                          '"voorstel": {"description": "ONGEVRAAGD"}}')
    uit = asyncio.run(svc.beurt(_ticket(db).id))

    assert uit["vragen"] == ["Wat als het al gepubliceerd is?"]
    assert uit["gaten"] == ["geen bovengrens"]
    # Ook al stuurt het model een voorstel mee: in deze modus telt het niet.
    assert uit["voorstel"] is None


def test_de_andere_tickets_van_het_bord_gaan_mee(db):
    """Zonder de buren kan hij niet zien dat iets al ergens anders staat."""
    svc, gezien = _meister(db, '{"antwoord": "x"}')
    asyncio.run(svc.beurt(_ticket(db).id))

    assert "IH-5" in gezien["prompt"]
    assert "Cross-domein relaties" in gezien["prompt"]
    # Het ticket zelf staat niet twee keer in de burenlijst.
    assert gezien["prompt"].count("IH-4") >= 1


def test_het_gesprek_gaat_mee_maar_afgekapt(db):
    from services.boards.grillmeister import MAX_BEURTEN

    svc, gezien = _meister(db, '{"antwoord": "x"}')
    eerder = [{"rol": "mens", "tekst": f"ronde {i}"} for i in range(MAX_BEURTEN + 5)]
    asyncio.run(svc.beurt(_ticket(db).id, geschiedenis=eerder))

    assert f"ronde {MAX_BEURTEN + 4}" in gezien["prompt"]
    assert "ronde 0" not in gezien["prompt"]


def test_de_prompts_renderen_zonder_ontbrekende_placeholder(db):
    svc, gezien = _meister(db, '{"antwoord": "x"}')
    for modus in ("grillen", "voorstel", "klantversie"):
        asyncio.run(svc.beurt(_ticket(db).id, modus=modus, bericht="let op de seed"))
        prompt = gezien["prompt"]
        assert not re.search(r"\{[a-z_]+\}", prompt), re.findall(r"\{[a-z_]+\}", prompt)
        assert "let op de seed" in prompt


def test_een_onbekende_modus_wordt_geweigerd(db):
    from fastapi import HTTPException

    svc, _ = _meister(db, "{}")
    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.beurt(_ticket(db).id, modus="bakken"))
    assert fout.value.status_code == 400


def test_proza_in_plaats_van_json_levert_geen_crash(db):
    svc, _ = _meister(db, "Tja, lastig te zeggen.")
    uit = asyncio.run(svc.beurt(_ticket(db).id))

    assert "lastig" in uit["antwoord"]
    assert uit["vragen"] == []


# ── voorstel ─────────────────────────────────────────────────────────────────

def test_een_voorstel_komt_terug_maar_staat_nog_niet_op_het_ticket(db):
    svc, _ = _meister(db, '{"antwoord": "aangescherpt", "voorstel": '
                          '{"titel": "Bulk in Zilver en Goud", '
                          '"description": "nieuw", "acceptance_criteria": "1. x"}}')
    uit = asyncio.run(svc.beurt(_ticket(db).id, modus="voorstel"))

    assert uit["voorstel"]["description"] == "nieuw"
    assert _ticket(db).description == "Meerdere nodes tegelijk publiceren."


def test_overnemen_schrijft_alleen_wat_je_meestuurt(db):
    from services.boards.grillmeister import GrillMeister

    t = _ticket(db)
    GrillMeister(db).pas_toe(t.id, acceptance_criteria="1. Het werkt.")

    ververst = _ticket(db)
    assert ververst.acceptance_criteria == "1. Het werkt."
    assert ververst.title == "Bulk in Zilver en Goud"
    assert ververst.description == "Meerdere nodes tegelijk publiceren."


def test_overnemen_zonder_inhoud_is_een_nette_fout(db):
    from fastapi import HTTPException

    from services.boards.grillmeister import GrillMeister

    with pytest.raises(HTTPException) as fout:
        GrillMeister(db).pas_toe(_ticket(db).id, description="   ")
    assert fout.value.status_code == 400


# ── klantversie ──────────────────────────────────────────────────────────────

def test_een_schone_klantversie_komt_schoon_terug(db):
    svc, _ = _meister(db, '{"antwoord": "weggelaten: alles intern", '
                          '"klanttekst": "U kunt binnenkort meerdere items '
                          'tegelijk publiceren."}')
    uit = asyncio.run(svc.beurt(_ticket(db).id, modus="klantversie"))

    assert uit["controle"]["schoon"] is True
    assert "meerdere items" in uit["klanttekst"]


def test_een_interne_verwijzing_in_de_klantversie_wordt_gemeld(db):
    """Dit is het hele punt: de prompt vraagt erom, de controle rekent na."""
    svc, _ = _meister(db, '{"antwoord": "klaar", "klanttekst": '
                          '"Dit is opgelost, zie IH-4 in Nectar."}')
    uit = asyncio.run(svc.beurt(_ticket(db).id, modus="klantversie"))

    assert uit["controle"]["schoon"] is False
    gevonden = {b["gevonden"].upper() for b in uit["controle"]["bevindingen"]}
    assert "IH-4" in gevonden
    assert "NECTAR" in gevonden
    # En het staat in het antwoord, niet alleen in een veld dat niemand leest.
    assert "IH-4" in uit["antwoord"]


def test_de_klantprompt_verbiedt_het_met_zoveel_woorden(db):
    svc, gezien = _meister(db, '{"klanttekst": "x"}')
    asyncio.run(svc.beurt(_ticket(db).id, modus="klantversie"))

    prompt = gezien["prompt"].lower()
    assert "ticketsleutels" in prompt
    assert "van buiten" in prompt
    for verboden in ("labs", "branches", "servernamen"):
        assert verboden in prompt


def test_alleen_de_klantversie_wordt_gecontroleerd(db):
    """Grillen en voorstellen zijn intern; daar mag IH-4 gewoon in staan."""
    svc, _ = _meister(db, '{"antwoord": "zie IH-4", "vragen": []}')
    uit = asyncio.run(svc.beurt(_ticket(db).id))

    assert uit["controle"] is None
    assert "IH-4" in uit["antwoord"]
