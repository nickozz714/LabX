"""De opstartreconciliatie moet een herkansing krijgen.

Gemeten geval, 21-10-2026 om 00:50: na een stroomstoring startte de server 25
containers tegelijk op, haalde `docker ps -a` zijn time-out van 15 seconden
niet, en werden allebei de daemons overgeslagen. Reconcile draaide alleen bij
het opstarten, dus daarna keek niemand er nog naar -- een lab stond de hele
nacht op "running" terwijl zijn container al uit was.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import (  # noqa: F401
        audit, azure_profile, background_run, board, claim_resource, cluster,
        cluster_bundel, cluster_env_file, cluster_secret, guard_audit, guard_rule,
        lab, lab_extra, lab_host, lab_secret, lab_worker, mcp_server, message,
        model_runner, notification, plan, schedule, secret, skill, skill_tool,
        thread, time_entry, tool, voice, workflow,
    )

    monkeypatch.setenv("LABX_FERNET_KEY",
                       "dGVzdC1zbGV1dGVsLXZvb3ItZGUtdW5pdC10ZXN0cy0wMDA9")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _verse_vlag():
    import services.lab.lab_service as ls

    ls._RECONCILE_GELUKT = False
    yield
    ls._RECONCILE_GELUKT = False


NU = "2026-10-21T00:50:00+00:00"


def _lab(db, *, container_id="abc123"):
    from models.lab import Lab

    p = Lab(id="lab-1", name="Krimpenerwaard", status="running", image="node:22",
            container_id=container_id, created_at=NU, updated_at=NU)
    db.add(p)
    db.commit()
    return p


class _Daemon:
    """Een daemon die of een lijst geeft, of er het zwijgen toe doet."""

    def __init__(self, rijen=None, stuk=False):
        self.rijen, self.stuk = rijen or [], stuk

    async def list_managed(self):
        if self.stuk:
            raise RuntimeError("docker ps -a time-out na 15s")
        return self.rijen


def test_een_onbereikbare_daemon_laat_de_herkansing_openstaan(db, monkeypatch):
    import services.lab.lab_service as ls

    _lab(db)
    svc = ls.LabService(db)
    monkeypatch.setattr(svc, "runtime", _Daemon(stuk=True), raising=False)

    asyncio.run(svc.reconcile_on_start())

    assert ls.reconcile_nodig() is True, "anders probeert niemand het ooit nog"


def test_de_herkansing_trekt_het_lab_alsnog_recht(db, monkeypatch):
    """De container staat er nog, maar is gestopt. Dat is precies wat een
    stroomstoring achterlaat: exited, niet weg."""
    import services.lab.lab_service as ls

    p = _lab(db)
    svc = ls.LabService(db)

    monkeypatch.setattr(svc, "runtime", _Daemon(stuk=True), raising=False)
    asyncio.run(svc.reconcile_on_start())
    assert p.status == "running", "zolang we niets weten blijft het staan"

    goed = _Daemon([{"name": "labx-lab-1", "id": "abc123", "state": "exited"}])
    monkeypatch.setattr(svc, "runtime", goed, raising=False)
    asyncio.run(svc.reconcile_on_start())

    assert p.status == "stopped"
    assert ls.reconcile_nodig() is False, "één keer gelukt is genoeg"


def test_na_een_geslaagde_ronde_doet_de_herkansing_niets_meer(db, monkeypatch):
    import services.lab.lab_service as ls

    _lab(db, container_id=None)
    svc = ls.LabService(db)
    monkeypatch.setattr(svc, "runtime", _Daemon([]), raising=False)

    asyncio.run(svc.reconcile_on_start())

    assert ls.reconcile_nodig() is False


def test_de_tik_staat_geregistreerd_en_stopt_zichzelf():
    """De plek doet ertoe: een herkansing die nergens vandaan wordt
    aangeroepen is geen herkansing."""
    import inspect

    import server

    bron = inspect.getsource(server)
    assert 'name="reconcile_herkansing"' in bron
    tik = inspect.getsource(server._reconcile_herkansing_tick)
    assert "if not reconcile_nodig():" in tik and "return" in tik


def test_de_lijst_krijgt_ruim_de_tijd():
    """15 seconden was te krap op het enige moment dat het ertoe deed."""
    import inspect

    from services.lab.docker_runtime import DockerRuntime

    bron = inspect.getsource(DockerRuntime.list_managed)
    assert "timeout=60" in bron
