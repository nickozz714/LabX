"""Eén waarheid over een machine, uit drie kanalen die het oneens kunnen zijn.

Het geval dat dit bestand verklaart, is echt gebeurd. Een lab wilde niet
starten en LabX zei: "staat die machine aan?" — tegen iemand die ernaast zat
met de machine áán. Gemeten op dat moment:

    laatst_gezien   twee minuten oud
    hostagent       Connection refused
    SSH             Connection reset by peer
    docker          onbereikbaar

Drie kanalen, drie verhalen, en een melding die naar het verkeerde probleem
wees. Dat is erger dan geen melding: je gaat de machine herstarten terwijl
het om SSH ging.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import lab, lab_host  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _host(db, *, gezien_sec=60, agent_url="http://192.168.2.65:8098"):
    from models.lab_host import LabHost

    nu = datetime.now(timezone.utc)
    h = LabHost(naam="MacBook-Pro-van-Nick.local", machine_id="m-65",
                status="gekoppeld", docker_host="ssh://nick@192.168.2.65",
                agent_url=agent_url,
                laatst_gezien=(nu - timedelta(seconds=gezien_sec)).isoformat(),
                created_at=nu.isoformat(), updated_at=nu.isoformat())
    db.add(h)
    db.commit()
    return h


def _nep(monkeypatch, *, agent, ssh, docker_up, docker_hint=""):
    import services.lab.machine_diagnose as mod

    async def nep_agent(_h):
        return agent

    async def nep_ssh(_h):
        return ssh

    class _Rt:
        def __init__(self, **_k):
            pass

        async def diagnose(self):
            return {"daemon_up": docker_up, "hint": docker_hint}

    monkeypatch.setattr(mod, "_agent", nep_agent)
    monkeypatch.setattr(mod, "_ssh", nep_ssh)
    monkeypatch.setattr("services.lab.docker_runtime.DockerRuntime", _Rt)


# ── het geval dat dit veroorzaakte ───────────────────────────────────────────

def test_machine_aan_maar_ssh_dicht_vraagt_niet_of_hij_aanstaat(db, monkeypatch):
    from services.lab.machine_diagnose import diagnose

    h = _host(db, gezien_sec=120)
    _nep(monkeypatch, agent=(False, "Connection refused"),
         ssh=(False, "Connection reset by peer"), docker_up=False)

    uit = asyncio.run(diagnose(db, h))

    assert uit["bruikbaar"] is False
    assert uit["oorzaak"] == "ssh dicht"
    assert "is aan" in uit["melding"]
    assert "Connection reset by peer" in uit["melding"]
    assert "Remote Login" in uit["melding"]
    # En precies NIET de vraag die ons de verkeerde kant op stuurde.
    assert "Staat die machine aan" not in uit["melding"]


def test_een_werkelijk_stille_machine_vraagt_dat_wel(db, monkeypatch):
    from services.lab.machine_diagnose import diagnose

    h = _host(db, gezien_sec=99999)
    _nep(monkeypatch, agent=(False, "timeout"), ssh=(False, "no route to host"),
         docker_up=False)

    uit = asyncio.run(diagnose(db, h))

    assert uit["oorzaak"] == "machine stil"
    assert "Staat die machine aan" in uit["melding"]
    # En de geruststelling, want "machine weg" leest als "werk weg".
    assert "blijft gewoon staan" in uit["melding"]


def test_binnen_maar_geen_docker_wijst_naar_docker(db, monkeypatch):
    """Drie verschillende problemen, drie verschillende antwoorden."""
    from services.lab.machine_diagnose import diagnose

    h = _host(db)
    _nep(monkeypatch, agent=(True, ""), ssh=(True, "ok"), docker_up=False,
         docker_hint="Cannot connect to the Docker daemon")

    uit = asyncio.run(diagnose(db, h))

    assert uit["oorzaak"] == "docker weg"
    assert "Docker Desktop" in uit["melding"]


def test_alles_in_orde_zegt_dat_ook(db, monkeypatch):
    from services.lab.machine_diagnose import diagnose

    h = _host(db)
    _nep(monkeypatch, agent=(True, ""), ssh=(True, "ok"), docker_up=True)

    uit = asyncio.run(diagnose(db, h))
    assert uit["bruikbaar"] is True
    assert uit["oorzaak"] == "in orde"


def test_de_vier_kanalen_staan_los_in_de_uitslag(db, monkeypatch):
    """Zodat een scherm kan tonen WELK kanaal het begeeft, niet alleen dat er
    iets mis is."""
    from services.lab.machine_diagnose import diagnose

    h = _host(db, gezien_sec=30)
    _nep(monkeypatch, agent=(False, "Connection refused"), ssh=(True, "ok"),
         docker_up=False, docker_hint="daemon weg")

    k = asyncio.run(diagnose(db, h))["kanalen"]

    assert k["opgeschreven"]["ok"] is True
    assert k["agent"]["ok"] is False and "refused" in k["agent"]["melding"]
    assert k["ssh"]["ok"] is True
    assert k["docker"]["ok"] is False


# ── de afstemming ────────────────────────────────────────────────────────────

class _Rt:
    def __init__(self, containers):
        self._c = containers

    async def list_managed(self):
        return self._c


def _lab(db, host, lab_id, naam, status, cid):
    from models.lab import Lab

    nu = "2026-10-20T10:00:00+00:00"
    db.add(Lab(id=lab_id, name=naam, status=status, image="x", volume_name="v",
               container_id=cid, host_id=host.id, created_at=nu, updated_at=nu))
    db.commit()


def test_een_verwijderde_container_wordt_benoemd(db, monkeypatch):
    """Precies wat er gebeurde: de container was met de hand weggegooid en
    LabX bleef een status tonen die nergens op sloeg."""
    import services.lab.machine_diagnose as mod
    from services.lab.machine_diagnose import afstemming

    h = _host(db)
    _lab(db, h, "l1", "Beeminds", "expired", "abc123")
    monkeypatch.setattr("services.lab.docker_runtime.DockerRuntime",
                        lambda **k: _Rt([]))

    uit = asyncio.run(afstemming(db, h))

    assert uit["gemeten"] is True
    assert len(uit["weg"]) == 1
    assert uit["weg"][0]["naam"] == "Beeminds"
    assert "bestaat hij niet meer" in uit["weg"][0]["toelichting"]
    assert "1 container(s) weg" in uit["melding"]


def test_een_status_die_niet_klopt_wordt_benoemd(db, monkeypatch):
    from services.lab.machine_diagnose import afstemming

    h = _host(db)
    _lab(db, h, "l1", "Werklab", "running", "abc123")
    monkeypatch.setattr("services.lab.docker_runtime.DockerRuntime",
                        lambda **k: _Rt([{"id": "abc123", "name": "labx-lab-abc",
                                          "state": "exited"}]))

    uit = asyncio.run(afstemming(db, h))

    assert len(uit["ander_label"]) == 1
    assert uit["ander_label"][0]["op_de_machine"] == "exited"
    assert uit["ander_label"][0]["in_labx"] == "running"


def test_een_onbekende_container_wordt_gemeld_maar_niet_opgeruimd(db, monkeypatch):
    """Dat is meestal een lab van een ÁNDERE LabX-installatie. Weggooien zou
    andermans werk vernietigen."""
    from services.lab.machine_diagnose import afstemming

    h = _host(db)
    monkeypatch.setattr("services.lab.docker_runtime.DockerRuntime",
                        lambda **k: _Rt([{"id": "vreemd", "name": "labx-lab-xyz",
                                          "state": "running"}]))

    uit = asyncio.run(afstemming(db, h))

    assert len(uit["onbekend"]) == 1
    assert uit["onbekend"][0]["container_id"] == "vreemd"
    # Geen 'opgeruimd', geen 'verwijderd' — alleen melden.
    assert "onbekend op de machine" in uit["melding"]


def test_een_lab_zonder_container_is_geen_verschil(db, monkeypatch):
    """Dat weet LabX zelf ook; `start` bouwt hem opnieuw op."""
    from services.lab.machine_diagnose import afstemming

    h = _host(db)
    _lab(db, h, "l1", "Leeg", "stopped", None)
    monkeypatch.setattr("services.lab.docker_runtime.DockerRuntime",
                        lambda **k: _Rt([]))

    uit = asyncio.run(afstemming(db, h))

    assert uit["weg"] == []
    assert len(uit["gelijk"]) == 1
    assert "Alles klopt" in uit["melding"]


def test_een_onbereikbare_machine_levert_geen_vals_beeld(db, monkeypatch):
    """Niets kunnen meten is iets anders dan 'er staat niets'. Zonder dit
    zou elke container als 'weg' gemeld worden."""
    from services.lab.machine_diagnose import afstemming

    h = _host(db)
    _lab(db, h, "l1", "Werklab", "running", "abc123")

    class _Stuk:
        async def list_managed(self):
            raise RuntimeError("ssh: connection reset")

    monkeypatch.setattr("services.lab.docker_runtime.DockerRuntime",
                        lambda **k: _Stuk())

    uit = asyncio.run(afstemming(db, h))

    assert uit["gemeten"] is False
    assert uit["weg"] == []
    assert "niet op te vragen" in uit["melding"]
