"""Een vaste poort op je eigen machine naar een branch-lab.

LabX deelt oplopende machinepoorten uit vanaf 18000 zodat branches naast
elkaar kunnen draaien. Applicaties met een OAuth-redirect eisen juist een
vast nummer: `http://localhost:5173` staat bij de identity provider
geregistreerd en nergens anders. Bij IntelligentHive gaf dat AADSTS50011
zodra je het lab op 18004 opende -- niet op te lossen in de repo, want het
zit in een registratie waar het lab niet bij kan.

Wat hier vastligt en stil kan sneuvelen:

- De tunnel wijst naar de MACHINEpoort, niet naar de containerpoort. Dat
  verschil is het hele punt en is van buiten niet te zien.
- Op een gekoppelde lab-host wijst hij naar DIE machine. Wijst hij daar naar
  `host.docker.internal`, dan tunnel je naar een poort op de verkeerde
  computer en krijg je of niets of, erger, iets anders.
- Een tunnel op een poort die al bezet is door een oudere tunnel moet die
  eerst opruimen: anders zie je stilletjes de verkeerde branch.
"""
from __future__ import annotations

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
        cluster_env_file, cluster_secret, guard_audit, guard_rule, lab, lab_extra, lab_host,
        lab_secret, lab_worker, mcp_server, message, model_runner, notification,
        plan, schedule, secret, skill, skill_tool, thread, time_entry, tool,
        voice, workflow,
    )

    monkeypatch.setenv("LABX_FERNET_KEY", "x")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _lab(db, *, host_id=None, poorten=None, branch="feature/inloggen"):
    from models.cluster import Cluster
    from models.lab import Lab

    nu = "2026-10-18T10:00:00+00:00"
    c = Cluster(naam="Hive", host_id=host_id, netwerk="labx-cluster-hive",
                created_at=nu, updated_at=nu)
    db.add(c)
    db.flush()
    p = Lab(id="lab1", name="Hive · inloggen", image="node:22", volume_name="v",
            status="running", cpu_limit=2, mem_limit_mb=4096, ttl_hours=24,
            cluster_id=c.id, branch=branch,
            poort_map=(poorten if poorten is not None else {"5173": 18004, "7071": 18005}),
            created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    return p


def test_de_tunnel_wijst_naar_de_machinepoort_niet_de_containerpoort(db):
    """Het hele punt: binnen heet hij 5173, buiten 18004."""
    from services.lab.tunnel import Tunnel

    p = _lab(db)
    uit = Tunnel(db).script(p.id, os_naam="mac")

    assert "tcp:host.docker.internal:18004" in uit["inhoud"]
    assert uit["url"] == "http://localhost:5173"
    assert uit["machine_poort"] == 18004
    assert "tcp-listen:5173" in uit["inhoud"]


def test_je_kunt_de_tweede_poort_kiezen(db):
    """Een cluster met een front-end en een API heeft er twee, en soms wil je
    de API op een vast nummer."""
    from services.lab.tunnel import Tunnel

    p = _lab(db)
    uit = Tunnel(db).script(p.id, os_naam="mac", container_poort=7071)

    assert "tcp:host.docker.internal:18005" in uit["inhoud"]
    assert uit["url"] == "http://localhost:7071"


def test_een_eigen_lokale_poort_mag(db):
    from services.lab.tunnel import Tunnel

    p = _lab(db)
    uit = Tunnel(db).script(p.id, os_naam="mac", lokaal=3000)

    assert uit["url"] == "http://localhost:3000"
    assert "tcp-listen:3000" in uit["inhoud"]
    assert "127.0.0.1:3000:3000" in uit["inhoud"]


def test_op_linux_wordt_de_hostnaam_aangemaakt(db):
    """`host.docker.internal` bestaat op Linux niet vanzelf. Zonder deze
    regel faalt het script daar met een naam die niet te vinden is."""
    from services.lab.tunnel import Tunnel

    p = _lab(db)
    linux = Tunnel(db).script(p.id, os_naam="linux")["inhoud"]
    mac = Tunnel(db).script(p.id, os_naam="mac")["inhoud"]

    assert "--add-host=host.docker.internal:host-gateway" in linux
    assert "--add-host" not in mac


def test_windows_krijgt_een_cmd_met_windows_regeleindes(db):
    """Een .cmd met alleen \\n draait wel, maar wordt in Kladblok één lange
    regel -- en dan is niet te zien wat je start."""
    from services.lab.tunnel import Tunnel

    p = _lab(db)
    uit = Tunnel(db).script(p.id, os_naam="windows")

    assert uit["bestandsnaam"].endswith(".cmd")
    assert "\r\n" in uit["inhoud"]
    assert "@echo off" in uit["inhoud"]
    assert "host.docker.internal:18004" in uit["inhoud"]


def test_een_oudere_tunnel_op_dezelfde_poort_wordt_opgeruimd(db):
    """Anders wijst poort 5173 nog naar de vorige branch en zie je stilletjes
    de verkeerde code."""
    from services.lab.tunnel import Tunnel

    p = _lab(db)
    for os_naam in ("mac", "linux", "windows"):
        inhoud = Tunnel(db).script(p.id, os_naam=os_naam)["inhoud"]
        assert "docker rm -f" in inhoud, os_naam


def test_een_lab_op_een_andere_machine_tunnelt_naar_die_machine(db):
    """host.docker.internal is JOUW machine. Staat het lab ergens anders, dan
    tunnel je daarmee naar een poort op de verkeerde computer."""
    from models.lab_host import LabHost
    from services.lab.tunnel import Tunnel

    nu = "2026-10-18T10:00:00+00:00"
    h = LabHost(naam="iMac", machine_id="m1", status="gekoppeld",
                agent_url="https://192.168.2.22:9443/", created_at=nu, updated_at=nu)
    db.add(h)
    db.flush()
    p = _lab(db, host_id=h.id)

    inhoud = Tunnel(db).script(p.id, os_naam="mac")["inhoud"]

    assert "tcp:192.168.2.22:18004" in inhoud
    assert "host.docker.internal" not in inhoud


def test_zonder_gepubliceerde_poorten_valt_er_niets_te_tunnelen(db):
    from fastapi import HTTPException

    from services.lab.tunnel import Tunnel

    p = _lab(db, poorten={})
    with pytest.raises(HTTPException) as fout:
        Tunnel(db).script(p.id, os_naam="mac")
    assert fout.value.status_code == 400


def test_een_onbekend_besturingssysteem_wordt_benoemd(db):
    from fastapi import HTTPException

    from services.lab.tunnel import Tunnel

    p = _lab(db)
    with pytest.raises(HTTPException) as fout:
        Tunnel(db).script(p.id, os_naam="bsd")
    assert fout.value.status_code == 400
    assert "bsd" in fout.value.detail


def test_de_bestandsnaam_overleeft_een_branch_met_schuine_strepen(db):
    """`feature/inloggen` als bestandsnaam levert een map op die niet bestaat."""
    from services.lab.tunnel import Tunnel

    p = _lab(db, branch="feature/inloggen")
    naam = Tunnel(db).script(p.id, os_naam="mac")["bestandsnaam"]

    assert "/" not in naam
    assert naam == "labx-tunnel-feature-inloggen-5173.sh"
