"""Tickets die samen in één werker draaien.

Een planning gaf elk ticket zijn eigen werker: twee runs in dezelfde container
vechten om dezelfde bestanden, processen en az-sessie. Dat blijft waar — maar
het is niet altijd een probleem, en soms is het precies wat je wilt: zes
tickets oppakken en ze per twee laten lopen, parallel waar het mag en
volgordelijk waar het moet.

Dat is een bundel: tickets met hetzelfde bundelnummer draaien samen in één
container, de bundels zelf gaan op volgorde. Wie ze bij elkaar zet, zegt
daarmee dat ze elkaar verdragen — LabX weet dat niet en gaat dat ook niet
raden.

Deze tests leggen de twee regels vast die dat werkend maken: een bundel bezet
samen één plek, en het tweede ticket van een bundel gaat naar de werker van
zijn bundelgenoot in plaats van op zoek naar een vrije.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.boards.plan_service import PlanService  # noqa: E402


def _svc(items=None, werker=None, bezet_door=None):
    svc = PlanService.__new__(PlanService)
    svc.items = lambda plan_id: list(items or [])
    svc._claim_worker = lambda plan: (werker, bezet_door)
    return svc


def _item(id_, *, bundel=None, state="waiting", worker_id=None):
    return SimpleNamespace(id=id_, bundel=bundel, state=state, worker_id=worker_id,
                           ticket_id=id_ * 10)


# ── de sleutel ──────────────────────────────────────────────────────────────

def test_zonder_bundelnummer_is_een_ticket_zijn_eigen_bundel():
    """Dit is wat maakt dat een bestaande planning zich niet anders gedraagt:
    elk ticket is dan zijn eigen bundel, dus elk ticket krijgt zijn eigen
    werker — precies zoals voorheen."""
    assert PlanService.bundelsleutel(_item(7)) == "item:7"
    assert PlanService.bundelsleutel(_item(8)) == "item:8"


def test_hetzelfde_nummer_is_dezelfde_bundel():
    assert (PlanService.bundelsleutel(_item(1, bundel=2))
            == PlanService.bundelsleutel(_item(9, bundel=2)))


def test_bundel_nul_telt_niet_als_bundel():
    """0 en None betekenen allebei 'alleen'. Een scherm dat een leeg veld als 0
    doorstuurt, mag niet per ongeluk alle tickets in één bundel gooien."""
    assert PlanService.bundelsleutel(_item(3, bundel=0)) == "item:3"


# ── waar een ticket terechtkomt ─────────────────────────────────────────────

def test_eerste_van_een_bundel_claimt_een_vrije_werker():
    werker = SimpleNamespace(id=42)
    svc = _svc(items=[_item(1, bundel=1)], werker=werker)
    mag, worker_id, bezet = svc._plek_voor_item(SimpleNamespace(id=1), _item(1, bundel=1))
    assert (mag, worker_id, bezet) == (True, 42, None)


def test_tweede_van_een_bundel_gaat_naar_de_werker_van_zijn_genoot():
    """De kern van het bundelen. Zonder dit zou het tweede ticket een NIEUWE
    werker claimen — en dan is een bundel niets anders dan gewoon parallel
    draaien."""
    lopend = _item(1, bundel=3, state="running", worker_id=7)
    svc = _svc(items=[lopend, _item(2, bundel=3)],
               werker=SimpleNamespace(id=99))  # deze zou hij NIET moeten pakken
    svc.db = SimpleNamespace()
    import services.lab.lab_service as ls
    origineel = ls.LabService
    try:
        ls.LabService = lambda db: SimpleNamespace(touch_worker=lambda _id: None)
        mag, worker_id, bezet = svc._plek_voor_item(SimpleNamespace(id=1), _item(2, bundel=3))
    finally:
        ls.LabService = origineel
    assert mag is True
    assert worker_id == 7, "hij hoort bij zijn bundelgenoot te gaan zitten"


def test_een_ticket_van_een_ANDERE_bundel_krijgt_gewoon_een_eigen_werker():
    lopend = _item(1, bundel=3, state="running", worker_id=7)
    svc = _svc(items=[lopend, _item(2, bundel=4)], werker=SimpleNamespace(id=99))
    mag, worker_id, _ = svc._plek_voor_item(SimpleNamespace(id=1), _item(2, bundel=4))
    assert (mag, worker_id) == (True, 99)


def test_geen_werker_vrij_betekent_wachten_en_geen_stille_start():
    svc = _svc(items=[], werker=None, bezet_door=12)
    mag, worker_id, bezet = svc._plek_voor_item(SimpleNamespace(id=1), _item(1))
    assert mag is False and bezet == 12


def test_bundelgenoot_zonder_werker_neemt_dezelfde_plek():
    """Een lab dat bij de allereerste start nog geen werkers had, draait zijn
    ticket zonder werker-id (dat is dan 'de container van het lab'). Zijn
    bundelgenoot hoort dáár ook te landen, en niet te gaan wachten."""
    lopend = _item(1, bundel=5, state="running", worker_id=None)
    svc = _svc(items=[lopend, _item(2, bundel=5)], werker=None, bezet_door=3)
    mag, worker_id, _ = svc._plek_voor_item(SimpleNamespace(id=1), _item(2, bundel=5))
    assert (mag, worker_id) == (True, None)


# ── hoeveel plekken een planning gebruikt ───────────────────────────────────

class NepDb:
    def __init__(self, lab=None, board=None):
        self._lab, self._board = lab, board

    def get(self, model, _id):
        return self._board if model.__name__ == "Board" else self._lab


def test_de_ruimte_telt_bundels_en_geen_tickets():
    """Drie tickets in één bundel zijn samen één plek. Zou dit tickets tellen,
    dan zou een bundel van drie in een lab met drie werkers alle ruimte
    opeten terwijl hij er maar één container gebruikt."""
    svc = PlanService.__new__(PlanService)
    svc.db = NepDb(lab=SimpleNamespace(max_workers=3), board=SimpleNamespace(lab_id="lab-1"))
    plan = SimpleNamespace(max_parallel=None, board_id=1)
    lopend = [_item(1, bundel=1, state="running"), _item(2, bundel=1, state="running"),
              _item(3, bundel=1, state="running")]
    bundels = {PlanService.bundelsleutel(i) for i in lopend}
    assert len(bundels) == 1
    assert svc._vrije_ruimte(plan, len(bundels)) == 2


# ── bundels zetten ──────────────────────────────────────────────────────────

class ZetDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def test_bundels_zetten_slaat_een_lopend_ticket_over():
    """Een lopend ticket zit al ergens. Zijn bundel verplaatsen zou betekenen
    dat het volgens de administratie ineens in een andere container draait dan
    waar het echt is."""
    lopend = _item(1, state="running")
    wacht = _item(2)
    svc = PlanService.__new__(PlanService)
    svc.db = ZetDb()
    svc.items = lambda plan_id: [lopend, wacht]
    svc.set_bundels(1, {1: 5, 2: 5})
    assert lopend.bundel is None, "een lopend ticket mag niet verhuizen"
    assert wacht.bundel == 5


@pytest.mark.parametrize("waarde", [None, 0, ""])
def test_leeg_bundelnummer_betekent_alleen(waarde):
    it = _item(2, bundel=4)
    svc = PlanService.__new__(PlanService)
    svc.db = ZetDb()
    svc.items = lambda plan_id: [it]
    svc.set_bundels(1, {2: waarde})
    assert it.bundel is None


# ── en dezelfde weg, maar dan door de echte database ────────────────────────

def test_bundels_overleven_de_database_en_komen_terug_in_de_uitvoer():
    """De unittests hierboven werken met nepobjecten. Deze legt vast dat de
    kolom er echt is, dat een bundelnummer bewaard blijft en dat het scherm het
    terugkrijgt — anders sleep je in de UI iets wat na een verversing weg is."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    import models.board  # noqa: F401  (Ticket + Board horen bij de tabellen)
    import models.plan  # noqa: F401
    from models.board import Board, Ticket
    from models.plan import PlanClaim, TicketPlan, TicketPlanItem

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Board.__table__, Ticket.__table__,
        TicketPlan.__table__, TicketPlanItem.__table__, PlanClaim.__table__,
    ])
    db = sessionmaker(bind=engine)()
    nu = "2026-09-23T12:00:00+00:00"
    db.add(Board(id=1, name="Bord", columns=["todo"], created_at=nu, updated_at=nu))
    for tid in (1, 2, 3):
        db.add(Ticket(id=tid, board_id=1, key=f"T-{tid}", title=f"Ticket {tid}",
                      status="todo", position=float(tid), created_at=nu, updated_at=nu))
    db.add(TicketPlan(id=1, board_id=1, name="Plan", state="draft",
                      created_at=nu, updated_at=nu))
    for tid in (1, 2, 3):
        db.add(TicketPlanItem(id=tid, plan_id=1, ticket_id=tid, position=float(tid * 100),
                              state="waiting"))
    db.commit()

    svc = PlanService(db)
    svc.set_bundels(1, {1: 1, 2: 1, 3: None})
    uit = svc.to_dict(db.get(TicketPlan, 1))
    bundels = {r["ticket_key"]: r["bundel"] for r in uit["items"]}
    assert bundels == {"T-1": 1, "T-2": 1, "T-3": None}

    # En losmaken werkt ook echt los.
    svc.set_bundels(1, {2: None})
    uit = svc.to_dict(db.get(TicketPlan, 1))
    assert {r["ticket_key"]: r["bundel"] for r in uit["items"]}["T-2"] is None
    db.close()
