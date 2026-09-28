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


# ── een ticket dat in LabX moet blijven ─────────────────────────────────────
#
# Niet elk ticket hoort bij de klant op het bord: eigen aantekeningen,
# vervolgwerk dat de agent zelf bedacht, iets dat je eerst wilt uitzoeken. Die
# naar Jira duwen maakt daar rommel die een ander moet opruimen.

def test_de_push_slaat_een_lokaal_gehouden_ticket_over():
    import inspect

    from services.boards.sync_service import BoardSyncService

    # Het duwen van één ticket staat apart, zodat de handmatige knop en de
    # automatische ronde niet uit elkaar kunnen lopen.
    bron = inspect.getsource(BoardSyncService._push_ticket)
    assert 'getattr(ticket, "sync_naar_bron", True)' in bron
    # `dirty` blijft staan, zodat hij alsnog meegaat als je het vinkje omzet.
    assert "ticket.dirty = False" not in bron.split("overgeslagen_lokaal")[1][:200]


def test_een_ticket_synchroniseert_standaard_wel():
    """Bestaande borden mogen hier niets van merken."""
    from models.board import Ticket

    assert Ticket.__table__.c.sync_naar_bron.default.arg is True


# ── één ticket los doorzetten ───────────────────────────────────────────────
#
# Een hele bordsynchronisatie is grof gereedschap als je net één ticket hebt
# aangepast: hij raakt alles aan, duurt langer, en als er iets misgaat staat
# jouw ticket tussen de rest.

@pytest.fixture()
def losdb():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models.board import Board, Ticket, TicketComment
    from models.lab import Lab

    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[
        Lab.__table__, Board.__table__, Ticket.__table__, TicketComment.__table__])
    s = sessionmaker(bind=motor)()
    s.add(Board(id=1, name="Swinkels", key_prefix="SWI", provider="jira",
                provider_config={"project_key": "BICC"}, sync_direction="two_way",
                columns=[{"key": "todo", "name": "Nog doen"}],
                created_at="nu", updated_at="nu"))
    s.commit()
    yield s
    s.close()


def _los_ticket(db, **velden):
    from models.board import Ticket

    basis = dict(board_id=1, key="SWI-1", title="Iets", status="todo",
                 priority="normal", labels=[], depends_on=[], position=1.0,
                 dirty=True, created_at="nu", updated_at="nu")
    basis.update(velden)
    t = Ticket(**basis)
    db.add(t)
    db.commit()
    return t


class _NepAdapter:
    def __init__(self):
        self.aangemaakt = []
        self.bijgewerkt = []
        self.opmerkingen = []

    async def create_item(self, **kw):
        from services.boards.sync.base import ExternalItem
        self.aangemaakt.append(kw)
        return ExternalItem(external_id="10001", external_key="BICC-7",
                            title=kw.get("title") or "", state="Nog doen")

    async def update_item(self, **kw):
        from services.boards.sync.base import ExternalItem
        self.bijgewerkt.append(kw)
        return ExternalItem(external_id=kw["external_id"], external_key="BICC-7",
                            title=kw.get("title") or "")

    async def add_comment(self, **kw):
        self.opmerkingen.append(kw)
        return "c1"


def _los_svc(db, adapter):
    from services.boards.sync_service import BoardSyncService

    svc = BoardSyncService(db)
    svc._adapter = lambda _b: adapter
    return svc


def test_een_nieuw_ticket_wordt_alleen_zelf_aangemaakt(losdb):
    import asyncio

    t = _los_ticket(losdb, item_type="Bug")
    _los_ticket(losdb, key="SWI-2", title="Een ander ticket")   # mag niet meegaan
    adapter = _NepAdapter()
    uit = asyncio.run(_los_svc(losdb, adapter).sync_ticket(t.id))

    assert uit["resultaat"] == "aangemaakt" and uit["ok"] is True
    assert uit["extern"] == "BICC-7"
    assert len(adapter.aangemaakt) == 1
    assert adapter.aangemaakt[0]["item_type"] == "Bug"      # het soort gaat mee
    losdb.refresh(t)
    assert t.dirty is False and t.external_key == "BICC-7"


def test_de_opmerkingen_van_dat_ticket_gaan_mee(losdb):
    """"Even doorzetten" zonder je laatste opmerking is precies niet wat je
    bedoelde."""
    import asyncio

    from models.board import TicketComment

    t = _los_ticket(losdb, external_id="10001", external_key="BICC-7",
                external_snapshot={"title": "Iets", "state": "Nog doen"})
    ander = _los_ticket(losdb, key="SWI-2", external_id="10002", external_key="BICC-8")
    for ticket, tekst in ((t, "van dit ticket"), (ander, "van het andere")):
        losdb.add(TicketComment(ticket_id=ticket.id, kind="comment", author="nick",
                                body=tekst, internal=False, pushed=False, created_at="nu"))
    losdb.commit()

    adapter = _NepAdapter()
    asyncio.run(_los_svc(losdb, adapter).sync_ticket(t.id))
    assert len(adapter.opmerkingen) == 1
    assert "van dit ticket" in adapter.opmerkingen[0]["body"]


def test_een_lokaal_gehouden_ticket_wordt_geweigerd_met_uitleg(losdb):
    """Het vinkje is een bewuste keuze; de knop mag die niet stil overrulen."""
    import asyncio

    from fastapi import HTTPException

    t = _los_ticket(losdb, sync_naar_bron=False)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(_los_svc(losdb, _NepAdapter()).sync_ticket(t.id))
    assert fout.value.status_code == 409
    assert "alleen LabX" in str(fout.value.detail)
    assert "Doorzetten naar de bron" in str(fout.value.detail)


def test_een_ticket_zonder_wijzigingen_meldt_dat_er_niets_was(losdb):
    """Het ijkpunt komt uit de dienst zelf: zo staat er precies in wat hij ook
    vergelijkt, in plaats van een zelfverzonnen handvol velden."""
    import asyncio

    from services.boards.sync.base import ExternalItem

    t = _los_ticket(losdb, external_id="10001", external_key="BICC-7")
    svc = _los_svc(losdb, _NepAdapter())
    t.external_snapshot = svc._snapshot(ExternalItem(
        external_id="10001", external_key="BICC-7", title=t.title,
        description=t.description, acceptance_criteria=t.acceptance_criteria,
        state="Nog doen", priority=t.priority, assignee=t.assignee, labels=[]))
    losdb.commit()

    uit = asyncio.run(svc.sync_ticket(t.id))
    assert uit["resultaat"] == "niets"
    assert not svc._adapter(None).bijgewerkt


def test_een_lokaal_bord_heeft_niets_om_naartoe_te_synchroniseren(losdb):
    import asyncio

    from fastapi import HTTPException
    from models.board import Board

    losdb.get(Board, 1).provider = "local"
    losdb.commit()
    t = _los_ticket(losdb)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(_los_svc(losdb, _NepAdapter()).sync_ticket(t.id))
    assert fout.value.status_code == 400
