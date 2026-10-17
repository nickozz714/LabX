"""Borden per cluster, tickets per branch.

De ontwerpkeuze die hier vastligt: er is GEEN bord per branch-lab. Een
branch-lab is wegwerpbaar — dat is het hele punt ervan — en een bord dat aan
zo'n lab hangt verdwijnt mee zodra je het opruimt. Dan ben je precies het werk
kwijt dat je wilde bewaren.

Dus: één bord per cluster, en elk ticket wijst naar een BRANCH. Het "labbord"
is dat bord gefilterd op één branch. De branch en niet het lab-id, om dezelfde
reden: het lab komt en gaat, de branch blijft.
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
        cluster_secret, guard_audit, guard_rule, lab, lab_extra, lab_host,
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


def _opzet(db):
    from models.lab import Lab
    from services.boards.board_service import BoardService
    from services.lab.clusters import ClusterService

    c = ClusterService(db).maak(naam="Hive")
    nu = "2026-10-17T10:00:00+00:00"
    db.add(Lab(id="l1", name="Hive · feature/zoek", image="node:22", volume_name="v",
               status="running", cpu_limit=2, mem_limit_mb=4096, ttl_hours=4,
               cluster_id=c.id, branch="feature/zoek", created_at=nu, updated_at=nu))
    db.commit()
    svc = BoardService(db)
    b = svc.create_board({"name": "Hive", "cluster_id": c.id})
    return svc, c, b


def test_een_bord_hoort_bij_een_cluster(db):
    svc, c, b = _opzet(db)
    uit = svc.board_to_dict(b)
    assert uit["cluster_id"] == c.id
    assert uit["cluster_naam"] == "Hive"


def test_het_bord_noemt_de_branches_die_nu_een_lab_hebben(db):
    """Dat is wat een "labbord" tot een filter maakt in plaats van een apart
    bord."""
    svc, c, b = _opzet(db)
    takken = svc.board_to_dict(b)["branches"]
    assert takken == [{"branch": "feature/zoek", "lab_id": "l1", "status": "running"}]


def test_een_ticket_wijst_naar_een_branch(db):
    svc, c, b = _opzet(db)
    t = svc.create_ticket(b.id, {"title": "Filter bouwen", "branch": "feature/zoek"})
    assert svc.ticket_to_dict(t)["branch"] == "feature/zoek"


def test_een_ticket_overleeft_het_opruimen_van_zijn_lab(db):
    """Hier zit de hele reden achter de keuze. Een bord per branch-lab zou
    met dat lab verdwijnen; een ticket dat naar een BRANCH wijst niet."""
    from models.lab import Lab

    svc, c, b = _opzet(db)
    t = svc.create_ticket(b.id, {"title": "Filter bouwen", "branch": "feature/zoek"})

    db.delete(db.get(Lab, "l1"))
    db.commit()

    opnieuw = svc.ticket_to_dict(svc.get_ticket(t.id))
    assert opnieuw["branch"] == "feature/zoek", "de branch blijft"
    assert svc.board_to_dict(b)["branches"] == [], "het lab is weg, en dat zie je"


def test_een_gewoon_bord_verandert_niet(db):
    from services.boards.board_service import BoardService

    svc = BoardService(db)
    b = svc.create_board({"name": "Los bord"})
    uit = svc.board_to_dict(b)
    assert uit["cluster_id"] is None
    assert "branches" not in uit, "geen cluster, geen branchlijst"
