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
    from models import board, cluster, lab, plan  # noqa: F401

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


# ── hoeveel tickets er tegelijk mogen lopen ──────────────────────────────────

def _plan_met(db, board, tickets):
    from models.plan import TicketPlan, TicketPlanItem

    nu = "2026-10-19T19:00:00+00:00"
    plan = TicketPlan(board_id=board.id, name="P", state="running",
                      created_at=nu, updated_at=nu)
    db.add(plan)
    db.commit()
    for i, t in enumerate(tickets):
        db.add(TicketPlanItem(plan_id=plan.id, ticket_id=t.id, position=i,
                              state="waiting"))
    db.commit()
    return plan


def _ticket(db, board, key, branch):
    from models.board import Ticket

    nu = "2026-10-19T19:00:00+00:00"
    t = Ticket(board_id=board.id, key=key, title=key, status="agent",
               priority="normal", labels=[], branch=branch,
               created_at=nu, updated_at=nu)
    db.add(t)
    db.commit()
    return t


def _lab(db, cluster_id, lab_id, branch, werkers=1):
    from models.lab import Lab

    nu = "2026-10-19T19:00:00+00:00"
    db.add(Lab(id=lab_id, name=lab_id, status="running", image="x", volume_name="v",
               cluster_id=cluster_id, branch=branch, max_workers=werkers,
               created_at=nu, updated_at=nu))
    db.commit()


def test_drie_tickets_in_drie_labs_lopen_tegelijk(db):
    """Dit was de fout: het plafond kwam van het lab van het BORD, en een
    clusterbord heeft dat niet. Drie tickets voor drie verschillende labs
    werden daardoor één voor één afgewerkt terwijl ze elkaar onmogelijk in de
    weg konden zitten -- je ziet geen fout, je ziet alleen traagheid."""
    from services.boards.plan_service import PlanService

    b, _ = _opzet(db, met_lab=False, branch="feature/a")
    _lab(db, b.cluster_id, "l-a", "feature/a")
    _lab(db, b.cluster_id, "l-b", "feature/b")
    _lab(db, b.cluster_id, "l-c", "feature/c")
    t1 = _ticket(db, b, "IH-10", "feature/a")
    t2 = _ticket(db, b, "IH-11", "feature/b")
    t3 = _ticket(db, b, "IH-12", "feature/c")
    plan = _plan_met(db, b, [t1, t2, t3])

    assert PlanService(db)._plafond(plan, b) == 3


def test_twee_tickets_op_dezelfde_branch_delen_hun_lab(db):
    """Die zitten wél in dezelfde container en horen elkaar niet te verdringen."""
    from services.boards.plan_service import PlanService

    b, _ = _opzet(db, met_lab=False, branch="feature/a")
    _lab(db, b.cluster_id, "l-a", "feature/a")
    t1 = _ticket(db, b, "IH-10", "feature/a")
    t2 = _ticket(db, b, "IH-11", "feature/a")
    plan = _plan_met(db, b, [t1, t2])

    assert PlanService(db)._plafond(plan, b) == 1


def test_meer_werkers_in_een_lab_tellen_mee(db):
    from services.boards.plan_service import PlanService

    b, _ = _opzet(db, met_lab=False, branch="feature/a")
    _lab(db, b.cluster_id, "l-a", "feature/a", werkers=3)
    t1 = _ticket(db, b, "IH-10", "feature/a")
    plan = _plan_met(db, b, [t1])

    assert PlanService(db)._plafond(plan, b) == 3


def test_een_branch_zonder_lab_telt_voor_een(db):
    """Het lab komt er nog; zolang dat zo is houdt _lab_voor_ticket het ticket
    zelf tegen, met een melding die zegt wat eraan ontbreekt."""
    from services.boards.plan_service import PlanService

    b, _ = _opzet(db, met_lab=False, branch="feature/a")
    _lab(db, b.cluster_id, "l-a", "feature/a")
    t1 = _ticket(db, b, "IH-10", "feature/a")
    t2 = _ticket(db, b, "IH-11", "feature/bestaat-nog-niet")
    plan = _plan_met(db, b, [t1, t2])

    assert PlanService(db)._plafond(plan, b) == 2


def test_een_eigen_plafond_wint(db):
    """Zet je zelf 'hooguit twee tegelijk', dan telt dat en niet de labs."""
    from services.boards.plan_service import PlanService

    b, _ = _opzet(db, met_lab=False, branch="feature/a")
    for n in "abc":
        _lab(db, b.cluster_id, f"l-{n}", f"feature/{n}")
    tickets = [_ticket(db, b, f"IH-2{i}", f"feature/{n}")
               for i, n in enumerate("abc")]
    plan = _plan_met(db, b, tickets)
    plan.max_parallel = 2
    db.commit()

    assert PlanService(db)._vrije_ruimte(plan, 0) == 2
