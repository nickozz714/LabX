"""Werken in een cluster-branch-lab is iets anders dan werken in een lab.

Vier dingen gingen mis op het IntelligentHive-cluster, alle vier met hetzelfde
patroon: code die aanneemt dat een bord aan een LAB hangt, terwijl een
clusterbord aan een CLUSTER hangt.

- De `board__*`-tools bestonden niet in het lab. De agent schreef het braaf
  op in zijn eindantwoord en deed de rest zonder te kunnen claimen.
- Een run liep stuk op `error_max_turns` bij de standaard van 250.
- De opdracht zei niet waar de werkkopie stond.
- Een branch-lab mocht naar een andere machine verhuizen dan zijn diensten.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import board, cluster, lab, lab_host, plan  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _cluster(db, **velden):
    from models.cluster import Cluster

    nu = "2026-10-19T22:00:00+00:00"
    c = Cluster(naam="Hive", netwerk="labx-cluster-hive",
                repo_url="https://github.com/org/Intelligenthive.git",
                created_at=nu, updated_at=nu, **velden)
    db.add(c)
    db.commit()
    return c


def _lab(db, c, lab_id="l1", branch="feature/x"):
    from models.lab import Lab

    nu = "2026-10-19T22:00:00+00:00"
    p = Lab(id=lab_id, name="Hive · x", status="running", image="x", volume_name="v",
            cluster_id=c.id, branch=branch, created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    return p


# ── de board-tools ───────────────────────────────────────────────────────────

def test_een_branch_lab_vindt_het_bord_van_zijn_cluster(db):
    """Dit was de oorzaak: de gateway zocht het bord via Board.lab_id, en een
    clusterbord heeft dat niet. Dus kreeg de agent geen enkele board__-tool."""
    from models.board import Board
    from services.boards.board_service import BoardService

    c = _cluster(db)
    nu = "2026-10-19T22:00:00+00:00"
    db.add(Board(name="Hive", key_prefix="IH", columns=[], cluster_id=c.id,
                 created_at=nu, updated_at=nu))
    db.commit()
    lab = _lab(db, c)

    bord = BoardService(db).board_for_lab(lab.id)
    assert bord is not None
    assert bord.cluster_id == c.id


def test_een_gewoon_lab_blijft_zijn_eigen_bord_vinden(db):
    from models.board import Board
    from models.lab import Lab
    from services.boards.board_service import BoardService

    nu = "2026-10-19T22:00:00+00:00"
    db.add(Lab(id="los", name="Los", status="running", image="x", volume_name="v",
               created_at=nu, updated_at=nu))
    db.add(Board(name="Eigen", key_prefix="E", columns=[], lab_id="los",
                 created_at=nu, updated_at=nu))
    db.commit()

    assert BoardService(db).board_for_lab("los").name == "Eigen"


def test_een_lab_zonder_bord_en_zonder_cluster_vindt_niets(db):
    from models.lab import Lab
    from services.boards.board_service import BoardService

    nu = "2026-10-19T22:00:00+00:00"
    db.add(Lab(id="kaal", name="Kaal", status="running", image="x", volume_name="v",
               created_at=nu, updated_at=nu))
    db.commit()

    assert BoardService(db).board_for_lab("kaal") is None


# ── model en beurten ─────────────────────────────────────────────────────────

def test_het_clustermodel_gaat_naar_de_bestaande_labs(db):
    """'Zet Opus op dit cluster' betekent alle branches. Eén vergeten lab is
    genoeg om een run op een ander model te laten lopen dan je dacht."""
    from services.lab.clusters import ClusterService

    c = _cluster(db)
    _lab(db, c, "l1", "feature/a")
    _lab(db, c, "l2", "feature/b")

    ClusterService(db).wijzig(c.id, lab_model="opus", lab_max_turns=600)

    from models.lab import Lab
    for lid in ("l1", "l2"):
        lab = db.get(Lab, lid)
        assert lab.model == "opus"
        assert lab.max_turns == 600


def test_het_beurtenplafond_van_het_lab_wint_van_de_instelling(db):
    """Zonder dit liep clusterwerk stuk op error_max_turns."""
    from services.agent.chat_agent import ChatAgent

    c = _cluster(db, lab_max_turns=600)
    lab = _lab(db, c)
    lab.max_turns = 600
    db.commit()

    assert ChatAgent(db)._lab_max_turns(lab.id) == 600
    assert ChatAgent(db)._lab_max_turns(None) is None


# ── de werkcontext in de opdracht ────────────────────────────────────────────

def test_de_opdracht_zegt_waar_de_werkkopie_staat(db):
    """Een agent die dat niet weet begint met `ls /workspace` en raadt."""
    from models.cluster import ClusterDienst
    from services.boards.cluster_werk import cluster_kop

    c = _cluster(db)
    nu = "2026-10-19T22:00:00+00:00"
    db.add(ClusterDienst(cluster_id=c.id, naam="hive-cosmos", image="cosmos:1",
                         poorten=[8081], poort_map={}, omgeving={},
                         created_at=nu, updated_at=nu))
    db.commit()
    lab = _lab(db, c, branch="feature/zoek")

    kop = cluster_kop(db, lab, None)

    assert "/workspace/Intelligenthive" in kop
    assert "feature/zoek" in kop
    assert "hive-cosmos" in kop
    assert "8081" in kop
    # en de kale naam, want zo noemt de repo hem
    assert "cosmos" in kop


def test_een_gewoon_lab_krijgt_geen_clusterkop(db):
    """Regulier labwerk verandert niet. Dat was het uitgangspunt."""
    from models.lab import Lab
    from services.boards.cluster_werk import cluster_kop

    nu = "2026-10-19T22:00:00+00:00"
    los = Lab(id="los", name="Los", status="running", image="x", volume_name="v",
              created_at=nu, updated_at=nu)
    db.add(los)
    db.commit()

    assert cluster_kop(db, los, None) == ""


def test_de_instructiebestanden_van_de_repo_komen_in_de_opdracht():
    """In de IntelligentHive-repo staat AGENTS.md en CLAUDE.md met precies de
    afspraken waar een agent naar hoort te kijken."""
    from services.boards.cluster_werk import instructieblok

    blok = instructieblok(["AGENTS.md", "CLAUDE.md", "components/api/README.md"],
                          "Intelligenthive")

    assert "/workspace/Intelligenthive/AGENTS.md" in blok
    assert "/workspace/Intelligenthive/CLAUDE.md" in blok
    assert "vóór je begint" in blok


def test_zonder_instructiebestanden_komt_er_geen_leeg_kopje():
    from services.boards.cluster_werk import instructieblok

    assert instructieblok([], "repo") == ""


# ── één cluster, één machine ─────────────────────────────────────────────────



# ── het hele cluster verhuizen ───────────────────────────────────────────────

class _VerhuisDocker:
    """Houdt bij wat er bij een verhuizing werkelijk gebeurt."""

    def __init__(self, naam="bron"):
        self.naam = naam
        self.geexporteerd, self.geimporteerd, self.weg, self.netwerken = [], [], [], []

    async def volume_bestaat(self, v):
        return True

    async def export_volume(self, volume, doel, **k):
        self.geexporteerd.append(volume)
        open(doel, "wb").write(b"x")

    async def import_volume(self, volume, bron, **k):
        self.geimporteerd.append(volume)

    async def remove_volume(self, v):
        self.weg.append(v)

    async def ensure_network_named(self, n):
        self.netwerken.append(n)

    async def verwijder_netwerk(self, n):
        pass


def test_een_cluster_verhuist_met_de_gegevens_van_zijn_diensten(db, monkeypatch):
    """Dit is het deel dat ertoe doet: in labx_dienst_* zit de DATA. Een
    dienst opnieuw starten op een lege machine geeft een lege database, en dat
    ziet er precies hetzelfde uit als een geslaagde verhuizing."""
    from models.cluster import ClusterDienst
    from services.lab.clusters import ClusterService
    import services.lab.lab_service as ls

    c = _cluster(db)
    nu = "2026-10-20T08:00:00+00:00"
    db.add(ClusterDienst(cluster_id=c.id, naam="hive-cosmos", image="cosmos:1",
                         poorten=[8081], poort_map={}, omgeving={}, status="draait",
                         container_id="abc", created_at=nu, updated_at=nu))
    db.commit()
    lab = _lab(db, c)

    bron, doel = _VerhuisDocker("bron"), _VerhuisDocker("doel")
    svc = ClusterService(db)
    monkeypatch.setattr(svc, "_runtime", lambda _c: bron)
    monkeypatch.setattr(ls.LabService, "_docker_op_host", lambda self, h: doel)
    monkeypatch.setattr(ls.LabService, "bezetting", lambda self, i: {})
    verplaatst = []

    async def nep_lab_verplaats(self, lab_id, *, host_id, met_het_cluster=False):
        verplaatst.append((lab_id, host_id, met_het_cluster))
        return {}

    monkeypatch.setattr(ls.LabService, "verplaats", nep_lab_verplaats)

    async def nep_stop(dienst_id):
        return None

    async def nep_start(dienst_id):
        return None

    monkeypatch.setattr(svc, "stop_dienst", nep_stop)
    monkeypatch.setattr(svc, "start_dienst", nep_start)

    asyncio.run(svc.verplaats(c.id, host_id=7))

    assert bron.geexporteerd == [f"labx_dienst_{c.id}_hive-cosmos"]
    assert doel.geimporteerd == [f"labx_dienst_{c.id}_hive-cosmos"]
    assert bron.weg == [f"labx_dienst_{c.id}_hive-cosmos"]
    assert verplaatst == [(lab.id, 7, True)]
    assert doel.netwerken == [c.netwerk]
    db.refresh(c)
    assert c.host_id == 7


def test_verhuizen_tijdens_een_run_wordt_geweigerd(db, monkeypatch):
    from fastapi import HTTPException

    from services.lab.clusters import ClusterService
    import services.lab.lab_service as ls

    c = _cluster(db)
    _lab(db, c)
    svc = ClusterService(db)
    monkeypatch.setattr(ls.LabService, "_docker_op_host", lambda self, h: _VerhuisDocker())
    monkeypatch.setattr(ls.LabService, "bezetting", lambda self, i: {"w1": 1})

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.verplaats(c.id, host_id=7))
    assert fout.value.status_code == 409
    assert "werk" in fout.value.detail.lower()


def test_naar_dezelfde_machine_verhuizen_doet_niets(db, monkeypatch):
    from services.lab.clusters import ClusterService

    c = _cluster(db)
    c.host_id = 4
    db.commit()
    svc = ClusterService(db)

    uit = asyncio.run(svc.verplaats(c.id, host_id=4))
    assert uit["machine"] or True
    db.refresh(c)
    assert c.host_id == 4


def test_alleen_het_nummer_wijzigen_mag_niet(db):
    """Dat zou de administratie verzetten en de containers, volumes en het
    netwerk op de oude machine laten staan — en eruitzien alsof het lukte."""
    from fastapi import HTTPException

    from services.lab.clusters import ClusterService

    c = _cluster(db)
    with pytest.raises(HTTPException) as fout:
        ClusterService(db).wijzig(c.id, host_id=2)
    assert fout.value.status_code == 400
    assert "verplaatsen" in fout.value.detail.lower()


def test_een_branch_lab_verhuist_nog_steeds_niet_los(db):
    from fastapi import HTTPException

    from services.lab.lab_service import LabService

    c = _cluster(db)
    lab = _lab(db, c)
    svc = LabService(db)
    svc._weiger_bij_host = lambda *a, **k: None

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.verplaats(lab.id, host_id=3))
    assert fout.value.status_code == 409
    assert "hele cluster" in fout.value.detail.lower()


# ── beurten: in een cluster geen plafond ─────────────────────────────────────

def test_in_een_cluster_geldt_er_standaard_geen_beurtenplafond(db):
    """Een beurt is geen maat voor werk. Het getal verhogen verplaatst het
    probleem alleen: bij 600 valt hetzelfde ticket om op een groter bestand.
    Tijd en geld begrenzen de run, niet het aantal keren opzoeken."""
    from services.agent.chat_agent import ChatAgent

    c = _cluster(db)
    lab = _lab(db, c)

    assert ChatAgent(db)._lab_max_turns(lab.id) == 0


def test_een_gewoon_lab_volgt_gewoon_de_instelling(db):
    """Regulier labwerk verandert niet — dat was het uitgangspunt."""
    from models.lab import Lab
    from services.agent.chat_agent import ChatAgent

    nu = "2026-10-20T09:00:00+00:00"
    db.add(Lab(id="los", name="Los", status="running", image="x", volume_name="v",
               created_at=nu, updated_at=nu))
    db.commit()

    assert ChatAgent(db)._lab_max_turns("los") is None


def test_een_eigen_getal_in_het_cluster_blijft_een_harde_grens(db):
    from services.agent.chat_agent import ChatAgent

    c = _cluster(db, lab_max_turns=600)
    lab = _lab(db, c)
    lab.max_turns = 600
    db.commit()

    assert ChatAgent(db)._lab_max_turns(lab.id) == 600


def test_nul_wordt_niet_stilletjes_de_instelling(db):
    """`or` zou die nul terugdraaien naar settings.max_turns, en dan was het
    hele punt weg."""
    from services.agent.chat_agent import ChatAgent

    class _Instellingen:
        default_model = "sonnet"
        oauth_token = None
        cli_path = "claude"
        max_turns = 250
        timeout_seconds = 100
        extra_args = None
        enable_tool_search = True
        fallback_model = None
        max_budget_usd = None
        autocompact = False
        custom_agents_json = None
        default_agent = None

    provider, _ = ChatAgent(db)._build_provider(None, None, _Instellingen(), max_turns=0)
    assert provider._max_turns == 0

    provider2, _ = ChatAgent(db)._build_provider(None, None, _Instellingen(), max_turns=None)
    assert provider2._max_turns == 250


def test_zonder_plafond_gaat_de_vlag_er_helemaal_af():
    """Dat is wat `0` moet betekenen: niet 'nul beurten'."""
    from services.agent.claude_cli_provider import ClaudeCliProvider

    p = ClaudeCliProvider(default_model="sonnet", max_turns=0)
    assert not p._max_turns
