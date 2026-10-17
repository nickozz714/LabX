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
        cluster_secret, guard_audit, guard_rule, lab, lab_extra, lab_host,
        lab_secret, lab_worker,
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
        self.netwerken_weg = []

    async def verwijder_netwerk(self, naam):
        self.netwerken_weg.append(naam)

    async def ensure_network_named(self, naam):
        self.netwerken.append(naam)

    async def run_dienst_container(self, *, naam, image, netwerk, volume,
                                   container_naam=None, poorten=None,
                                   omgeving=None, **kw):
        self.gestart.append({"naam": naam, "container_naam": container_naam or naam,
                             "image": image, "netwerk": netwerk,
                             "volume": volume, "poorten": dict(poorten or {}),
                             "omgeving": omgeving or {}})
        return f"container-{container_naam or naam}"

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
        asyncio.run(svc.verwijder(c.id))
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


# ── opruimen laat niets bij Docker achter ────────────────────────────────────

def test_verwijderen_ruimt_containers_en_netwerk_op(db):
    """Alleen de rijen weghalen voelt opgeruimd en is het niet.

    De containers van de vaste diensten blijven dan draaien en het netwerk
    blijft staan: geheugen dat niemand nog kan vinden, en een volgend cluster
    met dezelfde naam erft het netwerk van zijn voorganger.
    """
    docker = NepDocker()
    svc = _svc(db, docker)
    c = svc.maak(naam="Hive")
    d = svc.voeg_dienst_toe(c.id, naam="cosmos", image="emulator:1")
    d.container_id = "container-cosmos"
    db.commit()

    uit = asyncio.run(svc.verwijder(c.id))

    assert uit["niet_opgeruimd"] == []
    assert docker.verwijderd == ["container-cosmos"]
    assert docker.netwerken_weg == [c.netwerk]
    assert svc.lijst() == []


def test_een_container_die_niet_weg_wil_stopt_het_opruimen_niet(db):
    """Opruimen dat halverwege struikelt is erger dan opruimen dat iets laat
    staan — maar je hoort wel te zien wat er bleef."""
    docker = NepDocker()

    async def stug(cid, **kw):
        raise RuntimeError("daemon weg")

    docker.remove = stug
    svc = _svc(db, docker)
    c = svc.maak(naam="Hive")
    d = svc.voeg_dienst_toe(c.id, naam="cosmos", image="emulator:1")
    d.container_id = "container-cosmos"
    db.commit()

    uit = asyncio.run(svc.verwijder(c.id))

    assert svc.lijst() == [], "de rijen gaan hoe dan ook weg"
    assert any("cosmos" in k for k in uit["niet_opgeruimd"])


def test_twee_clusters_mogen_allebei_een_cosmos_hebben(db):
    """De DNS-naam is per cluster, de containernaam is globaal.

    Dit liep echt mis en het liep STIL mis: starten begint met een
    `docker rm -f` op de containernaam, dus de emulator van het tweede cluster
    maakte die van het eerste dood en nam zijn plaats in -- terwijl LabX voor
    allebei "draait" bleef melden. Gemeten op een echte daemon: de container-id
    van de eerste veranderde.
    """
    docker = NepDocker()
    svc = _svc(db, docker)
    een = svc.maak(naam="Hive")
    twee = svc.maak(naam="Hub")
    svc.voeg_dienst_toe(een.id, naam="cosmos", image="emulator:1")
    svc.voeg_dienst_toe(twee.id, naam="cosmos", image="emulator:1")

    for d in svc.diensten(een.id) + svc.diensten(twee.id):
        asyncio.run(svc.start_dienst(d.id))

    dns = [g["naam"] for g in docker.gestart]
    containers = [g["container_naam"] for g in docker.gestart]
    assert dns == ["cosmos", "cosmos"], "op het netwerk heet hij in beide clusters cosmos"
    assert len(set(containers)) == 2, "bij de daemon hebben ze verschillende namen"
    assert all(str(een.id) in c or str(twee.id) in c for c in containers)


# ── een branch-lab is een ontwikkellab, geen Fabric-werkplek ─────────────────

def test_een_branch_lab_krijgt_de_azure_cli_niet(db):
    """Deze installatie zet de Azure CLI in elk lab, want ze is voor
    Fabric-werk gebouwd. In een branch-lab kost dat 616 MB en vier minuten
    voor gereedschap dat een node-monorepo nooit aanroept."""
    from models.lab import Lab
    from services.lab.clusters import ClusterService
    from services.lab.lab_service import LabService

    c = ClusterService(db).maak(naam="Hive")
    nu = "2026-10-17T10:00:00+00:00"
    branch_lab = Lab(id="b1", name="Hive · feature/x", image="node:22", volume_name="v",
                     status="running", cpu_limit=2, mem_limit_mb=4096, ttl_hours=4,
                     cluster_id=c.id, branch="feature/x", created_at=nu, updated_at=nu)
    gewoon = Lab(id="g1", name="Gewoon", image="python:3", volume_name="v2",
                 status="running", cpu_limit=1, mem_limit_mb=2048, ttl_hours=4,
                 created_at=nu, updated_at=nu)
    db.add_all([branch_lab, gewoon])
    db.commit()

    svc = LabService(db)
    assert svc._az_gewenst(gewoon) is True, "een gewoon lab verandert niet"
    assert svc._az_gewenst(branch_lab) is False


def test_een_cluster_kan_de_azure_cli_alsnog_vragen(db):
    from models.lab import Lab
    from services.lab.clusters import ClusterService
    from services.lab.lab_service import LabService

    svc_c = ClusterService(db)
    c = svc_c.maak(naam="Hive")
    svc_c.wijzig(c.id, lab_azure=True)
    nu = "2026-10-17T10:00:00+00:00"
    db.add(Lab(id="b1", name="x", image="node:22", volume_name="v", status="running",
               cpu_limit=2, mem_limit_mb=4096, ttl_hours=4, cluster_id=c.id,
               branch="feature/x", created_at=nu, updated_at=nu))
    db.commit()

    assert LabService(db)._az_gewenst(db.get(Lab, "b1")) is True


def test_een_branch_lab_is_ruimer_bemeten(db):
    """Een gewoon lab is 1 CPU en 2 GB: genoeg om in rond te kijken, te krap
    om een monorepo in te bouwen."""
    from services.lab.clusters import BRANCH_LAB_CPU, BRANCH_LAB_MEM_MB, ClusterService

    c = ClusterService(db).maak(naam="Hive")
    uit = ClusterService(db).to_dict(c)
    assert uit["lab_cpu"] == BRANCH_LAB_CPU and uit["lab_mem_mb"] == BRANCH_LAB_MEM_MB
    assert BRANCH_LAB_MEM_MB > 2048 and BRANCH_LAB_CPU > 1.0


def test_het_lab_zegt_bij_welke_branch_het_hoort(db):
    """Zonder dit kan een scherm niet zeggen dat dit een branch-lab is, laat
    staan op welk adres het draait — en dan staat er een lab dat je nergens
    kunt openen."""
    from models.lab import Lab
    from services.lab.lab_service import LabService

    nu = "2026-10-17T10:00:00+00:00"
    db.add(Lab(id="b1", name="x", image="node:22", volume_name="v", status="stopped",
               cpu_limit=2, mem_limit_mb=4096, ttl_hours=4, cluster_id=3,
               branch="feature/x", poort_map={"8080": 18002}, created_at=nu, updated_at=nu))
    db.commit()

    uit = LabService(db)._to_dict(db.get(Lab, "b1"))
    assert uit["cluster_id"] == 3
    assert uit["branch"] == "feature/x"
    assert uit["poort_map"] == {"8080": 18002}


def test_een_branch_lab_start_altijd_als_branch_lab(db, monkeypatch):
    """Er is maar één deur.

    Zonder dit hing het ervan af welke knop je toevallig gebruikte: via het
    cluster kwam het lab op het clusternetwerk met zijn poorten en zijn
    LABX_-variabelen, via de labpagina op het gedeelde labnetwerk zonder
    poorten en zonder omgeving. Dat tweede lab stond vrolijk op "running" en
    was volstrekt onbruikbaar — en nergens stond waarom.
    """
    from models.lab import Lab
    from services.lab.clusters import ClusterService
    from services.lab.lab_service import LabService

    c = ClusterService(db).maak(naam="Hive")
    nu = "2026-10-17T10:00:00+00:00"
    db.add(Lab(id="b1", name="Hive · main", image="node:22", volume_name="v",
               status="stopped", cpu_limit=2, mem_limit_mb=4096, ttl_hours=4,
               cluster_id=c.id, branch="main", created_at=nu, updated_at=nu))
    db.commit()

    gezien = {}

    async def nep_branchstart(zelf, lab_id):
        gezien["lab_id"] = lab_id
        return {"id": lab_id}

    # Via monkeypatch en niet met een gewone toewijzing: dat laatste blijft
    # staan voor de rest van de sessie, en dan draait een latere test tegen
    # een nepmethode aan. Dat gebeurde ook -- de test die controleert of de
    # Azure-sessie gezet wordt, las de BRON van deze nepfunctie.
    monkeypatch.setattr(ClusterService, "start_branch_lab", nep_branchstart)
    asyncio.run(LabService(db).start("b1"))
    assert gezien.get("lab_id") == "b1", "de gewone start moet doorverwijzen"


def test_de_poorten_van_een_cluster_zijn_met_de_hand_te_zetten(db):
    """Zonder dit stond `lab_poorten` wel op het cluster maar was het niet te
    wijzigen — en dan kun je een verkeerde detectie alleen rechtzetten door
    opnieuw te verkennen, wat een modelaanroep kost."""
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    c = svc.maak(naam="Hive")
    svc.wijzig(c.id, lab_poorten=[5173, 7071])
    assert svc.get_or_404(c.id).lab_poorten == [5173, 7071]


def test_getallen_en_lijsten_worden_geen_tekst(db):
    """Deze stonden eerst in dezelfde lus als de tekstvelden, en dan werd
    `[5173, 7071]` de string "[5173, 7071]" — een lijst die er in een antwoord
    nog precies zo uitziet en bij het starten van een lab nergens meer op
    slaat."""
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    c = svc.maak(naam="Hive")
    svc.wijzig(c.id, lab_poorten=["5173", 7071], lab_cpu="4", lab_mem_mb="8192",
               lab_azure=True, lab_image="  node:22  ")
    ververst = svc.get_or_404(c.id)

    assert ververst.lab_poorten == [5173, 7071]
    assert ververst.lab_cpu == 4.0 and ververst.lab_mem_mb == 8192
    assert bool(ververst.lab_azure) is True
    assert ververst.lab_image == "node:22", "tekst wordt nog steeds getrimd"


# ── een cluster dat bij Azure moet kunnen ───────────────────────────────────

def test_een_branch_lab_erft_het_azure_profiel_van_zijn_cluster(db):
    """In het lab kan niemand `az login` doen; de sessie komt van dit
    profiel. Zonder dat valt de API om op "Missing API_KEY" terwijl de
    front-end gewoon draait — en dat ziet eruit als een half geslaagd lab."""
    from models.lab import Lab
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    c = svc.maak(naam="Hive")
    svc.wijzig(c.id, azure_profiel_id=1)

    nu = "2026-10-17T10:00:00+00:00"
    p = Lab(id="b1", name="x", image="node:22", volume_name="v", status="stopped",
            cpu_limit=2, mem_limit_mb=4096, ttl_hours=4, created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    # Wat maak_branch_lab doet na het aanmaken van het lab:
    p.cluster_id = c.id
    if svc.get_or_404(c.id).azure_profiel_id:
        p.azure_profile_id = svc.get_or_404(c.id).azure_profiel_id
    db.commit()

    assert db.get(Lab, "b1").azure_profile_id == 1


def test_een_ontbrekend_profiel_wordt_gemeld_als_het_nodig_is(db):
    """Een project dat zijn geheimen zelf ophaalt heeft een aanmelding nodig.
    Ontbreekt die, dan is dat geen detail maar de reden dat straks de helft
    van de applicatie niet start."""
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    c = svc.maak(naam="Hive")
    assert svc.to_dict(c)["azure_profiel_nodig"] is False, "zonder Azure geen eis"

    svc.wijzig(c.id, geheimen_zelf=True)
    assert svc.to_dict(svc.get_or_404(c.id))["azure_profiel_nodig"] is True

    svc.wijzig(c.id, azure_profiel_id=1)
    assert svc.to_dict(svc.get_or_404(c.id))["azure_profiel_nodig"] is False


def test_een_branch_lab_krijgt_de_azure_sessie(db):
    """Dit ontbrak, en daarom ging een branch-lab langs de hele
    Azure-machinerie heen: `az` stond er wel, maar `az account show` zei
    "Please run 'az login'". Een project dat zijn geheimen zelf uit een vault
    haalt, haalt er dan niets op — en valt om op "Missing API_KEY" terwijl de
    front-end gewoon draait."""
    import inspect

    from services.lab.clusters import ClusterService

    bron = inspect.getsource(ClusterService.start_branch_lab)
    assert "_sync_azure_profile_into_lab" in bron
