"""Development clusters: vaste diensten die blijven, branch-labs die gaan.

Wat hier vastligt is vooral het ONDERSCHEID. Een cluster is geen slim lab en
een vaste dienst is geen lab: de Cosmos-emulator moet blijven draaien terwijl
je drie branch-labs weggooit en vier nieuwe maakt. Precies dat verschil is de
reden dat clusters apart bestaan, en precies dat is wat stil kan verdwijnen
als iemand later "opruimen" uitbreidt.
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
        audit, azure_profile, background_run, board, claim_resource, cluster,
        guard_audit, guard_rule, lab, lab_extra, lab_host, lab_secret, lab_worker,
        mcp_server, message, model_runner, notification, plan, schedule, secret,
        skill, skill_tool, thread, time_entry, tool, voice, workflow,
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


class NepDocker:
    def __init__(self):
        self.netwerken, self.gestart, self.verwijderd = [], [], []

    async def ensure_network_named(self, naam):
        self.netwerken.append(naam)

    async def run_dienst_container(self, *, naam, image, netwerk, volume,
                                   poorten=None, omgeving=None, **kw):
        self.gestart.append({"naam": naam, "image": image, "netwerk": netwerk,
                             "volume": volume, "poorten": dict(poorten or {}),
                             "omgeving": omgeving or {}})
        return f"container-{naam}"

    async def remove(self, cid, **kw):
        self.verwijderd.append(cid)


def _svc(db, docker=None):
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    if docker is not None:
        svc._runtime = lambda c: docker          # noqa: ARG005
    return svc


# ── het cluster zelf ─────────────────────────────────────────────────────────

def test_een_cluster_krijgt_een_eigen_netwerk(db):
    """Een eigen netwerk en niet het gedeelde labnetwerk: de vaste diensten
    van dit cluster horen niet zichtbaar te zijn voor elk ander lab."""
    c = _svc(db).maak(naam="IntelligentHive")
    assert c.netwerk == "labx-cluster-intelligenthive"


def test_een_naam_met_rare_tekens_wordt_een_bruikbare_netwerknaam(db):
    c = _svc(db).maak(naam="Hive / Dev 2026!")
    assert c.netwerk.startswith("labx-cluster-")
    assert " " not in c.netwerk and "/" not in c.netwerk


def test_twee_clusters_met_dezelfde_naam_kan_niet(db):
    from fastapi import HTTPException

    _svc(db).maak(naam="Hive")
    with pytest.raises(HTTPException) as fout:
        _svc(db).maak(naam="Hive")
    assert fout.value.status_code == 409


# ── vaste diensten ───────────────────────────────────────────────────────────

def test_een_dienst_draait_op_het_clusternetwerk_en_op_zijn_eigen_naam(db):
    """Binnen het cluster praat alles op containernaam. Zou de dienst niet op
    het clusternetwerk staan, dan moest elk branch-lab een adres kennen."""
    d = NepDocker()
    svc = _svc(db, d)
    c = svc.maak(naam="Hive")
    dienst = svc.voeg_dienst_toe(c.id, naam="ih-cosmos",
                                 image="mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview",
                                 poorten=[8081, 1234])
    asyncio.run(svc.start_dienst(dienst.id))

    assert d.netwerken == [c.netwerk]
    gestart = d.gestart[0]
    assert gestart["naam"] == "ih-cosmos"
    assert gestart["netwerk"] == c.netwerk
    # Niet hetzelfde nummer aan beide kanten: 8081 is op een gemiddelde
    # machine allang bezet (bij de eerste proef botste hij op LabX zelf).
    assert set(gestart["poorten"]) == {8081, 1234}
    assert all(v >= 18000 for v in gestart["poorten"].values())


def test_een_dienst_houdt_zijn_volume_bij_opnieuw_opbouwen(db):
    """Een dienst is wegwerpbaar, zijn data niet. Anders is "even opnieuw
    opbouwen" een beslissing met gevolgen die niemand overzag."""
    d = NepDocker()
    svc = _svc(db, d)
    c = svc.maak(naam="Hive")
    dienst = svc.voeg_dienst_toe(c.id, naam="ih-cosmos", image="cosmos:1")
    asyncio.run(svc.start_dienst(dienst.id))
    eerste_volume = d.gestart[0]["volume"]

    asyncio.run(svc.start_dienst(dienst.id))     # opnieuw opbouwen
    assert d.verwijderd, "de oude container moet weg"
    assert d.gestart[1]["volume"] == eerste_volume, "het volume blijft"


def test_twee_diensten_met_dezelfde_naam_kan_niet(db):
    """De naam is ook de DNS-naam op het netwerk."""
    from fastapi import HTTPException

    svc = _svc(db, NepDocker())
    c = svc.maak(naam="Hive")
    svc.voeg_dienst_toe(c.id, naam="ih-cosmos", image="cosmos:1")
    with pytest.raises(HTTPException) as fout:
        svc.voeg_dienst_toe(c.id, naam="ih-cosmos", image="cosmos:2")
    assert "DNS-naam" in str(fout.value.detail)


def test_een_mislukte_dienst_zet_het_cluster_op_fout_met_een_reden(db):
    d = NepDocker()

    async def _stuk(**kw):
        raise RuntimeError("geen image gevonden")

    d.run_dienst_container = _stuk
    svc = _svc(db, d)
    c = svc.maak(naam="Hive")
    dienst = svc.voeg_dienst_toe(c.id, naam="ih-cosmos", image="bestaat-niet")
    asyncio.run(svc.start_dienst(dienst.id))

    db.refresh(c)
    assert c.status == "fout"
    assert "ih-cosmos" in (c.fout or "") and "image" in (c.fout or "")


# ── de grens tussen cluster en labs ──────────────────────────────────────────

def test_een_cluster_met_branch_labs_gaat_er_niet_uit(db):
    """Anders blijven die labs achter zonder vaste diensten om mee te praten."""
    from fastapi import HTTPException

    from models.lab import Lab

    svc = _svc(db)
    c = svc.maak(naam="Hive")
    nu = "2026-10-16T10:00:00+00:00"
    db.add(Lab(id="lab1", name="feature/zoek", image="node:22", volume_name="v",
               status="running", cpu_limit=1, mem_limit_mb=512, ttl_hours=4,
               cluster_id=c.id, branch="feature/zoek", created_at=nu, updated_at=nu))
    db.commit()

    with pytest.raises(HTTPException) as fout:
        svc.verwijder(c.id)
    assert "feature/zoek" in str(fout.value.detail)


def test_een_gewoon_lab_hoort_bij_geen_cluster(db):
    """Elk bestaand lab heeft cluster_id en branch NULL, en daar mag niets
    aan veranderen."""
    from models.lab import Lab

    nu = "2026-10-16T10:00:00+00:00"
    p = Lab(id="gewoon", name="Fabric", image="python:3", volume_name="v",
            status="running", cpu_limit=1, mem_limit_mb=512, ttl_hours=4,
            created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    db.refresh(p)
    assert p.cluster_id is None and p.branch is None


def test_de_migratietabel_heeft_geen_dubbele_sleutels():
    """Twee keer dezelfde tabelnaam in _ADDITIVE_COLUMNS laat de eerste stil
    verdwijnen — Python houdt bij een dict-literal alleen de laatste over.

    Dat gebeurde: er stond al een "labs"-blok, er kwam een tweede bij, en de
    kolommen uit het eerste werden nooit toegevoegd. De backend startte
    daarna niet meer op ("no such column: labs.cluster_id"), en dat is nog de
    vriendelijke variant — bij een kolom die alleen soms gelezen wordt, merk
    je het pas veel later.
    """
    import ast
    import collections
    from pathlib import Path

    bron = (Path(__file__).resolve().parents[1] / "src" / "db"
            / "init_db.py").read_text(encoding="utf-8")
    boom = ast.parse(bron)
    gevonden = False
    for knoop in ast.walk(boom):
        if (isinstance(knoop, ast.Assign)
                and getattr(knoop.targets[0], "id", "") == "_ADDITIVE_COLUMNS"):
            gevonden = True
            sleutels = [k.value for k in knoop.value.keys]
            dubbel = [k for k, n in collections.Counter(sleutels).items() if n > 1]
            assert not dubbel, f"dubbele tabelnamen: {dubbel}"
    assert gevonden, "_ADDITIVE_COLUMNS niet gevonden"


def test_labs_krijgen_de_clusterkolommen_erbij():
    """Zonder deze migratie start de backend niet meer op zodra het model de
    kolommen kent."""
    from pathlib import Path

    bron = (Path(__file__).resolve().parents[1] / "src" / "db"
            / "init_db.py").read_text(encoding="utf-8")
    assert '("cluster_id", "INTEGER")' in bron
    assert '("branch", "VARCHAR(255)")' in bron


def test_een_foutmelding_toont_de_oorzaak_en_niet_de_pull_voortgang():
    """Een mislukte start gaf een scherm vol "Pulling fs layer".

    Docker schrijft voortgang en fouten door elkaar. Bij een image van
    anderhalve gigabyte stond er twintig regels laagjes-voortgang in het veld
    waar had moeten staan wát er misging.
    """
    from services.lab.clusters import _laatste_zinnige_regel

    rommel = (
        "Unable to find image 'cosmos:vnext' locally\n"
        "vnext: Pulling from cosmosdb/linux/azure-cosmos-emulator\n"
        "65899dddd4f4: Pulling fs layer\n"
        "ede28f09bd35: Pulling fs layer\n"
        "no matching manifest for linux/arm64/v8 in the manifest list entries\n"
    )
    assert _laatste_zinnige_regel(rommel).startswith("no matching manifest")


def test_een_lege_fout_levert_nog_steeds_iets_op():
    from services.lab.clusters import _laatste_zinnige_regel

    assert _laatste_zinnige_regel("") == "onbekende fout"


# ── poorten naar buiten ──────────────────────────────────────────────────────

def test_poorten_krijgen_een_eigen_bereik_en_botsen_niet(db):
    """De containerpoort op de machine publiceren leek simpel, maar botst op
    alles wat daar al luistert. Bij de eerste proef liep de Cosmos-emulator
    vast op "address already in use" — op 8081, waar LabX zelf al zat."""
    d = NepDocker()
    svc = _svc(db, d)
    c = svc.maak(naam="Hive")
    een = svc.voeg_dienst_toe(c.id, naam="cosmos", image="c:1", poorten=[8081])
    twee = svc.voeg_dienst_toe(c.id, naam="mailvanger", image="m:1", poorten=[8081])
    asyncio.run(svc.start_dienst(een.id))
    asyncio.run(svc.start_dienst(twee.id))

    db.refresh(een)
    db.refresh(twee)
    poort_een = list(een.poort_map.values())[0]
    poort_twee = list(twee.poort_map.values())[0]
    assert poort_een != poort_twee, "twee diensten mogen niet dezelfde poort krijgen"
    assert poort_een >= 18000 and poort_twee >= 18000


def test_het_adres_van_een_dienst_blijft_hetzelfde_bij_herstarten(db):
    """Anders moet je na elke herstart opnieuw opzoeken waar je hem vindt."""
    d = NepDocker()
    svc = _svc(db, d)
    c = svc.maak(naam="Hive")
    dienst = svc.voeg_dienst_toe(c.id, naam="cosmos", image="c:1", poorten=[8081])
    asyncio.run(svc.start_dienst(dienst.id))
    db.refresh(dienst)
    eerst = dict(dienst.poort_map)

    asyncio.run(svc.start_dienst(dienst.id))
    db.refresh(dienst)
    assert dict(dienst.poort_map) == eerst


def test_een_gebruiksfout_van_docker_wijst_niet_naar_de_help(db):
    """docker sluit een gebruiksfout af met "See 'docker run --help'." — dan
    is juist de laatste regel de minst bruikbare."""
    from services.lab.clusters import _laatste_zinnige_regel

    tekst = ("docker: Error response from daemon: failed to bind host port for "
             "127.0.0.1:8081: address already in use.\n"
             "See 'docker run --help'.\n")
    assert "address already in use" in _laatste_zinnige_regel(tekst)
