"""Een clusterbord moet ook kunnen inplannen.

Een clusterbord heeft met OPZET geen `lab_id`: elk ticket wijst naar een
branch, en de agent werkt in het lab van díé branch. Zou hij in een bordlab
werken, dan wijzigde hij de verkeerde branch -- en dat merk je pas bij de
merge.

De backend kan dat geval (`agent_work._lab_voor`), maar de knoppen in het
bord keken alleen naar `lab_id` en stonden daardoor op een clusterbord
permanent uit. Deze test bewaakt allebei de kanten:

- de backend kiest het lab van de branch, en zegt het netjes als dat lab er
  nog niet is of als het ticket geen branch heeft;
- het scherm zet "Inplannen" niet uit op grond van `lab_id` alleen.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BRON = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(BRON))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import board, cluster, lab  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _opzet(db, *, met_lab=True, branch="feature/x"):
    from models.board import Board, Ticket
    from models.cluster import Cluster
    from models.lab import Lab

    nu = "2026-10-19T18:30:00+00:00"
    c = Cluster(naam="Hive", netwerk="n", created_at=nu, updated_at=nu)
    db.add(c)
    db.commit()
    b = Board(name="Hive", key_prefix="IH", columns=[], cluster_id=c.id,
              created_at=nu, updated_at=nu)
    db.add(b)
    db.commit()
    t = Ticket(board_id=b.id, key="IH-1", title="Iets", status="todo",
               priority="normal", labels=[], branch=branch,
               created_at=nu, updated_at=nu)
    db.add(t)
    if met_lab:
        db.add(Lab(id="l1", name="Hive · x", status="running", image="x",
                   volume_name="v", cluster_id=c.id, branch="feature/x",
                   created_at=nu, updated_at=nu))
    db.commit()
    return b, t


def test_het_lab_volgt_de_branch_van_het_ticket(db):
    from services.boards.agent_work import _lab_voor_ticket

    b, t = _opzet(db)
    assert _lab_voor_ticket(db, b, t).id == "l1"


def test_zonder_branch_zegt_hij_wat_er_moet_gebeuren(db):
    from fastapi import HTTPException

    from services.boards.agent_work import _lab_voor_ticket

    b, t = _opzet(db, branch=None)
    with pytest.raises(HTTPException) as fout:
        _lab_voor_ticket(db, b, t)
    assert fout.value.status_code == 409
    assert "branch" in fout.value.detail.lower()


def test_zonder_branch_lab_wijst_hij_de_weg(db):
    from fastapi import HTTPException

    from services.boards.agent_work import _lab_voor_ticket

    b, t = _opzet(db, met_lab=False)
    with pytest.raises(HTTPException) as fout:
        _lab_voor_ticket(db, b, t)
    assert fout.value.status_code == 409
    assert "feature/x" in fout.value.detail


def test_een_clusterbord_heeft_geen_lab_id_nodig(db):
    """Dat is geen tekortkoming maar het ontwerp."""
    b, _ = _opzet(db)
    assert b.lab_id is None
    assert b.cluster_id is not None


def test_de_knop_inplannen_kijkt_niet_alleen_naar_lab_id() -> None:
    """Dit was de echte oorzaak: `disabled={!board.lab_id}` zette de knop op
    een clusterbord permanent uit, terwijl de backend het geval aankon."""
    pagina = (BRON.parent.parent / "frontend" / "src" / "pages" / "BoardPage.tsx")
    tekst = pagina.read_text(encoding="utf-8")

    assert "disabled={!board.lab_id}" not in tekst
    assert "disabled={busy || !board.lab_id}" not in tekst
    # En wat er wél staat: lab OF cluster.
    assert "board.lab_id || board.cluster_id" in tekst
