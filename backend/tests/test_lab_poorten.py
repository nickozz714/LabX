"""Bij een poort in een lab komen -- hier of op een machine in je netwerk.

Dit is het stuk dat stilletjes fout kan gaan. Een lab op een netwerkmachine
START prima; pas als je zijn browser opent of een logintunnel nodig hebt,
blijkt dat de backend er helemaal niet bij kan. De containernaam resolvet hier
niet (die bridge zit op die pc) en de gepubliceerde poort staat op de loopback
VAN DIE MACHINE -- met opzet, want een labpoort hoort niet op je LAN.

De weg erheen is de SSH die we toch al hebben. Deze tests leggen vast dat het
goede pad gekozen wordt, en -- belangrijker -- dat het mis gaat met een
melding die zegt wat je eraan kunt doen.
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


def _host(db, docker_host="ssh://nick@werkpc"):
    from services.lab.hosts import LabHostService

    svc = LabHostService(db)
    uit = svc.aanmelden(machine_id="mid-1", naam="Werkpc",
                        agent_url="http://192.168.2.40:8098", capaciteit={})
    rij = svc.lijst()[0]
    svc.koppelen(rij.id, code=uit["koppelcode"], docker_host=docker_host)
    return rij


def _lab(db, **kw):
    from models.lab import Lab

    nu = "2026-10-15T10:00:00+00:00"
    p = Lab(id=kw.get("id", "lab1"), name="Fabric", image="python:3",
            volume_name="v", status="running", container_id=kw.get("container_id", "c1"),
            network_alias="labx-lab-abc", cpu_limit=1, mem_limit_mb=512, ttl_hours=4,
            ports=kw.get("ports", []), host_id=kw.get("host_id"), kind=kw.get("kind"),
            host_project_dir=kw.get("host_project_dir"),
            created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    return p


def _svc(db, port_map=None):
    from services.lab.lab_service import LabService

    class Nep:
        async def port_map(self, cid):
            return dict(port_map or {})

    svc = LabService(db, runtime=Nep())
    svc._docker_op_host = lambda host_id: Nep()    # noqa: ARG005
    return svc


# ── wat er gepubliceerd wordt ────────────────────────────────────────────────

def test_een_lab_hier_publiceert_alleen_wat_je_zelf_koos(db):
    """De labpoort niet publiceren is de reden dat de enige weg naar binnen
    langs de login van LabX loopt. Daar mag niets stiekem bijkomen."""
    p = _lab(db, ports=[8400])
    assert _svc(db)._te_publiceren(p) == [8400]


def test_een_lab_op_een_netwerkmachine_publiceert_ook_de_browserpoort(db):
    """Anders is er niets om een tunnel heen te leggen, en blijft het
    VNC-scherm leeg zonder dat iets zegt waarom."""
    from services.lab.lab_service import BROWSER_POORT

    host = _host(db)
    p = _lab(db, host_id=host.id, ports=[8400])
    assert _svc(db)._te_publiceren(p) == [8400, BROWSER_POORT]


def test_de_browserpoort_komt_er_niet_dubbel_in(db):
    from services.lab.lab_service import BROWSER_POORT

    host = _host(db)
    p = _lab(db, host_id=host.id, ports=[BROWSER_POORT])
    assert _svc(db)._te_publiceren(p) == [BROWSER_POORT]


# ── waar de backend naartoe praat ────────────────────────────────────────────

def test_een_lab_hier_wordt_op_zijn_containernaam_bereikt(db):
    """Geen tunnel, geen gepubliceerde poort: gewoon de gedeelde bridge."""
    p = _lab(db)
    adres = asyncio.run(_svc(db).poort_adres(p, 6080))
    assert adres == "labx-lab-abc:6080"


def test_een_lab_op_een_netwerkmachine_loopt_via_een_tunnel(db, monkeypatch):
    host = _host(db)
    p = _lab(db, host_id=host.id)

    gevraagd = {}

    async def _nep(docker_host, verre_poort):
        gevraagd["docker_host"] = docker_host
        gevraagd["verre_poort"] = verre_poort
        return 54321

    monkeypatch.setattr("services.lab.poort_tunnel.tunnels.lokale_poort", _nep)
    adres = asyncio.run(_svc(db, port_map={6080: 49154}).poort_adres(p, 6080))

    assert adres == "127.0.0.1:54321"
    # Naar de GEPUBLICEERDE poort op die machine, niet naar 6080 zelf: op de
    # loopback van die pc staat 6080 helemaal niet.
    assert gevraagd == {"docker_host": "ssh://nick@werkpc", "verre_poort": 49154}


def test_een_niet_gepubliceerde_poort_zegt_wat_je_eraan_kunt_doen(db):
    """Dit is de val: het lab draait, de poort luistert in de container, en
    toch kun je er niet bij. Een kale "verbinding geweigerd" stuurt je dan de
    verkeerde kant op."""
    from fastapi import HTTPException

    host = _host(db)
    p = _lab(db, host_id=host.id)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(_svc(db, port_map={}).poort_adres(p, 8400))
    assert fout.value.status_code == 409
    assert "niet gepubliceerd" in str(fout.value.detail)
    assert "Werkpc" in str(fout.value.detail)


def test_een_hostlab_praat_gewoon_met_de_machine_zelf(db):
    """Geen container, geen bridge: wat daar luistert, luistert op de pc."""
    p = _lab(db, kind="host", host_project_dir="/Users/nick/project")
    assert asyncio.run(_svc(db).poort_adres(p, 8400)) == "127.0.0.1:8400"


def test_een_ontkoppelde_machine_geeft_geen_halve_verbinding(db):
    from fastapi import HTTPException
    from services.lab.hosts import LabHostService

    host = _host(db)
    p = _lab(db, host_id=host.id)
    LabHostService(db).weigeren(host.id)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(_svc(db).poort_adres(p, 6080))
    assert "niet meer gekoppeld" in str(fout.value.detail)


# ── de tunnel voor JOUW laptop ───────────────────────────────────────────────

def test_de_tunnel_van_een_netwerklab_gaat_naar_die_machine_en_niet_naar_de_server(db):
    """De server kan bij dat labnetwerk net zomin als jij. Een script dat naar
    de server tunnelt zou netjes verbinden en nergens uitkomen."""
    host = _host(db)
    p = _lab(db, host_id=host.id, ports=[8400])
    info = asyncio.run(_svc(db, port_map={8400: 49200}).tunnel_info(p.id, port=8400))

    assert info["machine"] == "Werkpc"
    assert info["ssh_voorstel"] == "nick@werkpc"
    assert (info["doel_host"], info["doel_poort"]) == ("127.0.0.1", 49200)


def test_de_tunnel_van_een_gewoon_lab_blijft_naar_de_container_wijzen(db):
    p = _lab(db)

    from services.lab.lab_service import LabService

    class Nep:
        async def container_ip(self, cid):
            return "172.20.0.7"

    svc = LabService(db, runtime=Nep())
    info = asyncio.run(svc.tunnel_info(p.id, port=8400))
    assert (info["doel_host"], info["doel_poort"]) == ("172.20.0.7", 8400)
    assert info["machine"] is None


def test_het_script_wijst_naar_de_gepubliceerde_poort(db):
    """`-L 8400:127.0.0.1:8400` zou op die machine bij niets uitkomen."""
    from services.lab.lab_service import LabService

    script = LabService.tunnel_script(lab_name="Fabric", ssh_target="nick@werkpc",
                                      ip="127.0.0.1", port=8400, doel_poort=49200,
                                      machine="Werkpc")
    assert "-L 8400:127.0.0.1:49200" in script
    assert "Werkpc" in script


# ── het ssh-adres ontleden ───────────────────────────────────────────────────

@pytest.mark.parametrize("adres,verwacht", [
    ("ssh://nick@werkpc", "nick@werkpc"),
    ("ssh://nick@werkpc.local:2222", "nick@werkpc.local"),
    ("ssh://werkpc", "werkpc"),
    ("unix:///var/run/docker.sock", None),
    ("", None),
])
def test_ssh_doel_ontleden(adres, verwacht):
    from services.lab.poort_tunnel import ssh_doel

    assert ssh_doel(adres) == verwacht


def test_een_lokale_daemon_krijgt_geen_tunnel():
    """Zou dat wel gebeuren, dan probeerde de backend naar zichzelf te sshen
    om bij een container te komen die hij gewoon kan aanspreken."""
    from services.lab.poort_tunnel import PoortTunnels

    with pytest.raises(RuntimeError) as fout:
        asyncio.run(PoortTunnels().lokale_poort("unix:///var/run/docker.sock", 6080))
    assert "geen ssh-adres" in str(fout.value)
