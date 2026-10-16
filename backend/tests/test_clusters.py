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


# ── branch-labs ──────────────────────────────────────────────────────────────

def _cluster_met_repo(db, svc):
    c = svc.maak(naam="Hive")
    c.repo_url = "https://github.com/beeminds-data/Intelligenthive.git"
    c.lab_image = "node:22-bookworm"
    c.start_script = "yarn build:all && yarn start:api"
    db.commit()
    return c


def test_een_branch_lab_erft_het_startscript_van_het_cluster(db, monkeypatch):
    """Het startscript hoort bij het cluster maar draait in het lab.

    Het hier overnemen betekent dat het bestáánde inrichten het uitvoert, met
    zijn log, zijn statussen en zijn herstel na een herstart. Een tweede
    manier om scripts te draaien zou al die dingen opnieuw moeten uitvinden.
    """
    import asyncio

    from models.lab import Lab
    from services.lab.clusters import ClusterService
    from services.lab.lab_service import LabService

    svc = ClusterService(db)
    c = _cluster_met_repo(db, svc)

    nu = "2026-10-16T12:00:00+00:00"

    async def _nep_create(**kw):
        p = Lab(id="branchlab", name=kw["name"], image="node:22-bookworm",
                volume_name="v", status="stopped", cpu_limit=1, mem_limit_mb=512,
                ttl_hours=4, created_at=nu, updated_at=nu)
        db.add(p)
        db.commit()
        return {"id": p.id}

    async def _nep_kloon(self, c, p, *, branch, basis):
        return None

    monkeypatch.setattr(LabService, "create", lambda self, **kw: _nep_create(**kw))
    monkeypatch.setattr(ClusterService, "_kloon_branch", _nep_kloon)
    monkeypatch.setattr(LabService, "_to_dict", lambda self, p: {"id": p.id})

    asyncio.run(svc.maak_branch_lab(c.id, branch="feature/x", basis="main"))
    p = db.get(Lab, "branchlab")
    assert p.cluster_id == c.id and p.branch == "feature/x"
    assert p.setup_script == c.start_script


def test_twee_labs_voor_dezelfde_branch_kan_niet(db):
    import asyncio

    from fastapi import HTTPException
    from models.lab import Lab

    svc = _svc(db)
    c = _cluster_met_repo(db, svc)
    nu = "2026-10-16T12:00:00+00:00"
    db.add(Lab(id="al-er", name="x", image="node:22", volume_name="v",
               status="running", cpu_limit=1, mem_limit_mb=512, ttl_hours=4,
               cluster_id=c.id, branch="feature/x", created_at=nu, updated_at=nu))
    db.commit()

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.maak_branch_lab(c.id, branch="feature/x"))
    assert fout.value.status_code == 409


def test_een_cluster_zonder_repo_maakt_geen_branch_lab(db):
    import asyncio

    from fastapi import HTTPException

    svc = _svc(db)
    c = svc.maak(naam="Leeg")
    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.maak_branch_lab(c.id, branch="feature/x"))
    assert "repo" in str(fout.value.detail).lower()


def test_diensten_en_branch_labs_delen_een_poortbereik(db):
    """Apart tellen zou een dienst en een lab op hetzelfde nummer zetten --
    ze draaien op dezelfde machine."""
    import asyncio

    from models.lab import Lab

    d = NepDocker()
    svc = _svc(db, d)
    c = svc.maak(naam="Hive")
    dienst = svc.voeg_dienst_toe(c.id, naam="cosmos", image="c:1", poorten=[8081])
    asyncio.run(svc.start_dienst(dienst.id))
    db.refresh(dienst)

    nu = "2026-10-16T12:00:00+00:00"
    p = Lab(id="bl", name="x", image="node:22", volume_name="v", status="stopped",
            cpu_limit=1, mem_limit_mb=512, ttl_hours=4, cluster_id=c.id,
            branch="feature/x", ports=[3000], created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()

    kaart = svc._lab_poorten(p, c)
    dienst_poorten = set(dienst.poort_map.values())
    assert not (set(kaart.values()) & dienst_poorten), (
        "een lab mag niet op de poort van een dienst landen")


def test_de_auth_header_staat_in_de_git_config_en_niet_als_losse_optie():
    """`git ${VAR:+-c "http.extraHeader=AUTHORIZATION: basic $X"}` wordt door
    de shell alsnog op spaties gesplitst; git kreeg "basic" als subcommando.

    Dat viel nooit op omdat het alleen misgaat MET een token -- een publieke
    repo kloont prima. Het zat daarom ook in de gewone kloon van labs en in
    publish.
    """
    from pathlib import Path

    for naam in ("clusters.py", "lab_service.py"):
        bron = (Path(__file__).resolve().parents[1] / "src" / "services" / "lab"
                / naam).read_text(encoding="utf-8")
        if "extraHeader" not in bron:
            continue
        assert "git config --global http.extraHeader" in bron, naam
        # Alleen naar CODE kijken: de uitleg hierboven noemt het foute
        # patroon met zoveel woorden, en die hoort niet mee te tellen.
        code = "\n".join(r for r in bron.splitlines()
                          if not r.lstrip().startswith("#"))
        assert "${GIT_AUTH:+-c" not in code, (
            f"{naam}: de losse -c met expansie is de constructie die stukging")


# ── kan het cluster bij zijn repo? ───────────────────────────────────────────

def _met_pat(db, svc, monkeypatch, antwoord):
    """Een cluster met een token, en een GitHub die `antwoord` geeft."""
    import urllib.error
    import urllib.request

    c = _cluster_met_repo(db, svc)
    c.repo_token_geheim = "pat"
    db.commit()
    monkeypatch.setattr(type(svc), "_pat", lambda self, c: "github_pat_proef")

    def _nep(verzoek, timeout=0):
        if isinstance(antwoord, int):
            raise urllib.error.HTTPError("u", antwoord, "nee", {}, None)

        class Antwoord:
            def read(self):
                import json
                return json.dumps(antwoord).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return Antwoord()

    monkeypatch.setattr(urllib.request, "urlopen", _nep)
    return c


def test_een_token_dat_wacht_op_goedkeuring_wordt_benoemd(db, monkeypatch):
    """GitHub geeft 404 als een fine-grained token de repo niet mag zien — of
    dat nu komt door goedkeuring, door de selectie, of door de verkeerde
    resource owner. Een kale "403" uit git noemt geen van drieën."""
    import asyncio

    svc = _svc(db)
    c = _met_pat(db, svc, monkeypatch, 404)
    uit = asyncio.run(svc.controleer_toegang(c))

    assert uit["ok"] is False
    for stuk in ("goedgekeurd", "Pending requests", "Repository access",
                 "resource owner"):
        assert stuk.lower() in uit["hint"].lower(), stuk


def test_een_verlopen_token_krijgt_een_ander_antwoord(db, monkeypatch):
    import asyncio

    svc = _svc(db)
    c = _met_pat(db, svc, monkeypatch, 401)
    uit = asyncio.run(svc.controleer_toegang(c))
    assert uit["ok"] is False
    assert "verlopen" in uit["hint"].lower()


def test_alleen_lezen_wordt_gemeld_maar_houdt_niets_tegen(db, monkeypatch):
    """Lezen is genoeg om te klonen. Pas als de agent wil pushen gaat het
    knellen, en dan wil je het van tevoren weten."""
    import asyncio

    svc = _svc(db)
    c = _met_pat(db, svc, monkeypatch,
                 {"full_name": "org/repo", "permissions": {"push": False}})
    uit = asyncio.run(svc.controleer_toegang(c))
    assert uit["ok"] is True and uit["mag_pushen"] is False
    assert "Read and write" in uit["hint"]


def test_een_branch_lab_wordt_niet_aangemaakt_als_het_token_niet_mag(db, monkeypatch):
    """Een lab maken en daarna weer opruimen omdat het token niet mag, is
    werk en verwarring voor niets."""
    import asyncio

    from fastapi import HTTPException
    from models.lab import Lab

    svc = _svc(db)
    c = _met_pat(db, svc, monkeypatch, 404)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.maak_branch_lab(c.id, branch="feature/x"))
    assert fout.value.status_code == 400
    assert db.query(Lab).count() == 0, "er mag geen half lab achterblijven"


def test_een_niet_github_repo_wordt_niet_beoordeeld(db, monkeypatch):
    """We weten dan niet welke API erbij hoort, en raden is erger dan niet
    kijken."""
    import asyncio

    svc = _svc(db)
    c = svc.maak(naam="Elders")
    c.repo_url = "https://git.intern.example/team/app.git"
    db.commit()
    uit = asyncio.run(svc.controleer_toegang(c))
    assert uit["gecontroleerd"] is False
