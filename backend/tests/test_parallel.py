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
