"""Automatisch archiveren van tickets.

Twee eigenschappen die niet mogen verschuiven:

1. **Nul dagen is uit.** Dat is de standaard, en niemand hoort verrast te
   worden doordat zijn tickets vanzelf uit het bord verdwijnen.
2. **Archiveren is geen verwijderen.** Het ticket blijft bestaan; alleen
   `archived_at` wordt gezet. En wordt het daarna weer aangeraakt, dan komt
   het terug -- de aanname "hier gebeurt niets meer" klopte dan niet.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.boards.archief import (  # noqa: E402
    archiveer_afgeronde_tickets, haal_ten_onrechte_gearchiveerde_terug,
)


def _tijd(dagen_geleden: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dagen_geleden)).isoformat()


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    import models.board  # noqa: F401
    # De doelen van de verwijzingen moeten bestaan, anders weigert SQLAlchemy
    # de tabellen aan te maken -- hetzelfde patroon als in conftest.
    import models.lab  # noqa: F401    (Board.lab_id)
    import models.thread  # noqa: F401 (Ticket.agent_thread_id)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        models.lab.Lab.__table__,
        models.thread.Thread.__table__,
        models.board.Board.__table__, models.board.Ticket.__table__,
    ])
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _bord(db, *, kolom="done", dagen=7):
    from models.board import Board
    b = Board(name="Proef", key_prefix="PRF", seq=0,
              columns=[{"key": "todo", "name": "Te doen"}, {"key": "done", "name": "Klaar"}],
              provider="local", sync_direction="two_way",
              archive_column=kolom, archive_days=dagen,
              created_at=_tijd(30), updated_at=_tijd(30))
    db.add(b)
    db.commit()
    return b


def _ticket(db, bord, *, status="done", oud=10.0, key="PRF-1"):
    from models.board import Ticket
    t = Ticket(board_id=bord.id, key=key, title="Iets", status=status,
               created_at=_tijd(20), updated_at=_tijd(oud))
    db.add(t)
    db.commit()
    return t


def test_een_oud_ticket_in_de_kolom_wordt_gearchiveerd(db):
    bord = _bord(db, kolom="done", dagen=7)
    t = _ticket(db, bord, status="done", oud=10)

    assert archiveer_afgeronde_tickets(db) == 1
    db.refresh(t)
    assert t.archived_at is not None
    # Archiveren is geen verwijderen.
    assert t.title == "Iets" and t.status == "done"


def test_een_vers_ticket_blijft_staan(db):
    bord = _bord(db, kolom="done", dagen=7)
    t = _ticket(db, bord, status="done", oud=2)

    assert archiveer_afgeronde_tickets(db) == 0
    db.refresh(t)
    assert t.archived_at is None


def test_een_ticket_in_een_andere_kolom_blijft_staan(db):
    bord = _bord(db, kolom="done", dagen=7)
    t = _ticket(db, bord, status="todo", oud=30)

    assert archiveer_afgeronde_tickets(db) == 0
    db.refresh(t)
    assert t.archived_at is None


def test_nul_dagen_is_uit(db):
    """De standaard. Zonder dit zou elk bestaand bord na een update ineens
    tickets gaan opruimen die niemand heeft aangewezen."""
    bord = _bord(db, kolom="done", dagen=0)
    t = _ticket(db, bord, status="done", oud=365)

    assert archiveer_afgeronde_tickets(db) == 0
    db.refresh(t)
    assert t.archived_at is None


def test_zonder_kolom_gebeurt_er_niets(db):
    bord = _bord(db, kolom="", dagen=7)
    t = _ticket(db, bord, status="done", oud=30)

    assert archiveer_afgeronde_tickets(db) == 0
    db.refresh(t)
    assert t.archived_at is None


def test_weer_opgepakt_betekent_terug_op_het_bord(db):
    """Iemand heeft het ticket na het archiveren toch weer aangeraakt."""
    bord = _bord(db, kolom="done", dagen=7)
    t = _ticket(db, bord, status="done", oud=10)
    archiveer_afgeronde_tickets(db)

    t.updated_at = datetime.now(timezone.utc).isoformat()
    db.commit()

    assert haal_ten_onrechte_gearchiveerde_terug(db) == 1
    db.refresh(t)
    assert t.archived_at is None
