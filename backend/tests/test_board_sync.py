"""Wat de sync wel en niet naar de bron mag sturen.

Deze tests leggen vier bugs vast die samen één oorzaak hadden: de push stuurde
bij élke lokale wijziging alle velden mee, en de pull trok bij élke sync alles
terug. Eén ticket naar een andere kolom slepen herschreef daardoor ook de
omschrijving (plat, zonder opmaak), wiste de toewijzing, kon een nieuwere
Jira-versie overschrijven met een oudere, en trok het ticket meteen weer terug
naar de eerste kolom.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.boards.sync.base import ExternalItem  # noqa: E402
from services.boards.sync_service import BoardSyncService, _gelijk  # noqa: E402


def _board():
    return SimpleNamespace(
        id=1, name="Swinkels", provider="jira", columns=[
            {"key": "todo", "name": "Te doen"},
            {"key": "agent", "name": "Voor de agent"},
            {"key": "review", "name": "Review"},
            {"key": "done", "name": "Klaar"},
        ],
        provider_config={"state_map": {"todo": ["Nog doen"], "review": ["Test"],
                                       "done": ["Gereed"]}})


def _ticket(**kw):
    basis = dict(id=1, board_id=1, key="SWI-1", title="Titel", description="# Kop\n\n- punt",
                 acceptance_criteria=None, priority="normal", assignee="Nick Du Chatinier",
                 labels=[], status="todo", position=1.0, dirty=False,
                 external_id="1", external_key="BICC-1", external_snapshot=None)
    basis.update(kw)
    return SimpleNamespace(**basis)


def _item(**kw):
    basis = dict(external_id="1", external_key="BICC-1", title="Titel",
                 description="# Kop\n\n- punt", acceptance_criteria=None,
                 priority="normal", assignee="Nick Du Chatinier", labels=[],
                 state="Nog doen", rev="r1")
    basis.update(kw)
    return ExternalItem(**basis)


def _svc():
    """De service zonder database. `boards` moet er wél zijn: een ticket dat
    naar een andere kolom gaat krijgt een nieuwe positie in die kolom."""
    svc = BoardSyncService.__new__(BoardSyncService)
    svc.boards = SimpleNamespace(next_position=lambda board_id, status: 1.0)
    return svc


# ── wat er NIET gepusht mag worden ──────────────────────────────────────────

def test_kolomverhuizing_pusht_geen_omschrijving():
    """De kern van het probleem: een ticket verslepen mocht nooit de tekst in
    Jira herschrijven."""
    svc, board = _svc(), _board()
    item = _item()
    t = _ticket(external_snapshot=svc._snapshot(item), status="review", dirty=True)
    assert svc._lokale_wijzigingen(t) == {}
    assert svc._te_pushen_status(board, t) == "Test"


def test_alleen_het_gewijzigde_veld_gaat_mee():
    svc, board = _svc(), _board()
    t = _ticket(external_snapshot=_svc()._snapshot(_item()), title="Andere titel")
    assert svc._lokale_wijzigingen(t) == {"title": "Andere titel"}
    assert svc._te_pushen_status(board, t) is None


def test_zonder_ijkpunt_wordt_er_niets_gestuurd():
    """Tickets van vóór deze werkwijze: we weten niet wat er lokaal veranderd
    is, dus is niets versturen het enige veilige antwoord."""
    svc, board = _svc(), _board()
    t = _ticket(external_snapshot=None, description="totaal andere tekst", status="review")
    assert svc._lokale_wijzigingen(t) == {}
    assert svc._te_pushen_status(board, t) is None


def test_geen_transitie_naar_dezelfde_status():
    svc, board = _svc(), _board()
    t = _ticket(external_snapshot=svc._snapshot(_item(state="Nog doen")), status="todo")
    assert svc._te_pushen_status(board, t) is None


def test_agentkolom_stuurt_geen_status():
    """De agent-kolom bestaat in Jira niet; er valt niets te melden."""
    svc, board = _svc(), _board()
    t = _ticket(external_snapshot=svc._snapshot(_item()), status="agent")
    assert svc._te_pushen_status(board, t) is None


# ── wat de pull NIET mag overschrijven ──────────────────────────────────────

def test_ticket_blijft_in_de_agentkolom():
    """De bug die het board onbruikbaar maakte: alles in de agent-kolom stond
    na één sync weer in To Do."""
    svc, board = _svc(), _board()
    item = _item(state="Nog doen")
    t = _ticket(status="agent", external_snapshot=svc._snapshot(item))
    svc._apply_external_fields(board, t, item, "todo")
    assert t.status == "agent"


def test_agentkolom_wijkt_wel_als_jira_de_status_verandert():
    svc, board = _svc(), _board()
    t = _ticket(status="agent", external_snapshot=svc._snapshot(_item(state="Nog doen")))
    svc._apply_external_fields(board, t, _item(state="Gereed"), "done")
    assert t.status == "done"


def test_zonder_ijkpunt_blijft_een_labx_eigen_kolom_staan():
    svc, board = _svc(), _board()
    t = _ticket(status="agent", external_snapshot=None)
    svc._apply_external_fields(board, t, _item(state="Nog doen"), "todo")
    assert t.status == "agent"


def test_zonder_ijkpunt_is_de_bron_leidend_in_een_gekoppelde_kolom():
    svc, board = _svc(), _board()
    t = _ticket(status="todo", external_snapshot=None)
    svc._apply_external_fields(board, t, _item(state="Test"), "review")
    assert t.status == "review"


def test_lokale_bewerking_overleeft_een_pull():
    """Jira veranderde niets, LabX wel: de lokale tekst is de jongste."""
    svc, board = _svc(), _board()
    item = _item()
    t = _ticket(external_snapshot=svc._snapshot(item), description="mijn nieuwe tekst")
    svc._apply_external_fields(board, t, item, "todo")
    assert t.description == "mijn nieuwe tekst"


def test_bron_wint_als_de_bron_wel_veranderde():
    svc, board = _svc(), _board()
    t = _ticket(external_snapshot=svc._snapshot(_item()), description="mijn tekst")
    svc._apply_external_fields(board, t, _item(description="Jira's nieuwere tekst"), "todo")
    assert t.description == "Jira's nieuwere tekst"


def test_pull_zet_het_ijkpunt():
    svc, board = _svc(), _board()
    t = _ticket()
    svc._apply_external_fields(board, t, _item(title="Nieuw"), "todo")
    assert t.external_snapshot["title"] == "Nieuw"


def test_leeg_veld_van_de_bron_wist_niets():
    """Jira zonder acceptance_field geeft None terug; dat mag de lokale
    criteria niet wissen."""
    svc, board = _svc(), _board()
    t = _ticket(acceptance_criteria="- toetsbaar", external_snapshot=svc._snapshot(_item()))
    svc._apply_external_fields(board, t, _item(acceptance_criteria=None), "todo")
    assert t.acceptance_criteria == "- toetsbaar"


@pytest.mark.parametrize("a,b", [(None, ""), ("  x ", "x"), ([], None), (["a"], ["a"])])
def test_gelijk_kijkt_door_vormverschillen_heen(a, b):
    assert _gelijk(a, b)


def test_gelijk_ziet_echte_verschillen():
    assert not _gelijk("x", "y")
    assert not _gelijk(["a"], ["a", "b"])
