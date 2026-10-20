"""Draait een vaste dienst, en antwoordt hij ook?

Twee verschillende vragen, en LabX beantwoordde alleen de eerste. Gemeten op
de server: de Cosmos-emulator stond op "draait" terwijl hij nog aan het
opstarten was, en een knop indrukken gaf

    (ServiceUnavailable) pgcosmos extension is still starting

Een waar bericht op de verkeerde plek en in de verkeerde taal -- het ziet
eruit alsof de knop stuk is. Dat is dezelfde fout als een lab dat op
"running" staat zonder dat er iets luistert: de administratie zegt iets
anders dan de werkelijkheid, en de gebruiker krijgt de schuld.

En het ophalen van een image is een eigen stand geworden. Zonder dat hing de
startknop minutenlang zonder een woord: die emulator is 1,7 GB en `docker ps`
gaf al die tijd geen container terwijl er niets mis was.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))



@pytest.fixture(autouse=True)
def _vergeet_wie_geantwoord_heeft():
    """Een dienst die geantwoord heeft, wordt onthouden op container-id.

    In het echt is dat id uniek en wist een herstart het vanzelf; in tests
    heet elke container "c1". Dus schoonvegen, anders lekt het ene antwoord
    naar de volgende test.
    """
    from services.lab import clusters

    clusters._HEEFT_GEANTWOORD.clear()
    yield
    clusters._HEEFT_GEANTWOORD.clear()

@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import cluster, cluster_dienst_actie, lab, lab_host  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _dienst(db, *, status="draait", container="abc", poorten=(8081,)):
    from models.cluster import Cluster, ClusterDienst

    nu = "2026-10-20T14:00:00+00:00"
    c = Cluster(naam="Hive", netwerk="labx-cluster-hive", created_at=nu, updated_at=nu)
    db.add(c)
    db.commit()
    d = ClusterDienst(cluster_id=c.id, naam="ih-cosmos", image="cosmos:preview",
                      poorten=list(poorten), poort_map={}, omgeving={},
                      status=status, container_id=container,
                      created_at=nu, updated_at=nu)
    db.add(d)
    db.commit()
    return c, d


class _Rt:
    def __init__(self, antwoord="open"):
        self.antwoord = antwoord
        self.aanroepen = []

    async def run_wegwerp(self, *, image, cmd, netwerk=None, timeout=0, **_):
        self.aanroepen.append({"netwerk": netwerk, "cmd": cmd})
        if isinstance(self.antwoord, Exception):
            raise self.antwoord
        return {"exit_code": 0, "output": self.antwoord, "truncated": False}


def _svc(db, rt):
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    svc._runtime = lambda _c: rt
    return svc


def test_een_dienst_die_antwoordt_is_klaar(db):
    rt = _Rt("open\nHTTP/1.1 200 OK")
    _, d = _dienst(db)

    uit = asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    assert uit["klaar"] is True
    assert uit["stand"] == "klaar"
    assert "8081" in uit["melding"]


def test_een_open_poort_die_503_antwoordt_is_niet_klaar(db):
    """Gemeten op de server, en de reden dat dit meer doet dan `nc -z`.

    De Cosmos-gateway accepteerde netjes TCP terwijl zijn Postgres omviel,
    en gaf op elk verzoek 503. Een controle die daar "klaar" op zegt, liegt
    precies in het geval waarvoor hij bestaat.
    """
    rt = _Rt("open\n  HTTP/1.1 503 Service Unavailable")
    _, d = _dienst(db)

    uit = asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    assert uit["klaar"] is False
    assert uit["stand"] == "start_op"
    assert "503" in uit["melding"]


def test_een_dienst_die_geen_http_spreekt_telt_gewoon_als_klaar(db):
    """Niet alles is een webserver. Een database die de poort opent en
    zwijgt op een GET is bereikbaar, en dat is hier het antwoord."""
    rt = _Rt("open\nwget: can\'t connect to remote host")
    _, d = _dienst(db)

    uit = asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    assert uit["klaar"] is True


def test_een_andere_foutcode_dan_503_betekent_dat_er_iemand_thuis_is(db):
    """404 of 401 is een antwoord. Het is niet aan deze controle om te
    vinden dat het een vervelend antwoord is."""
    rt = _Rt("open\nHTTP/1.1 401 Unauthorized")
    _, d = _dienst(db)

    assert asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))["klaar"] is True


def test_een_dienst_die_nog_opstart_zegt_dat(db):
    """Dit is het geval dat de stacktrace veroorzaakte."""
    rt = _Rt("dicht")
    _, d = _dienst(db)

    uit = asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    assert uit["klaar"] is False
    assert uit["stand"] == "start_op"
    assert "luistert nog niet" in uit["melding"]
    assert "wachten is hier het goede antwoord" in uit["melding"]


def test_de_controle_gaat_over_het_clusternetwerk(db):
    """Op de naam waarop een branch-lab hem ook aanspreekt. De gepubliceerde
    poort op de machine zegt daar niets over."""
    rt = _Rt("open")
    c, d = _dienst(db)

    asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    aanroep = rt.aanroepen[0]
    assert aanroep["netwerk"] == c.netwerk
    assert "ih-cosmos 8081" in aanroep["cmd"][-1]


def test_een_image_dat_nog_opgehaald_wordt_heeft_een_eigen_melding(db):
    rt = _Rt("open")
    _, d = _dienst(db, status="ophalen", container=None)

    uit = asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    assert uit["stand"] == "ophalen"
    assert uit["klaar"] is False
    assert "één keer per machine" in uit["melding"]
    assert not rt.aanroepen          # er valt nog niets te bevragen


def test_een_gestopte_dienst_wordt_niet_bevraagd(db):
    rt = _Rt("open")
    _, d = _dienst(db, status="gestopt", container=None)

    uit = asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    assert uit["klaar"] is False
    assert not rt.aanroepen


def test_een_dienst_zonder_poort_telt_als_klaar(db):
    """Geen poort om op te kloppen: dan is 'de container draait' het beste
    antwoord dat er is, en dat zeggen we dan ook zo."""
    rt = _Rt("open")
    _, d = _dienst(db, poorten=())

    uit = asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    assert uit["klaar"] is True
    assert "geen poort" in uit["melding"].lower()
    assert not rt.aanroepen


def test_niet_kunnen_meten_is_geen_oordeel(db):
    """Anders houdt een kapotte meting je werk tegen."""
    rt = _Rt(RuntimeError("daemon weg"))
    _, d = _dienst(db)

    uit = asyncio.run(_svc(db, rt).dienst_gezondheid(d.id))

    assert uit["stand"] == "onbekend"
    assert uit["klaar"] is False


# ── en wat de knoppen ermee doen ─────────────────────────────────────────────

def test_een_knop_wacht_op_een_dienst_die_nog_opstart(db, monkeypatch):
    from fastapi import HTTPException

    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    a = svc.zet(d.id, label="Tellen", script="echo x", plek="naast")

    async def nog_niet(_self, _id):
        return {"stand": "start_op", "klaar": False,
                "melding": "ih-cosmos draait, maar luistert nog niet op poort 8081."}

    monkeypatch.setattr("services.lab.clusters.ClusterService.dienst_gezondheid", nog_niet)

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.draai(a.id))
    assert fout.value.status_code == 409
    assert "luistert nog niet" in fout.value.detail


def test_een_knop_draait_gewoon_als_de_dienst_klaar_is(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    a = svc.zet(d.id, label="Tellen", script="echo x", plek="naast")

    async def klaar(_self, _id):
        return {"stand": "klaar", "klaar": True, "melding": "ok"}

    monkeypatch.setattr("services.lab.clusters.ClusterService.dienst_gezondheid", klaar)
    monkeypatch.setattr(svc, "_runtime", lambda _c: _KlaarRt())

    uit = asyncio.run(svc.draai(a.id))
    assert uit["exit_code"] == 0


class _KlaarRt:
    async def run_wegwerp(self, **_k):
        return {"exit_code": 0, "output": "13603", "truncated": False}

    async def exec(self, *a, **k):
        return {"exit_code": 0, "output": "13603", "truncated": False}


def test_een_onmeetbare_dienst_houdt_je_knop_niet_tegen(db, monkeypatch):
    from services.lab.dienst_acties import DienstActieService

    _, d = _dienst(db)
    svc = DienstActieService(db)
    a = svc.zet(d.id, label="Tellen", script="echo x", plek="naast")

    async def onbekend(_self, _id):
        return {"stand": "onbekend", "klaar": False, "melding": "niet te meten"}

    monkeypatch.setattr("services.lab.clusters.ClusterService.dienst_gezondheid", onbekend)
    monkeypatch.setattr(svc, "_runtime", lambda _c: _KlaarRt())

    uit = asyncio.run(svc.draai(a.id))
    assert uit["exit_code"] == 0
