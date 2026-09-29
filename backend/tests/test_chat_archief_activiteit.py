"""Een chat waarin je praat, hoort niet in het archief te staan.

Waargenomen: een gesprek dat bij een board-ticket hoorde was op 25-09
gearchiveerd, en er werd tot en met 29-09 dagelijks in doorgepraat — maar het
bleef gearchiveerd en dus buiten de lijst. Vijf gesprekken van hetzelfde lab
hadden het.

De oorzaak was klein: een bericht werkte `updated_at` bij maar liet
`archived_at` staan. Eenmaal gearchiveerd, altijd gearchiveerd.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.lab import Lab  # noqa: E402
from models.thread import Thread  # noqa: E402
from services.chat import archief  # noqa: E402


def _iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture()
def db():
    from db.database import Base

    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[Lab.__table__, Thread.__table__])
    s = sessionmaker(bind=motor)()
    yield s
    s.close()


def _thread(db, **velden):
    basis = dict(id="t1", lab_id="lab-1", title="KRI-101", source="board",
                 created_at=_iso(days=10), updated_at=_iso(days=10))
    basis.update(velden)
    t = Thread(**basis)
    db.add(t)
    db.commit()
    return t


def test_aanraken_haalt_een_chat_uit_het_archief(db):
    t = _thread(db, archived_at=_iso(days=4))
    archief.raak_aan(t)
    db.commit()
    assert t.archived_at is None
    assert t.updated_at > _iso(minutes=1)


def test_aanraken_van_niets_valt_niet_om(db):
    archief.raak_aan(None)      # de thread kan weg zijn


def test_een_chat_die_na_het_archiveren_gebruikt_is_komt_terug(db):
    """Zelfherstellend: wie hier al last van had hoeft niets te doen."""
    t = _thread(db, archived_at=_iso(days=4), updated_at=_iso(hours=1))
    assert archief.haal_ten_onrechte_gearchiveerde_terug(db) == 1
    db.refresh(t)
    assert t.archived_at is None


def test_een_echt_stille_chat_blijft_in_het_archief(db):
    t = _thread(db, archived_at=_iso(days=1), updated_at=_iso(days=5))
    assert archief.haal_ten_onrechte_gearchiveerde_terug(db) == 0
    db.refresh(t)
    assert t.archived_at is not None


def test_een_teruggehaalde_chat_wordt_niet_meteen_opnieuw_gearchiveerd(db):
    """Dat is de reden dat het terughalen vóór het opruimen gebeurt."""
    _thread(db, archived_at=_iso(days=4), updated_at=_iso(hours=1))
    archief.haal_ten_onrechte_gearchiveerde_terug(db)
    assert archief.archiveer_stille_chats(db, dagen=3) == 0


def test_de_verzendroutes_raken_de_thread_via_dezelfde_plek_aan():
    """Los van elkaar gaan die twee uit elkaar lopen — dat is precies hoe een
    actief gesprek dagenlang in het archief kon blijven staan."""
    import inspect

    from routers import chat_router
    from services.agent import background_runs

    bron = inspect.getsource(chat_router)
    assert bron.count("archief.raak_aan(") >= 2
    assert "archief.raak_aan(" in inspect.getsource(background_runs)
