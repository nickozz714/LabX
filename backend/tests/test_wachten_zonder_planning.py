"""Wachten op iets dat minuten duurt, ook zonder planning.

`board__wait_until` parkeerde alleen een planning-item. Draaide een ticket
buiten een planning — de gewone "Agent starten"-knop — dan weigerde de tool,
met een foutbericht dat de agent aanraadde "binnen deze run (kort)" te wachten.
Dat deed hij dan ook: een `sleep` van minuten in het lab, met een werker eraan
vast en zijn context eraan op. Op 2026-10-02 stond dat live te gebeuren
(`timeout -k 2 295 sh -lc "... sleep 260; ..."`).

Deze tests leggen de drie dingen vast die dat oplossen: parkeren werkt ook
zonder planning, de afloop-hook laat zo'n geparkeerd ticket met rust in plaats
van het naar de klaar-kolom te schuiven, en de hervatlus pakt het pas op als de
tijd echt om is.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.boards.plan_service import PlanService  # noqa: E402


def _nu(minuten: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minuten)).isoformat()


class _Db:
    """Net genoeg database om het parkeren te volgen."""

    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _ticket(**kw):
    basis = dict(id=7, key="KRI-114", board_id=2, agent_state="running",
                 agent_run_id="run-1", agent_resume_at=None,
                 agent_wait_reason=None, updated_at=_nu(-60))
    basis.update(kw)
    return SimpleNamespace(**basis)


def _svc(ticket, monkeypatch):
    svc = PlanService.__new__(PlanService)
    svc.db = _Db()
    svc._lopend_item = lambda lab_id, worker_id: None      # geen planning
    svc._lopend_ticket = lambda lab_id, worker_id: ticket
    # De opmerking is bijzaak voor deze test, maar hij moet wél geplaatst
    # worden: dat is straks de enige context bij het hervatten.
    geplaatst = []
    import services.boards.board_service as bs
    monkeypatch.setattr(
        bs, "BoardService",
        lambda db: SimpleNamespace(
            add_comment=lambda tid, **kw: geplaatst.append((tid, kw.get("body", "")))))
    return svc, geplaatst


def test_een_los_ticket_kan_ook_wachten(monkeypatch):
    ticket = _ticket()
    svc, geplaatst = _svc(ticket, monkeypatch)

    uit = svc.wacht_tot(lab_id="lab-1", worker_id=4, minuten=30,
                        reden="de PL_SILVER-run van Krimpenerwaard")

    assert "error" not in uit, "wachten buiten een planning mag niet meer weigeren"
    assert ticket.agent_state == "waiting"
    assert ticket.agent_resume_at is not None
    assert ticket.agent_resume_at > _nu(25)        # ergens rond de 30 minuten
    assert ticket.agent_resume_at < _nu(35)
    assert "PL_SILVER" in (ticket.agent_wait_reason or "")
    # De agent moet weten dat hij nu moet afronden, niet blijven hangen.
    assert "af" in uit["result"].lower()
    assert geplaatst, "er hoort een activiteit-opmerking bij het ticket te staan"


def test_zonder_herkenbaar_ticket_blijft_het_een_nette_weigering(monkeypatch):
    svc, _ = _svc(_ticket(), monkeypatch)
    svc._lopend_ticket = lambda lab_id, worker_id: None

    uit = svc.wacht_tot(lab_id="lab-1", worker_id=4, minuten=30, reden="iets")

    assert "error" in uit
    # Geen verwijzing meer naar "wacht binnen deze run": dat was precies de zin
    # die de agent de sleep in stuurde.
    assert "sleep" not in uit["error"].lower()
    assert "opmerking" in uit["error"].lower()


def test_de_afloophook_laat_een_geparkeerd_ticket_staan():
    """Een run die eindigt terwijl het ticket geparkeerd staat, is niet klaar."""
    from services.boards import agent_work

    ticket = _ticket(agent_state="waiting", agent_resume_at=_nu(30))

    class _Q:
        def __init__(self, rij):
            self._rij = rij

        def filter(self, *a, **k):
            return self

        def first(self):
            return self._rij

    class _DbMetTicket(_Db):
        def query(self, model):
            # Het planning-item bestaat niet; het ticket wel.
            return _Q(None if model.__name__ == "TicketPlanItem" else ticket)

    assert agent_work._wacht_nog(_DbMetTicket(), SimpleNamespace(id="run-1")) is True


def test_een_verlopen_wachttijd_telt_niet_meer_als_wachten():
    from services.boards import agent_work

    ticket = _ticket(agent_state="waiting", agent_resume_at=_nu(-5))

    class _Q:
        def __init__(self, rij):
            self._rij = rij

        def filter(self, *a, **k):
            return self

        def first(self):
            return self._rij

    class _DbMetTicket(_Db):
        def query(self, model):
            return _Q(None if model.__name__ == "TicketPlanItem" else ticket)

    # Tijd om: de hook mag het ticket nu gewoon afhandelen, anders blijft een
    # ticket waarvan het hervatten mislukte voor eeuwig "aan het wachten".
    assert agent_work._wacht_nog(_DbMetTicket(), SimpleNamespace(id="run-1")) is False
