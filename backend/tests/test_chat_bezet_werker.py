"""Een lopende chat bezet een werker — net als een ticket.

"Nu staat er bij werkers 0/1 bij De Vries. Maar eigenlijk moet dat 1/1 zijn."
Klopte: een chatbeurt kiest geen werker, dus `lab_worker_id` bleef leeg, en de
telling sloeg zulke runs over. Twee gevolgen: het overzicht zei 0/1 terwijl je
zelf zat te chatten, en de planner zag werker 1 als vrij en zette er een ticket
bovenop — in dezelfde container, met dezelfde bestanden en dezelfde az-sessie.

Een run zonder werker draait per definitie in werker 1 (zie `container_for`),
dus zo hoort hij ook geteld te worden.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture()
def db():
    from db.database import Base
    import models.background_run  # noqa: F401
    import models.lab  # noqa: F401
    import models.lab_worker  # noqa: F401
    import models.message  # noqa: F401
    import models.thread  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        models.lab.Lab.__table__, models.lab_worker.LabWorker.__table__,
        models.thread.Thread.__table__, models.message.Message.__table__,
        models.background_run.BackgroundRun.__table__,
    ])
    s = sessionmaker(bind=engine)()
    s.add(models.lab.Lab(id="lab1", name="De Vries", status="running",
                         image="python:3-bookworm", worker_count=2, max_workers=2,
                         created_at=_nu(), updated_at=_nu()))
    for i in (1, 2):
        s.add(models.lab_worker.LabWorker(id=i, lab_id="lab1", index=i,
                                          container_id=f"c{i}", status="running",
                                          # Een EXTRA werker doet pas mee als zijn
                                          # inrichting geslaagd is — zie claimbare_werkers.
                                          provision_status="ok",
                                          created_at=_nu(), updated_at=_nu()))
    s.add(models.thread.Thread(id="t1", title="Chat", lab_id="lab1",
                               created_at=_nu(), updated_at=_nu()))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _run(db, *, status="running", worker=None, tid="t1"):
    from models.background_run import BackgroundRun
    r = BackgroundRun(id=f"r{db.query(BackgroundRun).count() + 1}", thread_id=tid,
                      prompt="p", status=status, mode="foreground",
                      lab_worker_id=worker, steps=[], created_at=_nu())
    db.add(r)
    db.commit()
    return r


def test_chat_zonder_werker_bezet_werker_1(db):
    from services.lab.lab_service import LabService

    svc = LabService(db)
    assert svc.bezette_werkers("lab1") == set()
    _run(db)                       # een gewone chatbeurt: geen werker gekozen
    assert svc.bezette_werkers("lab1") == {1}


def test_afgelopen_chat_bezet_niets(db):
    from services.lab.lab_service import LabService

    _run(db, status="completed")
    assert LabService(db).bezette_werkers("lab1") == set()


def test_chat_en_ticket_samen(db):
    from services.lab.lab_service import LabService

    _run(db)                       # chat -> werker 1
    _run(db, worker=2)             # ticket -> werker 2
    assert LabService(db).bezette_werkers("lab1") == {1, 2}


def test_vrije_werker_slaat_de_chat_over(db):
    """Het punt van de hele exercitie: een ticket mag niet in de container
    landen waar op dat moment een chat in zit."""
    from models.lab import Lab
    from services.lab.lab_service import LabService

    svc = LabService(db)
    _run(db)                       # chat zit in werker 1
    vrij = svc.vrije_werker(db.get(Lab, "lab1"))
    assert vrij is not None and vrij.index == 2


def test_alles_bezet_levert_geen_werker_op(db):
    from models.lab import Lab
    from services.lab.lab_service import LabService

    svc = LabService(db)
    _run(db)
    _run(db, worker=2)
    assert svc.vrije_werker(db.get(Lab, "lab1")) is None


def test_chat_van_een_ander_lab_telt_niet_mee(db):
    from models.lab import Lab
    from models.thread import Thread
    from services.lab.lab_service import LabService

    db.add(Lab(id="lab2", name="Ander", status="running", image="x",
               created_at=_nu(), updated_at=_nu()))
    db.add(Thread(id="t2", title="Chat", lab_id="lab2", created_at=_nu(), updated_at=_nu()))
    db.commit()
    _run(db, tid="t2")
    assert LabService(db).bezette_werkers("lab1") == set()


# ── en wat er gebeurt als alles bezet is ────────────────────────────────────
#
# Dan is er een keuze te maken die niet voor elk lab hetzelfde uitpakt: gaat de
# chatbeurt door in een bezette werker (jij drukte op verzenden, je krijgt
# antwoord) of wordt hij geweigerd (geen twee runs in dezelfde bestanden)?
# `chat_deelt_werker` op het lab beslist dat; standaard aan, want zo werkte het
# altijd en het weigeren van een mens is de ingrijpendere van de twee.
#
# Met asyncio.run en niet met pytest-asyncio: één coroutine aanroepen is het
# niet waard om er een testplugin bij te halen.

def _vol(db, monkeypatch, *, deelt: bool):
    """Lab met alles bezet en een plafond dat niet meegeeft."""
    from models.lab import Lab

    lab = db.get(Lab, "lab1")
    lab.max_workers = 1
    lab.chat_deelt_werker = deelt
    db.commit()
    _run(db)                     # werker 1 bezet
    _run(db, worker=2)           # werker 2 ook

    async def _niets(self, lab_id):
        return None
    monkeypatch.setattr("services.lab.lab_service.LabService.ensure_extra_worker", _niets)
    return lab


def test_deelt_standaard_een_bezette_werker(db, monkeypatch):
    import asyncio

    from routers import chat_router

    lab = _vol(db, monkeypatch, deelt=True)
    # None = "geen eigen werker": de beurt landt in werker 1, naast het werk.
    assert asyncio.run(chat_router._werker_voor_chat(db, lab)) is None


def test_uitgezet_weigert_in_plaats_van_delen(db, monkeypatch):
    import asyncio

    from fastapi import HTTPException
    from routers import chat_router

    lab = _vol(db, monkeypatch, deelt=False)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(chat_router._werker_voor_chat(db, lab))
    assert fout.value.status_code == 409
    # De melding moet zeggen wat je eraan kunt doen, niet alleen dat het niet kan.
    assert "plafond" in str(fout.value.detail)


def test_vrije_werker_gaat_altijd_voor(db):
    """Ook met delen aan: is er een werker vrij, dan gaat de beurt daarheen."""
    import asyncio

    from models.lab import Lab
    from routers import chat_router

    _run(db)                     # alleen werker 1 bezet
    assert asyncio.run(chat_router._werker_voor_chat(db, db.get(Lab, "lab1"))) == 2
