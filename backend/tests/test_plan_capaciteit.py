"""Een planning moet de capaciteit gebruiken die je hebt ingesteld.

Aanleiding: een lab met 3 werkers en 3 sessies per werker -- negen plekken op
papier -- verwerkte één ticket tegelijk van een planning met elf tickets. Daar
zaten drie dingen achter, en die worden hier alledrie vastgelegd:

1. **Vol is niet hetzelfde als bezig.** De planning sloot elke werker uit waar
   één ticket op draaide, ongeacht `sessies_per_werker`. De telling die dat wél
   goed deed (`bezette_werkers`) kwam nooit aan bod.
2. **Werkers worden tegelijk ingericht.** Het slot zat per LAB, terwijl het
   losse containers zijn -- dus wachtte werker 3 de hele Playwright-installatie
   van werker 2 af.
3. **De planning zegt waarom hij wacht.** Anders zie je "1 loopt" bij drie
   werkers en is de database de enige plek waar het antwoord staat.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import (  # noqa: F401
        audit, azure_profile, background_run, board, claim_resource, guard_audit,
        guard_rule, lab, lab_extra, lab_secret, lab_worker, mcp_server, message,
        notification, plan, schedule, secret, skill, skill_tool, thread,
        time_entry, tool, voice, workflow,
    )

    monkeypatch.setenv("LABX_FERNET_KEY",
                       "dGVzdC1zbGV1dGVsLXZvb3ItZGUtdW5pdC10ZXN0cy0wMDA9")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _lab_met_werkers(db, *, aantal: int, sessies: int, inricht_klaar: bool = True):
    from models.lab import Lab
    from models.lab_worker import LabWorker

    nu = "2026-10-15T09:00:00+00:00"
    p = Lab(id="lab1", name="Krimpenerwaard", image="python:3", volume_name="v",
            network_alias="labx-lab-1", status="running", cpu_limit=1,
            mem_limit_mb=2048, ttl_hours=24, created_at=nu, updated_at=nu,
            worker_count=aantal, min_workers=1, max_workers=aantal,
            sessies_per_werker=sessies)
    db.add(p)
    for i in range(1, aantal + 1):
        db.add(LabWorker(
            lab_id=p.id, index=i, container_id=f"c{i}",
            network_alias=f"labx-lab-1-w{i}", status="running",
            provision_status="ok" if (i == 1 or inricht_klaar) else "pending",
            created_at=nu, updated_at=nu))
    db.commit()
    return p


def _plan_met_tickets(db, lab, aantal: int, *, max_parallel=None):
    from models.board import Board, Ticket
    from models.plan import TicketPlan, TicketPlanItem

    nu = "2026-10-15T09:00:00+00:00"
    # Board- en ticket-id's zijn INTEGERS hier, geen strings.
    board = Board(id=1, name="Bord", lab_id=lab.id, columns=["todo"],
                  created_at=nu, updated_at=nu)
    db.add(board)
    plan = TicketPlan(id=1, board_id=board.id, name="Onderzoek", state="running",
                      max_parallel=max_parallel, workspace_mode="apart",
                      created_at=nu, updated_at=nu)
    db.add(plan)
    db.flush()
    for i in range(1, aantal + 1):
        db.add(Ticket(id=i, board_id=board.id, key=f"K-{i}", title=f"Ticket {i}",
                      status="todo", position=float(i), created_at=nu, updated_at=nu))
        db.add(TicketPlanItem(id=i, plan_id=plan.id, ticket_id=i,
                              position=i * 100.0, state="waiting"))
    db.commit()
    return plan


# ── 1. vol is niet hetzelfde als bezig ───────────────────────────────────────

def test_een_tweede_ticket_mag_bij_een_draaiende_werker(db):
    """De kern van de klacht. Met 3 sessies per werker mag er een tweede
    ticket bij op een werker waar er al één draait."""
    from services.boards.plan_service import PlanService

    lab = _lab_met_werkers(db, aantal=1, sessies=3)
    plan = _plan_met_tickets(db, lab, 2)
    svc = PlanService(db)

    werker, bezet = svc._claim_worker(plan)
    assert werker is not None, "eerste ticket krijgt een werker"

    # Doe alsof dat ticket nu draait.
    items = svc.items(plan.id)
    items[0].state = "running"
    items[0].worker_id = werker.id
    db.commit()

    opnieuw, bezet = svc._claim_worker(plan)
    assert opnieuw is not None, "bij 3 sessies per werker mag er een tweede bij"
    assert opnieuw.id == werker.id, "en dat is dezelfde werker, want er is er maar één"


def test_bij_een_sessie_per_werker_blijft_het_een_per_werker(db):
    """De oude afspraak moet blijven gelden waar hij bedoeld was."""
    from services.boards.plan_service import PlanService

    lab = _lab_met_werkers(db, aantal=1, sessies=1)
    plan = _plan_met_tickets(db, lab, 2)
    svc = PlanService(db)

    werker, _ = svc._claim_worker(plan)
    items = svc.items(plan.id)
    items[0].state = "running"
    items[0].worker_id = werker.id
    db.commit()

    opnieuw, bezet_door = svc._claim_worker(plan)
    assert opnieuw is None, "met 1 sessie per werker is hij nu vol"


def test_werk_gaat_naar_de_minst_drukke_werker(db):
    """Anders stapelt alles op werker 1 omdat die vooraan staat."""
    from services.boards.plan_service import PlanService

    lab = _lab_met_werkers(db, aantal=3, sessies=3)
    plan = _plan_met_tickets(db, lab, 5)
    svc = PlanService(db)
    gekozen = []
    for i in range(3):
        w, _ = svc._claim_worker(plan)
        assert w is not None
        items = svc.items(plan.id)
        items[i].state = "running"
        items[i].worker_id = w.id
        db.commit()
        gekozen.append(w.index)

    assert sorted(gekozen) == [1, 2, 3], f"eerst spreiden, niet stapelen: {gekozen}"


def test_een_handmatig_gestarte_run_bezet_ook_een_plek(db):
    """De spiegelfout van de hoofdklacht: een ticket dat met de hand is gestart
    heeft geen planningsregel. Telde de planning die niet mee, dan zette hij
    werk bovenop iemand die er al zat."""
    from models.background_run import BackgroundRun
    from models.thread import Thread
    from services.boards.plan_service import PlanService

    nu = "2026-10-15T09:00:00+00:00"
    lab = _lab_met_werkers(db, aantal=1, sessies=1)
    plan = _plan_met_tickets(db, lab, 2)
    svc = PlanService(db)
    werker = svc._claim_worker(plan)[0]

    # Geen planningsitem: puur een losse run op die werker. Een run hangt
    # altijd aan een thread, dus die hoort erbij.
    db.add(Thread(id="th1", lab_id=lab.id, title="Handmatig",
                  created_at=nu, updated_at=nu))
    db.add(BackgroundRun(id="r1", thread_id="th1", lab_worker_id=werker.id,
                         status="running", mode="background", prompt="doe iets",
                         created_at=nu))
    db.commit()

    opnieuw, _ = svc._claim_worker(plan)
    assert opnieuw is None, "die werker zit vol door de handmatige run"


# ── 2. werkers worden tegelijk ingericht ─────────────────────────────────────

def test_het_inrichten_gaat_per_werker_tegelijk(db, monkeypatch):
    """Het slot zat per LAB, maar het zijn losse containers. Werker 3 wachtte
    daardoor de hele Playwright-installatie van werker 2 af."""
    import asyncio

    from services.lab.lab_service import LabService

    lab = _lab_met_werkers(db, aantal=3, sessies=1)
    lab.container_id = "c1"
    lab.allow_network = True
    db.commit()

    tegelijk = {"nu": 0, "max": 0}

    async def _stap(self, container_id, step, *, force=False):
        tegelijk["nu"] += 1
        tegelijk["max"] = max(tegelijk["max"], tegelijk["nu"])
        await asyncio.sleep(0.01)
        tegelijk["nu"] -= 1
        return {"key": step["key"], "label": step["label"], "status": "ok",
                "exit_code": 0, "output": ""}

    monkeypatch.setattr(LabService, "_run_provision_step", _stap)
    asyncio.run(LabService(db).provision(lab.id))

    assert tegelijk["max"] > 1, "de werkers moeten tegelijk ingericht worden"


# ── 3. de planning zegt waarom hij wacht ─────────────────────────────────────

def test_een_wachtende_planning_zegt_dat_er_ingericht_wordt(db):
    from services.boards.plan_service import PlanService

    lab = _lab_met_werkers(db, aantal=3, sessies=1, inricht_klaar=False)
    plan = _plan_met_tickets(db, lab, 5)
    svc = PlanService(db)

    # Eerst claimen, dan pas op "running" zetten — andersom ziet _claim_worker
    # een lopend item zonder werker, en dat betekent "het hele lab is bezet".
    w, _ = svc._claim_worker(plan)
    items = svc.items(plan.id)
    items[0].state = "running"
    items[0].worker_id = w.id
    db.commit()

    reden = svc._capaciteit_in_woorden(plan)
    assert reden and "ingericht" in reden
    assert "1 van de 3" in reden, f"zeg de getallen: {reden}"


def test_een_vol_lab_zegt_dat_het_vol_is(db):
    from services.boards.plan_service import PlanService

    lab = _lab_met_werkers(db, aantal=2, sessies=1)
    plan = _plan_met_tickets(db, lab, 5)
    svc = PlanService(db)

    items = svc.items(plan.id)
    for i in range(2):
        w, _ = svc._claim_worker(plan)
        items[i].state = "running"
        items[i].worker_id = w.id
        db.commit()

    reden = svc._capaciteit_in_woorden(plan)
    assert reden and "vol" in reden
    assert "sessies per werker" in reden, f"wijs naar de knop: {reden}"


def test_met_ruimte_zegt_de_planning_niets(db):
    """Een melding die er altijd staat, leest niemand meer."""
    from services.boards.plan_service import PlanService

    lab = _lab_met_werkers(db, aantal=3, sessies=3)
    plan = _plan_met_tickets(db, lab, 5)
    assert PlanService(db)._capaciteit_in_woorden(plan) is None
