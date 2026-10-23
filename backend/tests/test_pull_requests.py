"""Van branch-lab naar pull request.

Het ticket wordt gevonden via de branch: een bord hangt aan een cluster en
een ticket draagt de branch waaraan gewerkt wordt. Geen extra veld, geen
tweede administratie die uit de pas kan lopen.
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


NU = "2026-10-23T12:00:00+00:00"


def _opzet(db, *, branch="feature/x", met_ticket=True):
    from models.board import Board, Ticket
    from models.cluster import Cluster
    from models.lab import Lab

    c = Cluster(naam="Hive", netwerk="n", repo_url="https://github.com/org/repo.git",
                created_at=NU, updated_at=NU)
    db.add(c); db.commit()
    p = Lab(id="lab-1", name="Hive · x", status="running", image="node:22",
            cluster_id=c.id, branch=branch, created_at=NU, updated_at=NU)
    db.add(p)
    if met_ticket:
        b = Board(name="Hive", key_prefix="IH", cluster_id=c.id, seq=1,
                  columns=[{"key": "todo", "name": "Te doen"}],
                  created_at=NU, updated_at=NU)
        db.add(b); db.commit()
        db.add(Ticket(board_id=b.id, key="IH-1", title="Meerdere waarden kiezen",
                      description="De gebruiker moet er meer dan één kunnen kiezen.",
                      acceptance_criteria="1. Je kunt er twee kiezen.",
                      status="todo", branch=branch, position=1,
                      created_at=NU, updated_at=NU))
    db.commit()
    return p, c


# ── het ticket vinden ────────────────────────────────────────────────────────

def test_het_ticket_wordt_via_de_branch_gevonden(db):
    from services.lab.pull_requests import PullRequests

    p, _ = _opzet(db)
    assert PullRequests(db).ticket_van(p).key == "IH-1"


def test_zonder_ticket_is_het_geen_fout(db):
    """Je mag een PR maken voor werk dat niet op een bord staat."""
    from services.lab.pull_requests import PullRequests

    p, _ = _opzet(db, met_ticket=False)
    assert PullRequests(db).ticket_van(p) is None


def test_een_ticket_van_een_ander_cluster_telt_niet(db):
    """Twee clusters kunnen dezelfde branchnaam hebben; dan hoort het ticket
    van de buurman hier niet te verschijnen."""
    from models.board import Board, Ticket
    from models.cluster import Cluster
    from services.lab.pull_requests import PullRequests

    p, _ = _opzet(db, met_ticket=False)
    ander = Cluster(naam="Ander", netwerk="n2", created_at=NU, updated_at=NU)
    db.add(ander); db.commit()
    b = Board(name="Ander", key_prefix="AN", cluster_id=ander.id, seq=1,
              columns=[{"key": "todo", "name": "Te doen"}], created_at=NU, updated_at=NU)
    db.add(b); db.commit()
    db.add(Ticket(board_id=b.id, key="AN-1", title="Iets anders", status="todo",
                  branch="feature/x", position=1, created_at=NU, updated_at=NU))
    db.commit()

    assert PullRequests(db).ticket_van(p) is None


# ── de teksten ───────────────────────────────────────────────────────────────

def test_de_titel_komt_van_het_ticket(db):
    """Een reviewer wil eerst weten WAAROM dit er is, en dat staat in het
    ticket -- niet in een commitbericht."""
    from services.lab.pull_requests import _titel

    p, _ = _opzet(db)
    from services.lab.pull_requests import PullRequests
    ticket = PullRequests(db).ticket_van(p)

    assert _titel(ticket, "feature/x", []) == "IH-1: Meerdere waarden kiezen"


def test_zonder_ticket_wint_de_enige_commit(db):
    from services.lab.pull_requests import _titel

    commits = [{"sha": "abc1234", "titel": "Meerdere waarden toegestaan"}]
    assert _titel(None, "feature/x", commits) == "Meerdere waarden toegestaan"


def test_bij_meerdere_commits_is_de_branchnaam_eerlijker(db):
    """Eén van de vijf pakken is een willekeurige greep."""
    from services.lab.pull_requests import _titel

    commits = [{"sha": "a", "titel": "Eerst"}, {"sha": "b", "titel": "Toen"}]
    assert _titel(None, "feature/multi-select", commits) == "multi select"


def test_de_beschrijving_bevat_ticket_en_commits(db):
    from services.lab.pull_requests import PullRequests, _beschrijving

    p, _ = _opzet(db)
    ticket = PullRequests(db).ticket_van(p)
    tekst = _beschrijving(ticket, [{"sha": "abc1234", "titel": "Eerste stap"}])

    assert "IH-1" in tekst
    assert "meer dan één kunnen kiezen" in tekst
    assert "Acceptatiecriteria" in tekst
    assert "`abc1234` Eerste stap" in tekst


def test_zonder_iets_blijft_de_beschrijving_leeg(db):
    """Liever een lege PR dan een PR met een verzonnen samenvatting erin."""
    from services.lab.pull_requests import _beschrijving

    assert _beschrijving(None, []) == ""


# ── de randen ────────────────────────────────────────────────────────────────

def test_zonder_github_repo_een_duidelijke_weigering(db):
    from fastapi import HTTPException

    from models.cluster import Cluster
    from services.lab.pull_requests import PullRequests

    p, c = _opzet(db)
    db.get(Cluster, c.id).repo_url = "https://dev.azure.com/org/project/_git/repo"
    db.commit()

    with pytest.raises(HTTPException) as fout:
        PullRequests(db).voorstel(p.id)
    assert "GitHub" in fout.value.detail


def test_zonder_token_wordt_de_deploy_key_benoemd(db):
    """Een deploy key kan wel pushen en geen PR maken. Dat verschil moet in
    de foutmelding staan, anders zoek je het op de verkeerde plek."""
    from fastapi import HTTPException

    from services.lab.pull_requests import PullRequests

    p, _ = _opzet(db)
    with pytest.raises(HTTPException) as fout:
        PullRequests(db).voorstel(p.id)
    assert "deploy key" in fout.value.detail.lower()


# ── de rechtencontrole ───────────────────────────────────────────────────────

class _NepGitHub:
    """Een GitHub die antwoordt zoals de test voorschrijft."""

    def __init__(self, antwoorden):
        self.repo = "org/repo"
        self._antwoorden = antwoorden

    def _vraag(self, pad, **_kw):
        from fastapi import HTTPException

        for stuk, uitkomst in self._antwoorden.items():
            if stuk in pad:
                if isinstance(uitkomst, int):
                    raise HTTPException(status_code=uitkomst, detail=f"GitHub gaf {uitkomst}")
                return uitkomst
        raise AssertionError(f"onverwacht pad: {pad}")

    rechten = None  # wordt hieronder van de echte klasse geleend


def _rechten_van(antwoorden):
    from services.lab.github import GitHub

    nep = _NepGitHub(antwoorden)
    return GitHub.rechten(nep)


def test_alles_in_orde_zegt_dat_schrijven_pas_blijkt():
    """Of een token pull requests mag MAKEN is van buitenaf niet te zien, en
    uitproberen zou een echte pull request aanmaken."""
    uit = _rechten_van({"/pulls": [], "/repos/org/repo": {"permissions": {"push": True}}})

    assert uit["ziet_repo"] and uit["mag_pushen"] and uit["pr_lezen"]
    assert "blijkt pas" in uit["advies"]


def test_geen_pull_requests_wijst_naar_de_permissie():
    """Dit is het gemeten geval: pushen lukt, /pulls geeft 403."""
    uit = _rechten_van({"/pulls": 403, "/repos/org/repo": {"permissions": {"push": True}}})

    assert uit["mag_pushen"] is True
    assert uit["pr_lezen"] is False
    assert "Pull requests: Read and write" in uit["advies"]
    assert "goedkeuring" in uit["advies"], "en dat het aan goedkeuring kan liggen"


def test_de_repo_niet_zien_is_een_ander_probleem():
    """Dan heeft sleutelen aan de permissies geen zin: de repository staat
    niet in Repository access, of het token wacht nog op goedkeuring."""
    uit = _rechten_van({"/repos/org/repo": 404})

    assert uit["ziet_repo"] is False
    assert "Repository access" in uit["advies"]
    assert uit["pr_lezen"] is False


def test_de_controle_verandert_niets():
    """Een knop die 'controleren' heet, hoort niets aan te maken."""
    import inspect

    from services.lab.github import GitHub

    bron = inspect.getsource(GitHub.rechten)
    assert "POST" not in bron and "maak_pr" not in bron
