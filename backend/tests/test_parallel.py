"""Tickets naast elkaar, en wat ze uit elkaar houdt.

Tot nu toe deed een planning één ticket tegelijk. Vijf entiteiten die niets met
elkaar te maken hebben, stonden dus vijf keer achter elkaar te wachten — en een
agent die op een Fabric-pipeline van een uur wachtte, legde met
`board__wait_until` de hele rij stil in plaats van alleen zichzelf.

Wat parallel draaien mogelijk maakt zonder dat ze elkaar slopen, is de claim:
de agent meldt waar hij aan zit, en LabX start geen ticket dat eerder aan
hetzelfde zat. Deze tests leggen die twee regels vast.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.boards.plan_service import PlanService  # noqa: E402


def _svc():
    return PlanService.__new__(PlanService)


# ── hoe breed mag een planning worden ───────────────────────────────────────

class NepDb:
    def __init__(self, lab=None, board=None):
        self._lab, self._board = lab, board
    def get(self, model, _id):
        return self._board if model.__name__ == "Board" else self._lab


def test_zonder_eigen_plafond_bepaalt_het_lab_de_breedte():
    svc = _svc()
    svc.db = NepDb(lab=SimpleNamespace(max_workers=4),
                   board=SimpleNamespace(lab_id="lab-1"))
    plan = SimpleNamespace(max_parallel=None, board_id=1)
    assert svc._vrije_ruimte(plan, lopend=0) == 4
    assert svc._vrije_ruimte(plan, lopend=3) == 1
    assert svc._vrije_ruimte(plan, lopend=4) == 0


def test_eigen_plafond_wint_van_het_lab():
    svc = _svc()
    svc.db = NepDb(lab=SimpleNamespace(max_workers=8),
                   board=SimpleNamespace(lab_id="lab-1"))
    plan = SimpleNamespace(max_parallel=2, board_id=1)
    assert svc._vrije_ruimte(plan, lopend=0) == 2
    assert svc._vrije_ruimte(plan, lopend=2) == 0


def test_zonder_lab_blijft_het_bij_een():
    """Een bord zonder lab kan geen werkers hebben; dan is parallel draaien
    geen keuze maar een fout die je wilt voorkomen."""
    svc = _svc()
    svc.db = NepDb(lab=None, board=SimpleNamespace(lab_id=None))
    assert svc._vrije_ruimte(SimpleNamespace(max_parallel=None, board_id=1), lopend=0) == 1


# ── claims ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("invoer,verwacht", [
    ("Fabric:ACC:PL_RUN_SILVER", "fabric:acc:pl_run_silver"),
    ("  repo:/workspace/silver  ", "repo:/workspace/silver"),
    ("tabel:   dbo.product", "tabel: dbo.product"),
    ("", ""),
    (None, ""),
])
def test_bronnen_worden_genormaliseerd(invoer, verwacht):
    """Twee agents die dezelfde pipeline anders schrijven, moeten toch botsen —
    anders is de claim een schijnzekerheid."""
    assert PlanService._normaliseer(invoer) == verwacht


def test_normalisatie_kapt_af_maar_blijft_vergelijkbaar():
    lang = "fabric:" + "x" * 400
    uit = PlanService._normaliseer(lang)
    assert len(uit) == 255
    assert PlanService._normaliseer(lang) == uit  # stabiel


# ── de eigen werkmap bij een planning met gescheiden werk ───────────────────

def test_werkmap_is_per_planning_en_per_ticket():
    plan = SimpleNamespace(id=7)
    a = PlanService.werkmap(plan, SimpleNamespace(key="SWI-88"))
    b = PlanService.werkmap(plan, SimpleNamespace(key="SWI-89"))
    assert a == "/workspace/.plan-7/SWI-88"
    assert a != b


@pytest.mark.parametrize("sleutel", ["../../etc/x", "a/b", "x;rm -rf /", "", None])
def test_werkmap_blijft_een_map_onder_de_planning(sleutel):
    """Waar het om gaat is niet hoe de naam eruitziet maar dat het ÉÉN map
    blijft: geen schuine strepen erin, dus geen pad dat ergens anders uitkomt."""
    map_ = PlanService.werkmap(SimpleNamespace(id=1), SimpleNamespace(key=sleutel))
    prefix = "/workspace/.plan-1/"
    assert map_.startswith(prefix)
    naam = map_[len(prefix):]
    assert naam, "een lege naam zou de planningsmap zelf zijn"
    assert "/" not in naam


def test_gedeelde_planning_krijgt_geen_werkmap_instructie():
    svc = _svc()
    plan = SimpleNamespace(id=1, instruction="Let op TST", workspace_mode="gedeeld")
    tekst = svc._instructie_voor(plan, SimpleNamespace(key="SWI-1"))
    assert tekst == "Let op TST"


def test_aparte_planning_vertelt_waar_je_moet_werken():
    svc = _svc()
    plan = SimpleNamespace(id=3, instruction="Let op TST", workspace_mode="apart")
    tekst = svc._instructie_voor(plan, SimpleNamespace(key="SWI-1"))
    assert "Let op TST" in tekst
    assert "/workspace/.plan-3/SWI-1" in tekst
    # De agent moet weten WAAROM, anders houdt hij zich er niet aan zodra het
    # onhandig uitkomt.
    assert "tegelijk" in tekst


def test_lege_instructie_levert_none_op():
    svc = _svc()
    plan = SimpleNamespace(id=1, instruction=None, workspace_mode="gedeeld")
    assert svc._instructie_voor(plan, SimpleNamespace(key="SWI-1")) is None


# ── eigen claims mogen je niet blokkeren ────────────────────────────────────
#
# Op 11-09-2026 legde dit een planning van acht tickets volledig stil. Alle
# acht hadden `board__wait_until` aangeroepen, alle acht hielden hun claims
# vast (juist de bedoeling: wie een uur op een pipeline wacht, wil niet dat er
# iemand anders in zit), en alle acht botsten daardoor bij het hervatten met
# zichzelf. `bezet_door` was het ticket zélf.

BORD = SimpleNamespace(id=1, lab_id="lab-1", columns=[], agent_done_column=None)


class ClaimDb:
    """Genoeg database om _claim_botsing te laten lopen. Drie soorten vragen:
    het bord opzoeken, de borden van hetzelfde lab, en de claims."""
    def __init__(self, claims, items=None):
        self.claims, self.items = claims, items or []
    def get(self, model, _id):
        # _claim_botsing zoekt het bord op, en daarna het ticket dat de
        # botsende claim vasthoudt (om zijn sleutel te kunnen noemen).
        if getattr(model, "__name__", "") == "Ticket":
            return SimpleNamespace(id=_id, key=f"KRI-{_id}")
        return BORD
    def query(self, *models):
        return ClaimQuery(self.claims, self.items, models)


class ClaimQuery:
    def __init__(self, claims, items, models):
        self.claims, self.items, self.models = claims, items, models
    def filter(self, *a, **k): return self
    def join(self, *a, **k): return self
    def all(self):
        naam = getattr(self.models[0], "__name__", "")
        if naam == "Board":
            return [BORD]
        # _actieve_claims vraagt (PlanClaim, TicketPlanItem); _claim_botsing
        # vraagt alleen PlanClaim.
        if len(self.models) > 1:
            return list(zip(self.claims, self.items))
        return self.claims


def _claim(bron, ticket_id, item_id):
    return SimpleNamespace(resource=bron, ticket_id=ticket_id, plan_item_id=item_id,
                           created_at="2026-09-11T18:00:00+00:00")


def test_eigen_claim_blokkeert_niet():
    svc = _svc()
    eigen = _claim("fabric:acc:pl_run_silver", ticket_id=20, item_id=36)
    item = SimpleNamespace(id=36, state="waiting")
    svc.db = ClaimDb([eigen], [item])
    ticket = SimpleNamespace(id=20, key="KRI-20")
    assert svc._claim_botsing(SimpleNamespace(board_id=1), ticket, item) is None


def test_claim_van_een_ander_blokkeert_wel():
    svc = _svc()
    vreemd = _claim("fabric:acc:pl_run_silver", ticket_id=12, item_id=30)
    eigen = _claim("fabric:acc:pl_run_silver", ticket_id=20, item_id=36)
    item = SimpleNamespace(id=36, state="running")
    # De geschiedenis van dit ticket bevat de bron; de actieve claim ligt bij
    # een ander item.
    svc.db = ClaimDb([eigen, vreemd],
                     [SimpleNamespace(id=36, state="running"),
                      SimpleNamespace(id=30, state="waiting")])
    ticket = SimpleNamespace(id=20, key="KRI-20")
    botsing = svc._claim_botsing(SimpleNamespace(board_id=1), ticket, item)
    assert botsing is not None
    assert botsing["resource"] == "fabric:acc:pl_run_silver"


def test_zonder_geschiedenis_geen_botsing():
    svc = _svc()
    svc.db = ClaimDb([], [])
    assert svc._claim_botsing(SimpleNamespace(board_id=1),
                              SimpleNamespace(id=99, key="X"), None) is None


# ── eindpunten: de planning kijkt ook naar het TICKET ───────────────────────

def test_klaar_kolommen_komen_uit_het_bord():
    svc = _svc()
    board = SimpleNamespace(
        columns=[{"key": "todo"}, {"key": "review"}, {"key": "done", "is_done": True}],
        agent_done_column="review")
    assert svc._klaar_kolommen(board) == {"done", "review"}


def test_geen_bord_geen_klaar_kolommen():
    assert _svc()._klaar_kolommen(None) == set()


# Een item dat in deze planning heeft gedraaid; zie de tests onderaan voor
# waarom dat onderscheid ertoe doet.
GEDRAAID = SimpleNamespace(started_at="2026-09-11T18:47:50+00:00", run_id="r1")


def test_ticket_in_een_klaar_kolom_telt_als_afgerond():
    """De reden dat een planning eeuwig bleef lopen: de agent zette het ticket
    zelf op Klaar, maar de planning wist dat alleen via haar eigen afloop-hook
    — en die deed niets voor een item dat geparkeerd stond."""
    svc = _svc()
    board = SimpleNamespace(columns=[{"key": "done", "is_done": True}], agent_done_column=None)
    svc.db = SimpleNamespace(get=lambda model, _id: board)
    ticket = SimpleNamespace(status="done", key="KRI-20")
    assert svc._afgerond_buitenom(SimpleNamespace(board_id=1), GEDRAAID, ticket) is True


def test_ticket_dat_nog_loopt_telt_niet_als_afgerond():
    svc = _svc()
    board = SimpleNamespace(columns=[{"key": "done", "is_done": True}], agent_done_column=None)
    svc.db = SimpleNamespace(get=lambda model, _id: board)
    for kolom in ("todo", "in_progress", "review", "blocked"):
        ticket = SimpleNamespace(status=kolom, key="X")
        assert svc._afgerond_buitenom(SimpleNamespace(board_id=1), GEDRAAID, ticket) is False


def test_verdwenen_ticket_is_niet_afgerond():
    assert _svc()._afgerond_buitenom(SimpleNamespace(board_id=1), GEDRAAID, None) is False


# ── een nieuwe planning vinkt niet meteen alles af ──────────────────────────
#
# Een planning kan buiten zichzelf om te weten komen dat een ticket klaar is:
# de agent parkeerde met `board__wait_until`, rondde daarna af en zette het
# ticket op Klaar. Dat kreeg de planning niet mee en bleef eeuwig hangen.
#
# Maar die toets sloeg te breed toe. Wie acht AFGERONDE tickets inplant om ze
# te laten controleren, kreeg ze meteen alle acht op 'done' — precies het
# tegenovergestelde van wat hij vroeg. Het verschil: heeft dit item in DEZE
# planning gedraaid?

def _bord_met_klaarkolom():
    return SimpleNamespace(columns=[{"key": "todo"}, {"key": "done", "is_done": True}],
                           agent_done_column=None)


def test_nieuw_ticket_op_klaar_wordt_niet_afgevinkt():
    """Acht afgeronde tickets inplannen om ze te controleren: die moeten
    gewoon draaien."""
    svc = _svc()
    svc.db = SimpleNamespace(get=lambda model, _id: _bord_met_klaarkolom())
    item = SimpleNamespace(started_at=None, run_id=None)
    ticket = SimpleNamespace(status="done", key="KRI-20")
    assert svc._afgerond_buitenom(SimpleNamespace(board_id=1), item, ticket) is False


def test_gedraaid_ticket_op_klaar_wordt_wel_afgevinkt():
    """Het geval waarvoor de toets bestaat: gestart, geparkeerd, en daarna door
    de agent zelf afgerond."""
    svc = _svc()
    svc.db = SimpleNamespace(get=lambda model, _id: _bord_met_klaarkolom())
    item = SimpleNamespace(started_at="2026-09-11T18:47:50+00:00", run_id="r1")
    ticket = SimpleNamespace(status="done", key="KRI-20")
    assert svc._afgerond_buitenom(SimpleNamespace(board_id=1), item, ticket) is True


def test_gedraaid_ticket_dat_nog_niet_klaar_is_blijft_lopen():
    svc = _svc()
    svc.db = SimpleNamespace(get=lambda model, _id: _bord_met_klaarkolom())
    item = SimpleNamespace(started_at="2026-09-11T18:47:50+00:00", run_id="r1")
    assert svc._afgerond_buitenom(SimpleNamespace(board_id=1), item,
                                  SimpleNamespace(status="todo", key="X")) is False
