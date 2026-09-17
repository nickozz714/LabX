"""Chats die stil zijn geworden gaan opzij — en blijven bestaan.

De nadruk ligt op het verschil met verwijderen: dit mag alleen vanzelf gebeuren
omdat er niets verdwijnt. Wat hier getoetst wordt is dan ook vooral wat er NIET
gebeurt: geen bericht weg, geen thread weg, en niets dat je vandaag nog gebruikt
hebt.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _iso(dagen: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dagen)).isoformat()


@pytest.fixture()
def db():
    from db.database import Base
    import models.lab  # noqa: F401
    import models.message  # noqa: F401
    import models.thread  # noqa: F401
    from models.thread import Thread

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        models.lab.Lab.__table__, models.thread.Thread.__table__,
        models.message.Message.__table__,
    ])
    sessie = sessionmaker(bind=engine)()
    sessie.add(models.lab.Lab(id="lab1", name="Lab", status="stopped",
                              image="python:3-bookworm",
                              created_at=_iso(30), updated_at=_iso(30)))

    def chat(tid: str, dagen: float, *, source: str = "chat", gearchiveerd=None):
        sessie.add(Thread(id=tid, title=tid, lab_id="lab1", source=source,
                          archived_at=gearchiveerd,
                          created_at=_iso(30), updated_at=_iso(dagen)))

    chat("vers", 0.2)
    chat("gisteren", 1)
    chat("net-op-tijd", 2.9)
    chat("oud", 5)
    chat("stokoud", 40)
    chat("al-weg", 9, gearchiveerd=_iso(1))
    sessie.commit()
    try:
        yield sessie
    finally:
        sessie.close()


def test_alleen_wat_dagen_stilligt_gaat_opzij(db):
    from models.thread import Thread
    from services.chat.archief import archiveer_stille_chats

    assert archiveer_stille_chats(db, dagen=3) == 2

    def weg(tid):
        return db.get(Thread, tid).archived_at is not None

    assert weg("oud") and weg("stokoud")
    assert not weg("vers")
    assert not weg("gisteren")
    # 2,9 dagen is nog geen 3. De grens hoort de grens te zijn: iemand die op
    # dag drie terugkomt bij een gesprek, hoort het te vinden waar hij het liet.
    assert not weg("net-op-tijd")


def test_niets_verdwijnt(db):
    """Archiveren raakt de inhoud niet — dat is de hele reden dat het vanzelf mag."""
    from models.thread import Thread
    from services.chat.archief import archiveer_stille_chats

    voor = db.query(Thread).count()
    archiveer_stille_chats(db, dagen=3)
    assert db.query(Thread).count() == voor
    t = db.get(Thread, "stokoud")
    assert t is not None and t.title == "stokoud" and t.lab_id == "lab1"


def test_updated_at_blijft_staan(db):
    """De klok van 'wanneer gebeurde er voor het laatst echt iets' mag niet
    verspringen door het opruimen zelf — anders is niet meer te zien hoe oud een
    gesprek is."""
    from models.thread import Thread
    from services.chat.archief import archiveer_stille_chats

    voor = db.get(Thread, "oud").updated_at
    archiveer_stille_chats(db, dagen=3)
    assert db.get(Thread, "oud").updated_at == voor


def test_uitgezet_doet_niets(db):
    from services.chat.archief import archiveer_stille_chats

    assert archiveer_stille_chats(db, dagen=0) == 0


def test_tweede_ronde_pakt_niets_dubbel(db):
    from services.chat.archief import archiveer_stille_chats

    assert archiveer_stille_chats(db, dagen=3) == 2
    assert archiveer_stille_chats(db, dagen=3) == 0
