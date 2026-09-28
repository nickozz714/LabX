"""Tickets in bulk verwijderen.

Honderd tickets één voor één weggooien is geen werk dat een mens hoort te doen,
maar verwijderen is wél onomkeerbaar — dus dit is de plek waar de vangrails
staan. Deze tests leggen vast welke dat zijn: een lopende agent blijft met rust,
de bron wordt niet aangeraakt (en dus komt een gesynchroniseerd ticket terug),
alles wat aan een ticket hangt gaat mee, en er zit een bovengrens op.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.board import Board, Ticket, TicketComment  # noqa: E402
from models.lab import Lab  # noqa: E402
from models.plan import TicketPlan, PlanClaim, TicketPlanItem  # noqa: E402
from services.boards.board_service import BoardService  # noqa: E402


@pytest.fixture()
def db():
    from db.database import Base

    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[
        Lab.__table__, Board.__table__, Ticket.__table__, TicketComment.__table__,
        TicketPlan.__table__, TicketPlanItem.__table__, PlanClaim.__table__])
    s = sessionmaker(bind=motor)()
    s.add(Board(id=1, name="Swinkels", key_prefix="SWI", provider="jira",
                provider_config={}, sync_direction="two_way",
                columns=[{"key": "todo", "name": "Nog doen"}],
                created_at="nu", updated_at="nu"))
    s.commit()
    yield s
    s.close()


def _t(db, key, **velden):
    basis = dict(board_id=1, key=key, title=key, status="todo", priority="normal",
                 labels=[], depends_on=[], position=1.0, created_at="nu", updated_at="nu")
    basis.update(velden)
    t = Ticket(**basis)
    db.add(t)
    db.commit()
    return t


def test_meerdere_tickets_in_een_keer_weg(db):
    for k in ("SWI-1", "SWI-2", "SWI-3"):
        _t(db, k)
    uit = BoardService(db).verwijder_tickets(1, ["SWI-1", "SWI-3"])
    assert uit["verwijderd"] == ["SWI-1", "SWI-3"]
    assert [t.key for t in db.query(Ticket).all()] == ["SWI-2"]


def test_een_ticket_waar_de_agent_op_draait_blijft_staan(db):
    """Zijn ticket weghalen laat een run achter die naar niets meer wijst, en
    die blijft daarna hangen in het overzicht."""
    _t(db, "SWI-1", agent_state="running")
    _t(db, "SWI-2")
    uit = BoardService(db).verwijder_tickets(1, ["SWI-1", "SWI-2"])
    assert uit["verwijderd"] == ["SWI-2"]
    assert uit["overgeslagen"][0]["key"] == "SWI-1"
    assert "bezig" in uit["overgeslagen"][0]["reden"]
    assert db.query(Ticket).filter(Ticket.key == "SWI-1").first() is not None


def test_met_ook_lopende_gaat_hij_alsnog(db):
    """Dan is het een keuze, en geen ongeluk."""
    _t(db, "SWI-1", agent_state="running")
    uit = BoardService(db).verwijder_tickets(1, ["SWI-1"], ook_lopende=True)
    assert uit["verwijderd"] == ["SWI-1"]


def test_een_gesynchroniseerd_ticket_wordt_gemeld_als_terugkomer(db):
    """Dit haalt de LabX-kopie weg, niet het issue in Jira — dus bij de
    volgende synchronisatie staat hij er weer. Dat hoort niet iets te zijn dat
    je zelf moet ontdekken."""
    _t(db, "SWI-1", external_id="10001", external_key="BICC-7")
    _t(db, "SWI-2")
    uit = BoardService(db).verwijder_tickets(1, ["SWI-1", "SWI-2"])
    assert uit["komt_terug_bij_sync"] == ["BICC-7"]
    assert len(uit["verwijderd"]) == 2


def test_alles_wat_aan_een_ticket_hangt_gaat_mee(db):
    """Anders blijven er rijen staan die naar een ticket wijzen dat niet meer
    bestaat."""
    t = _t(db, "SWI-1")
    db.add(TicketComment(ticket_id=t.id, kind="comment", author="nick", body="hoi",
                         created_at="nu"))
    db.add(PlanClaim(plan_item_id=1, ticket_id=t.id, resource="fabric:acc",
                           created_at="nu"))
    db.commit()
    BoardService(db).verwijder_tickets(1, ["SWI-1"])
    assert db.query(TicketComment).count() == 0
    assert db.query(PlanClaim).count() == 0


def test_een_onbekende_sleutel_is_geen_fout_maar_een_antwoord(db):
    """Bij een geplakte lijst van honderd zit er altijd eentje die al weg is;
    daar hoort de hele actie niet op te stranden."""
    _t(db, "SWI-1")
    uit = BoardService(db).verwijder_tickets(1, ["SWI-1", "SWI-999"])
    assert uit["verwijderd"] == ["SWI-1"]
    assert uit["niet_gevonden"] == ["SWI-999"]


@pytest.mark.parametrize("hoe", ["SWI-1", "BICC-7", "id"])
def test_een_ticket_is_op_drie_manieren_aan_te_wijzen(db, hoe):
    """Wie een lijst plakt heeft hem uit het scherm, uit Jira of uit een
    script."""
    t = _t(db, "SWI-1", external_id="10001", external_key="BICC-7")
    sleutel = str(t.id) if hoe == "id" else hoe
    assert BoardService(db).verwijder_tickets(1, [sleutel])["verwijderd"] == ["SWI-1"]


def test_er_zit_een_bovengrens_op(db):
    """Meer dan dit in één keer is geen opruimactie meer maar een ongeluk dat
    zich aan het voltrekken is."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as fout:
        BoardService(db).verwijder_tickets(1, [f"SWI-{i}" for i in range(500)])
    assert fout.value.status_code == 400
    assert "porties" in str(fout.value.detail)


def test_een_ticket_van_een_ander_bord_wordt_niet_geraakt(db):
    db.add(Board(id=2, name="Ander", key_prefix="AND", provider="local",
                 provider_config={}, sync_direction="two_way",
                 columns=[{"key": "todo", "name": "Nog doen"}],
                 created_at="nu", updated_at="nu"))
    db.commit()
    _t(db, "SWI-1", board_id=2)
    uit = BoardService(db).verwijder_tickets(1, ["SWI-1"])
    assert uit["niet_gevonden"] == ["SWI-1"]
    assert db.query(Ticket).count() == 1
