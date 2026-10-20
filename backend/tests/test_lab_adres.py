"""Waar je een branch-lab opent, en waarom dat niet altijd localhost is.

Dit bestaat door een gemeten geval: het cluster verhuisde naar een machine in
het netwerk, de applicatie startte netjes, en de knop "Openen" wees naar
`http://localhost:18002` -- een poort op de verkeerde computer. Er stond
"applicatie gestart" naast een knop die stuk leek.
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


NU = "2026-10-20T12:00:00+00:00"


def _lab(db, *, host_id=None, poort_map=None):
    from models.cluster import Cluster
    from models.lab import Lab

    c = Cluster(naam="Hive", netwerk="labx-cluster-hive", host_id=host_id,
                created_at=NU, updated_at=NU)
    db.add(c)
    db.commit()
    p = Lab(id="lab-1", name="Hive · feature", status="running", cluster_id=c.id,
            image="node:22",
            poort_map=poort_map if poort_map is not None else {"5173": 18002},
            created_at=NU, updated_at=NU)
    db.add(p)
    db.commit()
    return p


def _machine(db, **kw):
    from models.lab_host import LabHost

    velden = {"machine_id": "m1", "naam": "Werkpc", "status": "gekoppeld",
              "agent_url": "http://192.168.2.65:8098", "agent_token": "geheim",
              "created_at": NU, "updated_at": NU}
    velden.update(kw)
    h = LabHost(**velden)
    db.add(h)
    db.commit()
    return h


def test_een_lab_op_deze_machine_opent_gewoon_op_localhost(db):
    from services.lab.lab_adres import adres_voor_lab

    p = _lab(db)

    uit = adres_voor_lab(db, p.id)

    assert uit["url"] == "http://localhost:18002"
    assert uit["via"] == "deze machine"


def test_een_lab_op_een_andere_machine_krijgt_het_adres_van_die_machine(db, monkeypatch):
    """Dit is het geval waarvoor dit bestaat."""
    from services.lab import lab_adres

    h = _machine(db)
    p = _lab(db, host_id=h.id)
    gevraagd = {}

    def nep(agent_url, token, pad, body):
        gevraagd.update({"url": agent_url, "token": token, "pad": pad, "body": body})
        return {"poort": 28002, "lab_poort": body["poort"]}

    monkeypatch.setattr(lab_adres, "_vraag_agent", nep)

    uit = lab_adres.adres_voor_lab(db, p.id)

    assert uit["url"] == "http://192.168.2.65:28002"
    assert uit["via"] == "Werkpc"
    assert gevraagd["body"] == {"poort": 18002}
    assert gevraagd["token"] == "geheim"


def test_zonder_ingang_op_die_machine_zeggen_we_wat_eraan_schort(db):
    """Een agent van voor deze functie heeft geen token. Dan is "het doet het
    niet" het verkeerde antwoord: je moet weten dat die agent bij moet."""
    from fastapi import HTTPException

    from services.lab.lab_adres import adres_voor_lab

    h = _machine(db, agent_token=None)
    p = _lab(db, host_id=h.id)

    with pytest.raises(HTTPException) as fout:
        adres_voor_lab(db, p.id)
    assert fout.value.status_code == 409
    assert "opnieuw" in fout.value.detail


def test_een_lab_zonder_poorten_stuurt_je_niet_het_bos_in(db):
    from fastapi import HTTPException

    from services.lab.lab_adres import adres_voor_lab

    p = _lab(db, poort_map={})

    with pytest.raises(HTTPException) as fout:
        adres_voor_lab(db, p.id)
    assert fout.value.status_code == 400
    assert "start het eerst" in fout.value.detail


def test_een_onbereikbare_agent_wordt_benoemd_met_machine_en_adres(db, monkeypatch):
    import urllib.error

    from fastapi import HTTPException

    from services.lab import lab_adres

    h = _machine(db)
    p = _lab(db, host_id=h.id)

    def stuk(*_a, **_k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(lab_adres, "_vraag_agent", stuk)

    with pytest.raises(HTTPException) as fout:
        lab_adres.adres_voor_lab(db, p.id)
    assert fout.value.status_code == 502
    assert "Werkpc" in fout.value.detail
    assert "192.168.2.65:8098" in fout.value.detail
