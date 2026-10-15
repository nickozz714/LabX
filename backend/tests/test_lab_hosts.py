"""Computers in je netwerk die labs draaien.

Het beveiligingsmodel staat of valt met één regel: **wat zich meldt, mag
zichzelf niet toelaten.** Een aanmelding komt van iets op je netwerk dat zegt
dat het een lab-host is — dat is geen bewijs. Daarom komt een melder binnen als
`nieuw` en krijgt hij niets tot een mens de code bevestigt die op het scherm
van díé machine staat.

Verder: een lab zonder host blijft op deze server draaien. Elk bestaand lab
heeft `host_id` NULL, en daar mag niets aan veranderen.
"""
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
        audit, azure_profile, background_run, board, claim_resource, guard_audit,
        guard_rule, lab, lab_extra, lab_host, lab_secret, lab_worker, mcp_server,
        message, notification, plan, schedule, secret, skill, skill_tool, thread,
        time_entry, tool, voice, workflow,
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


def _svc(db):
    from services.lab.hosts import LabHostService
    return LabHostService(db)


def _meld(db, **kw):
    return _svc(db).aanmelden(
        machine_id=kw.get("machine_id", "mid-1"),
        naam=kw.get("naam", "Werkpc"),
        agent_url=kw.get("agent_url", "http://192.168.2.40:8098"),
        docker_host=kw.get("docker_host"),
        capaciteit=kw.get("capaciteit") or {"cpus": 8, "geheugen_mb": 16384})


# ── aanmelden geeft niets weg ────────────────────────────────────────────────

def test_wie_zich_meldt_krijgt_nog_niets(db):
    """De kern. Een aanmelding zet een rij op `nieuw` — meer niet."""
    uit = _meld(db)
    assert uit["status"] == "nieuw"
    assert uit["koppelcode"], "er moet een code zijn om over te typen"
    assert uit["wakker_houden"] is False, "niet wakker blijven voor werk dat er niet is"

    host = _svc(db).lijst()[0]
    assert host.docker_host is None or host.status == "nieuw"


def test_dezelfde_machine_komt_niet_twee_keer_in_de_lijst(db):
    """Een laptop op wifi krijgt een ander IP. Herkennen gaat op machine_id,
    niet op adres — anders groeit de lijst elke dag."""
    _meld(db, agent_url="http://192.168.2.40:8098")
    _meld(db, agent_url="http://192.168.2.77:8098")

    hosts = _svc(db).lijst()
    assert len(hosts) == 1
    assert hosts[0].agent_url.endswith(":8098")


def test_de_koppelcode_lekt_niet_meer_na_het_koppelen(db):
    """Zou hij blijven terugkomen, dan is het een wachtwoord dat niemand
    vervangt — en elke aanmelding van buiten kan hem uitlezen."""
    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")

    opnieuw = _meld(db)
    assert opnieuw["status"] == "gekoppeld"
    assert opnieuw["koppelcode"] is None


def test_een_verkeerde_code_koppelt_niet(db):
    svc = _svc(db)
    _meld(db)
    host = svc.lijst()[0]
    with pytest.raises(Exception) as fout:
        svc.koppelen(host.id, code="000000", docker_host="ssh://nick@werkpc")
    assert "koppelcode" in str(fout.value.detail).lower()
    assert svc.get_or_404(host.id).status == "nieuw"


def test_koppelen_zonder_docker_adres_kan_niet(db):
    """Een gekoppelde host zonder adres is een host waar je labs op zet die
    nooit starten."""
    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    with pytest.raises(Exception) as fout:
        svc.koppelen(host.id, code=uit["koppelcode"], docker_host="")
    assert "ssh://" in str(fout.value.detail)


def test_wakker_houden_pas_na_koppelen(db):
    """Een machine die niet is toegelaten, hoeft ook niet wakker te blijven."""
    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    assert _meld(db)["wakker_houden"] is True


# ── labs en hosts ────────────────────────────────────────────────────────────

def _lab(db, **kw):
    from models.lab import Lab

    nu = "2026-10-15T10:00:00+00:00"
    p = Lab(id=kw.get("id", "lab1"), name=kw.get("name", "Fabric"), image="python:3",
            volume_name="v", status="running", cpu_limit=1, mem_limit_mb=512,
            ttl_hours=4, created_at=nu, updated_at=nu, host_id=kw.get("host_id"))
    db.add(p)
    db.commit()
    return p


def test_een_lab_zonder_host_draait_hier(db):
    """Elk bestaand lab heeft host_id NULL en moet exact blijven doen wat het
    deed: de Docker van deze server."""
    from services.lab.docker_runtime import DockerRuntime
    from services.lab.lab_service import LabService

    p = _lab(db)
    rt = LabService(db).runtime_voor(p)
    assert isinstance(rt, DockerRuntime)
    assert rt._docker_host is None, "geen SSH-omweg voor een gewoon lab"


def test_een_lab_op_een_gekoppelde_host_praat_via_ssh(db):
    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")

    p = _lab(db, host_id=host.id)
    rt = LabService(db).runtime_voor(p)
    assert rt._docker_host == "ssh://nick@werkpc"


def test_een_lab_op_een_ongekoppelde_host_start_niet(db):
    """Anders kan alles wat zich op je netwerk als lab-host voordoet, labs
    gaan draaien."""
    from services.lab.lab_service import LabService

    _meld(db)
    host = _svc(db).lijst()[0]
    p = _lab(db, host_id=host.id)

    with pytest.raises(Exception) as fout:
        LabService(db).runtime_voor(p)
    assert "niet gekoppeld" in str(fout.value.detail)


def test_een_host_met_labs_erop_gaat_er_niet_uit(db):
    """Anders wijzen die labs naar een machine die niet meer bestaat."""
    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    _lab(db, host_id=host.id, name="Krimpenerwaard")

    with pytest.raises(Exception) as fout:
        svc.verwijder(host.id)
    assert "Krimpenerwaard" in str(fout.value.detail)


# ── wat alleen door het te draaien bleek ─────────────────────────────────────

def test_daemon_up_kijkt_ook_naar_de_uitvoer():
    """`docker info --format ...` geeft exit-code 0 terwijl het faalt:

        error during connect: ... exec: "ssh": executable file not found
        $ echo $?
        0

    Op alleen de code vertrouwen gaf een groene "verbinding testen" voor een
    machine die er niet is.
    """
    import inspect

    from services.lab.docker_runtime import DockerRuntime

    bron = inspect.getsource(DockerRuntime.daemon_up)
    assert "out" in bron and "strip()" in bron, "de uitvoer moet meetellen"


def test_het_image_heeft_een_ssh_client():
    """Zonder openssh-client in de backend-image mislukt élke lab-host in het
    netwerk, en de melding wijst naar docker in plaats van naar ssh."""
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "openssh-client" in dockerfile


# ── alles wat met een lab gebeurt, gebeurt op ZIJN machine ───────────────────

def test_een_lab_op_een_netwerkmachine_overleeft_een_herstart(db, monkeypatch):
    """Reconcile keek alleen naar de Docker van deze server. Een lab op een
    netwerkmachine stond daar niet in de lijst, en werd dus bij élke herstart
    van LabX op "error" gezet met "Container niet meer gevonden"."""
    import asyncio

    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    p = _lab(db, host_id=host.id)
    p.container_id = "c-op-de-werkpc"
    db.commit()

    class Daemon:
        def __init__(self, rijen):
            self.rijen = rijen

        async def list_managed(self):
            return list(self.rijen)

        async def remove(self, cid, **kw):
            raise AssertionError(f"niets opruimen: {cid}")

    lab_svc = LabService(db, runtime=Daemon([]))          # deze server: leeg
    monkeypatch.setattr(
        "services.lab.lab_service.DockerRuntime",
        lambda **kw: Daemon([{"id": "c-op-de-werkpc", "name": "labx-lab-abc",
                              "state": "running"}]))

    asyncio.run(lab_svc.reconcile_on_start())
    db.refresh(p)
    assert p.status == "running"
    assert not p.error


def test_een_machine_die_uit_staat_meldt_geen_labs_kapot(db, monkeypatch):
    """"Ik kon er niet bij" is iets anders dan "de container is weg". Dat
    verschil niet maken betekent dat een pc die een nacht uit staat al je labs
    erop op error zet."""
    import asyncio

    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    p = _lab(db, host_id=host.id)
    p.container_id = "c-op-de-werkpc"
    db.commit()

    class Leeg:
        async def list_managed(self):
            return []

        async def remove(self, cid, **kw):
            return None

    class Weg:
        async def list_managed(self):
            raise RuntimeError("ssh: connect to host werkpc port 22: Host is down")

    monkeypatch.setattr("services.lab.lab_service.DockerRuntime", lambda **kw: Weg())
    asyncio.run(LabService(db, runtime=Leeg()).reconcile_on_start())
    db.refresh(p)
    assert p.status == "running", "onbereikbaar is geen bewijs dat de container weg is"
