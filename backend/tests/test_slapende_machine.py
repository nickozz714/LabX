"""Een lab op een machine die uit is geweest.

Dit is het nare geval: in Docker stáát de container daar gewoon, de
administratie hier klopt, en toch mislukt alles. Zonder controle loopt het
starten door in een SSH-verbinding die niet tot stand komt, valt er ergens
een uitzondering die niemand verwacht, en krijgt het scherm platte tekst
terug in plaats van JSON — waarna de gebruiker een PARSE-FOUT ziet die
nergens over gaat.

Twee kanten, want het ging aan twee kanten mis:

- De backend hoort vooraf te vragen of die machine praat, en anders te zeggen
  wat eraan is.
- De frontend hoort nooit op een niet-JSON antwoord te klappen. Wat de server
  zei is altijd bruikbaarder dan "Unexpected token I in JSON".
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BRON = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(BRON))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import cluster, lab, lab_host  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _opzet(db, *, status="stopped"):
    from models.lab import Lab
    from models.lab_host import LabHost

    nu = "2026-10-20T10:00:00+00:00"
    h = LabHost(naam="imac-van-nick", machine_id="m-1", status="gekoppeld",
                docker_host="ssh://nick@192.168.2.68",
                laatst_gezien="2026-10-15T21:28:17+00:00",
                created_at=nu, updated_at=nu)
    db.add(h)
    db.commit()
    # allow_network uit: anders plant `start` een inrichtingsronde in die een
    # eigen sessie opent tegen een database die deze test niet heeft.
    p = Lab(id="l1", name="Werklab", status=status, image="x", volume_name="v",
            container_id="abc", host_id=h.id, allow_network=False,
            created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    return h, p


class _Daemon:
    def __init__(self, up):
        self._up = up

    async def daemon_up(self):
        if isinstance(self._up, Exception):
            raise self._up
        return self._up


def test_een_slapende_machine_geeft_een_leesbare_melding(db, monkeypatch):
    from fastapi import HTTPException

    from services.lab.lab_service import LabService

    h, p = _opzet(db)
    svc = LabService(db)
    monkeypatch.setattr(svc, "_docker_op_host", lambda hid: _Daemon(False))

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.start(p.id))

    assert fout.value.status_code == 409
    detail = fout.value.detail
    assert "imac-van-nick" in detail
    assert "geen antwoord" in detail
    # De naam van de machine en wanneer we hem voor het laatst zagen: dat is
    # wat je nodig hebt om te weten wélke machine je moet aanzetten.
    assert "2026-10-15" in detail
    # En de geruststelling, want "machine weg" leest als "werk weg".
    assert "niet weg" in detail


def test_een_mislukte_controle_telt_als_onbereikbaar(db, monkeypatch):
    """Niet kunnen vragen is hier hetzelfde als 'nee' — doorgaan levert de
    onbegrijpelijke fout op die we juist wilden vermijden."""
    from fastapi import HTTPException

    from services.lab.lab_service import LabService

    _, p = _opzet(db)
    svc = LabService(db)
    monkeypatch.setattr(svc, "_docker_op_host",
                        lambda hid: _Daemon(RuntimeError("ssh: connect timed out")))

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.start(p.id))
    assert fout.value.status_code == 409


def test_een_machine_die_wel_antwoordt_wordt_niet_tegengehouden(db, monkeypatch):
    """De controle mag geen extra drempel worden voor een machine die het
    gewoon doet."""
    from services.lab.lab_service import LabService

    _, p = _opzet(db)
    svc = LabService(db)
    monkeypatch.setattr(svc, "_docker_op_host", lambda hid: _Daemon(True))

    # Geen uitzondering = door naar het echte starten.
    asyncio.run(svc._eis_bereikbare_machine(p))


def test_een_lab_op_deze_server_wordt_niet_gecontroleerd(db, monkeypatch):
    """Zonder host_id valt er niets te bereiken — en een extra docker-aanroep
    bij elke start van een lokaal lab is zuivere vertraging."""
    from models.lab import Lab
    from services.lab.lab_service import LabService

    nu = "2026-10-20T10:00:00+00:00"
    db.add(Lab(id="lokaal", name="Lokaal", status="stopped", image="x",
               volume_name="v", container_id="abc", created_at=nu, updated_at=nu))
    db.commit()
    svc = LabService(db)

    def nooit(_hid):
        raise AssertionError("er hoort geen host bevraagd te worden")

    monkeypatch.setattr(svc, "_docker_op_host", nooit)
    asyncio.run(svc._eis_bereikbare_machine(db.get(Lab, "lokaal")))


# ── de frontend-kant ─────────────────────────────────────────────────────────

def test_de_frontend_klapt_niet_op_een_niet_json_antwoord() -> None:
    """`JSON.parse` stond kaal in api.ts. Een backend die onderuitgaat
    antwoordt met platte tekst en een proxy met HTML; de gebruiker zag dan een
    parse-fout die niets zegt over wat er misging."""
    api = (BRON.parent.parent / "frontend" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")

    assert "function leesJson(" in api
    # De kale variant mag nergens meer staan.
    assert "text ? JSON.parse(text) : null" not in api
    assert "leesJson(text, res.status, res.statusText)" in api
