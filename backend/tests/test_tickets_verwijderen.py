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


# ── archiveren: de zachte variant ───────────────────────────────────────────
#
# Een bord loopt vol met afgehandeld werk, en dan is verwijderen te grof: je
# wilt het kunnen terugvinden. Een gearchiveerd ticket verdwijnt alleen uit het
# bord.

def test_archiveren_haalt_het_ticket_van_het_bord(db):
    _t(db, "SWI-1")
    _t(db, "SWI-2")
    svc = BoardService(db)
    uit = svc.archiveer_tickets(1, ["SWI-1"])
    assert uit["verwerkt"] == ["SWI-1"]
    assert [t.key for t in svc.list_tickets(1)] == ["SWI-2"]


def test_het_archief_is_apart_op_te_vragen(db):
    _t(db, "SWI-1")
    _t(db, "SWI-2")
    svc = BoardService(db)
    svc.archiveer_tickets(1, ["SWI-1"])
    assert [t.key for t in svc.list_tickets(1, gearchiveerd=True)] == ["SWI-1"]
    # en allebei tegelijk kan ook
    assert len(svc.list_tickets(1, gearchiveerd=None)) == 2


def test_terughalen_zet_hem_terug_op_het_bord(db):
    """Met één klik terug; dat is het hele verschil met verwijderen."""
    _t(db, "SWI-1")
    svc = BoardService(db)
    svc.archiveer_tickets(1, ["SWI-1"])
    uit = svc.archiveer_tickets(1, ["SWI-1"], terug=True)
    assert uit["verwerkt"] == ["SWI-1"]
    assert [t.key for t in svc.list_tickets(1)] == ["SWI-1"]


def test_nog_een_keer_archiveren_verandert_niets(db):
    """Bij een geplakte lijst van honderd zit er altijd eentje die al weg is."""
    _t(db, "SWI-1")
    svc = BoardService(db)
    svc.archiveer_tickets(1, ["SWI-1"])
    uit = svc.archiveer_tickets(1, ["SWI-1"])
    assert uit["verwerkt"] == [] and uit["liep_al"] == ["SWI-1"]


def test_archiveren_raakt_het_ticket_zelf_niet_kwijt(db):
    """Opmerkingen en de koppeling met de bron blijven; dat is de belofte."""
    t = _t(db, "SWI-1", external_id="10001", external_key="PROJ-7")
    db.add(TicketComment(ticket_id=t.id, kind="comment", author="nick", body="hoi",
                         created_at="nu"))
    db.commit()
    BoardService(db).archiveer_tickets(1, ["SWI-1"])
    db.refresh(t)
    assert t.external_key == "PROJ-7"
    assert db.query(TicketComment).count() == 1


def test_een_gearchiveerd_ticket_blijft_vindbaar_om_te_verwijderen(db):
    """Anders zit je met een archief dat je niet meer kunt opruimen."""
    _t(db, "SWI-1")
    svc = BoardService(db)
    svc.archiveer_tickets(1, ["SWI-1"])
    assert svc.verwijder_tickets(1, ["SWI-1"])["verwijderd"] == ["SWI-1"]


# ── verwijzingen naar andere tickets ────────────────────────────────────────
#
# In een opdracht staat vaak "wacht op SWI-12" of "zie PROJ-7317". Om daar een
# link van te maken moet je weten of die sleutel bestaat én welk ticket het is.

def test_een_sleutel_is_op_te_zoeken_op_alle_manieren(db):
    t = _t(db, "SWI-1", external_id="10001", external_key="PROJ-7")
    svc = BoardService(db)
    board = svc.get_board(1)
    for sleutel in ("SWI-1", "PROJ-7", str(t.id)):
        gevonden = svc._zoek_ticket(board, sleutel)
        assert gevonden is not None and gevonden.id == t.id


def test_een_gearchiveerd_ticket_blijft_op_te_zoeken(db):
    """Dat bestaat nog, het staat alleen niet op het bord — een verwijzing
    ernaartoe moet blijven werken."""
    _t(db, "SWI-1")
    svc = BoardService(db)
    svc.archiveer_tickets(1, ["SWI-1"])
    assert svc._zoek_ticket(svc.get_board(1), "SWI-1") is not None


def test_een_sleutel_die_niet_bestaat_levert_niets_op(db):
    """En dan hoort het scherm dat te zeggen in plaats van stil niets te doen:
    dan weet je niet of je mis klikte of dat het ticket weg is."""
    svc = BoardService(db)
    assert svc._zoek_ticket(svc.get_board(1), "SWI-999") is None
