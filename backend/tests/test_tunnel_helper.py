"""Eén helper op je machine, voor alle labs.

Het tunnelscript was per lab: downloaden, uitvoerbaar maken, starten. Drie
keer wrijving voor iets wat je dagelijks doet -- en het bestand wees naar een
doorgang die bij een herstart van de agent verdwijnt.
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
        model_runner, notification, plan, schedule, secret, setting, skill,
        skill_tool, thread, time_entry, tool, voice, workflow,
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


NU = "2026-10-21T20:00:00+00:00"


def _lab(db, naam="Hive · feature", poorten=None, status="running"):
    from models.cluster import Cluster
    from models.lab import Lab

    c = db.query(Cluster).first()
    if c is None:
        c = Cluster(naam="Hive", netwerk="labx-cluster-hive",
                    created_at=NU, updated_at=NU)
        db.add(c)
        db.commit()
    p = Lab(id=f"lab-{naam[-1]}", name=naam, status=status, image="node:22",
            cluster_id=c.id, branch="feature/x",
            poort_map=poorten if poorten is not None else {"5173": 18002},
            created_at=NU, updated_at=NU)
    db.add(p)
    db.commit()
    return p


def test_de_sleutel_komt_er_pas_als_je_de_helper_ophaalt(db):
    """Een sleutel die bestaat zonder dat iemand hem gebruikt, is een sleutel
    die je vergeet in te trekken."""
    from services.lab import tunnel_helper

    assert tunnel_helper.sleutel(db) == ""
    gemaakt = tunnel_helper.sleutel(db, maak_aan=True)
    assert len(gemaakt) > 20
    assert tunnel_helper.sleutel(db) == gemaakt, "en daarna blijft hij gelijk"


def test_een_verkeerde_sleutel_komt_er_niet_in(db):
    from fastapi import HTTPException

    from services.lab import tunnel_helper

    tunnel_helper.sleutel(db, maak_aan=True)
    with pytest.raises(HTTPException) as fout:
        tunnel_helper.controleer(db, "iets-anders")
    assert fout.value.status_code == 401


def test_zonder_sleutel_in_de_database_mag_niemand_erin(db):
    """Anders zou een lege installatie met een lege sleutel openstaan."""
    from fastapi import HTTPException

    from services.lab import tunnel_helper

    with pytest.raises(HTTPException):
        tunnel_helper.controleer(db, "")


def test_de_lijst_geeft_alleen_wat_de_helper_nodig_heeft(db):
    """Geen paden, geen geheimen, geen instellingen: hij moet je een keuze
    kunnen laten maken en verder niets."""
    from services.lab import tunnel_helper

    _lab(db)

    rij = tunnel_helper.labs(db)[0]

    assert set(rij) == {"id", "naam", "branch", "cluster", "draait", "poorten"}
    assert rij["poorten"] == [5173]


def test_draaiende_labs_staan_bovenaan(db):
    """Je kiest bijna altijd iets dat aan staat; dat hoort niet onderaan."""
    from services.lab import tunnel_helper

    _lab(db, naam="Hive · a", status="stopped")
    _lab(db, naam="Hive · b", status="running")

    assert [r["naam"] for r in tunnel_helper.labs(db)] == ["Hive · b", "Hive · a"]


def test_er_staat_geen_enkel_poortnummer_in_de_helper():
    """Dat is het hele punt: een nummer dat je opschrijft klopt morgen niet
    meer. Hij vraagt het bij elke keuze opnieuw op."""
    from services.lab.tunnel_helper_script import script

    inhoud = script(labx_url="https://192.168.2.15:8443", sleutel="geheim",
                    image="alpine/socat:latest")

    assert "28002" not in inhoud and "18002" not in inhoud
    assert "/api/tunnel/adres" in inhoud, "hij vraagt het adres op"
    assert "/api/tunnel/labs" in inhoud, "en de lijst labs"


def test_de_helper_heeft_jouw_server_en_sleutel_erin():
    from services.lab.tunnel_helper_script import script

    inhoud = script(labx_url="https://192.168.2.15:8443/", sleutel="geheim",
                    image="alpine/socat:latest")

    assert 'LABX="https://192.168.2.15:8443"' in inhoud, "zonder slash op het eind"
    assert 'SLEUTEL="geheim"' in inhoud


def test_de_helper_ruimt_op_als_je_hem_sluit():
    """Een doorgeefluik dat blijft staan, wijst op een dag naar een lab dat
    er niet meer is."""
    from services.lab.tunnel_helper_script import script

    inhoud = script(labx_url="https://x", sleutel="g", image="i")

    assert "trap afsluiten INT TERM" in inhoud
    assert "docker rm -f" in inhoud
