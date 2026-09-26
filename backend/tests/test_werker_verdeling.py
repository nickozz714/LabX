"""Werk verdelen over de werkers, ook buiten een planning om.

Waargenomen: drie tickets met de hand starten vanaf het ticket zelf leverde
drie runs in ÉÉN werker op, terwijl werker 2 en 3 niets deden. De oorzaak was
een gat in de verdeling: alleen de planner koos een werker (`_claim_worker`),
de knop op het ticket gaf geen `lab_worker_id` mee en dan valt alles terug op
de container van het lab zelf.

Daar zat meteen de spiegelfout aan vast: de planner telde bezetting uit
`ticket_plan_items`, en een handmatig gestarte run heeft geen planningsregel.
Die runs waren dus onzichtbaar, en een planning kon een werker pakken waar al
iemand aan het werk was — twee runs in dezelfde container vechten om dezelfde
bestanden, processen en az-sessie.

Beide kanten lopen nu via LabService: één plek die weet wie bezet is.

Later kwam daar `sessies_per_werker` bij: een lab is een sandbox-pc, en op een
pc kun je twee keer Claude draaien. "Bezet" betekent sindsdien VOL, niet "er
draait iets", en werk gaat naar de rustigste werker in plaats van naar de
eerste vrije — anders zou een tweede sessie meteen bovenop de eerste landen
terwijl werker 2 leegstaat.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.lab_service import LabService  # noqa: E402


def _werker(wid, index):
    return SimpleNamespace(id=wid, index=index, status="running",
                           container_id=f"c{wid}", provision_status="ok")


def _svc(werkers, bezet=None, bezetting=None, ruimte=1):
    """`bezet` is de korte vorm: die werkers zitten vol. `bezetting` is het
    echte getal per werker, nodig zodra er meer dan één sessie in mag."""
    telling = dict(bezetting or {})
    for wid in (bezet or set()):
        telling[wid] = ruimte
    svc = LabService.__new__(LabService)
    svc.claimbare_werkers = lambda _p: werkers
    svc.bezetting = lambda _lab_id: dict(telling)
    svc.touch_worker = lambda _wid: None
    return svc


LAB_RUIM = SimpleNamespace(id="lab-1", sessies_per_werker=2)


LAB = SimpleNamespace(id="lab-1")


def test_de_eerste_vrije_werker_wordt_gekozen():
    svc = _svc([_werker(4, 1), _werker(5, 2), _werker(6, 3)], bezet={4})
    assert svc.vrije_werker(LAB).id == 5


def test_drie_starts_verdelen_zich_over_drie_werkers():
    """Het waargenomen geval: elke volgende start pakt de volgende werker,
    omdat de vorige run als bezet meetelt."""
    werkers = [_werker(4, 1), _werker(5, 2), _werker(6, 3)]
    bezet = set()
    gekozen = []
    for _ in range(3):
        w = _svc(werkers, bezet).vrije_werker(LAB)
        gekozen.append(w.id)
        bezet.add(w.id)          # de run die net startte bezet hem
    assert gekozen == [4, 5, 6]


def test_alles_bezet_geeft_niets_terug():
    """Geen vrije werker is geen fout: de aanroeper beslist dan zelf. Een
    handmatige start valt terug op werker 1 (je drukte bewust op start), een
    planning gaat wachten."""
    svc = _svc([_werker(4, 1), _werker(5, 2)], bezet={4, 5})
    assert svc.vrije_werker(LAB) is None


def test_zonder_werkers_geen_keuze():
    """Een lab van vóór de autoscaler, of een lab dat nog opstart."""
    assert _svc([], bezet=set()).vrije_werker(LAB) is None


def test_een_handmatige_start_kiest_zelf_een_werker():
    """De regel die ontbrak. Zonder dit kreeg background_runs.start() nooit een
    `lab_worker_id` en landde alles in de container van het lab zelf."""
    import inspect

    from services.boards.agent_work import start_ticket_run

    bron = inspect.getsource(start_ticket_run)
    assert "if lab_worker_id is None:" in bron
    assert "vrije_werker(lab)" in bron


def test_een_handmatige_start_tikt_de_autoscaler_aan():
    """Het gat dat na de eerste reparatie overbleef. De autoscaler werd alleen
    door de PLANNER aangeroepen, dus een lab waarop je met de hand werkte bleef
    op één werker hangen: de extra werkers werden na dertig minuten stilte
    opgeruimd en kwamen nooit terug. Dat was op 13-09-2026 precies wat er
    gebeurd was — Krimpenerwaard stond weer op één werker terwijl het plafond
    op drie stond.

    Deze run zelf wacht er niet op (een nieuwe container moet eerst ingericht
    worden, en dat duurt minuten); hij gaat op werker 1 en de vólgende start
    heeft er een."""
    import inspect

    from services.boards.agent_work import start_ticket_run

    bron = inspect.getsource(start_ticket_run)
    assert "ensure_extra_worker(lab.id)" in bron
    # Het bijschalen mag de start nooit tegenhouden.
    assert "except Exception" in bron.split("ensure_extra_worker")[1][:200]


def test_de_planner_telt_handmatige_runs_mee():
    """De spiegelfout: anders zet een planning werk op een werker waar al
    iemand zit, en zijn we terug bij twee runs in dezelfde container."""
    import inspect

    from services.boards.plan_service import PlanService

    bron = inspect.getsource(PlanService._claim_worker)
    assert "bezette_werkers(lab.id)" in bron


def test_bezetting_komt_uit_de_runs_en_niet_uit_de_planningen():
    """Dit is wat het gat dichtte: `background_runs` kent élke lopende run,
    `ticket_plan_items` alleen die van een planning."""
    import inspect

    bron = inspect.getsource(LabService.bezetting)
    assert "BackgroundRun" in bron
    assert "LabWorker.lab_id == lab_id" in bron, "bezetting moet per lab zijn"
    assert '"running", "queued"' in bron


# ── meer dan één sessie in dezelfde werker ──────────────────────────────────

def test_een_tweede_sessie_mag_erbij_als_het_lab_dat_toestaat():
    """Eén werker, ruimte voor twee: de tweede sessie hoeft niet te wachten."""
    svc = _svc([_werker(4, 1)], bezetting={4: 1}, ruimte=2)
    assert svc.vrije_werker(LAB_RUIM).id == 4


def test_een_tweede_sessie_gaat_liever_naar_een_lege_werker():
    """Anders zou "twee tegelijk" meteen gebeuren omdat werker 1 vooraan staat,
    terwijl werker 2 leegstaat. Delen is het vangnet, niet de eerste keuze."""
    svc = _svc([_werker(4, 1), _werker(5, 2)], bezetting={4: 1}, ruimte=2)
    assert svc.vrije_werker(LAB_RUIM).id == 5


def test_vol_is_vol_ook_met_ruimte_voor_twee():
    svc = _svc([_werker(4, 1), _werker(5, 2)], bezetting={4: 2, 5: 2}, ruimte=2)
    assert svc.vrije_werker(LAB_RUIM) is None


def test_standaard_blijft_een_sessie_per_werker():
    """Een lab dat niets ingesteld heeft, gedraagt zich zoals het altijd deed."""
    zonder = SimpleNamespace(id="lab-1")
    assert LabService.sessies_per_werker(zonder) == 1
    svc = _svc([_werker(4, 1)], bezetting={4: 1})
    assert svc.vrije_werker(zonder) is None


def test_een_werker_met_ruimte_over_mag_niet_opgeruimd_worden():
    """`bezette_werkers` betekent nu VOL. Het opruimen van werkers moet naar de
    andere vraag kijken — anders zet je een container weg waar iemand in
    werkt."""
    import inspect

    bron = inspect.getsource(LabService.scale)
    assert "_bezette_werkers" in bron
    assert "aantal > 0" in inspect.getsource(LabService.werkers_met_werk)
