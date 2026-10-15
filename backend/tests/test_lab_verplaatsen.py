"""Een lab naar een andere machine verhuizen.

De inzet is het volume: daar staat /workspace in, en dus het werk. Alles in
deze tests draait om de vraag "kan dat kwijtraken?" -- want de verhuizing is
de enige handeling in LabX die data van de ene daemon naar de andere tilt.

De containerlaag gaat NIET mee, met opzet: die halen we op de nieuwe machine
opnieuw op. Daarom moet het lab daar ook opnieuw worden ingericht.
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
        audit, azure_profile, background_run, board, claim_resource, guard_audit,
        guard_rule, lab, lab_extra, lab_host, lab_secret, lab_worker, mcp_server,
        message, notification, plan, schedule, secret, skill, skill_tool, thread,
        time_entry, tool, voice, workflow,
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


class NepRuntime:
    """Een daemon die onthoudt wat er met hem gebeurde."""

    def __init__(self, naam, arch="x86_64"):
        self.naam = naam
        self.arch = arch
        self.volumes = set()
        self.verwijderde_containers = []
        self.sporen = []

    async def volume_bestaat(self, volume):
        return volume in self.volumes

    async def export_volume(self, volume, doel, **kw):
        self.sporen.append(("export", volume))
        Path(doel).write_bytes(b"de-inhoud-van-het-volume")

    async def import_volume(self, volume, bron, **kw):
        assert Path(bron).read_bytes() == b"de-inhoud-van-het-volume"
        self.sporen.append(("import", volume))
        self.volumes.add(volume)

    async def create_volume(self, volume):
        self.sporen.append(("create_volume", volume))
        self.volumes.add(volume)

    async def remove_volume(self, volume):
        self.sporen.append(("remove_volume", volume))
        self.volumes.discard(volume)

    async def remove(self, container_id, **kw):
        self.verwijderde_containers.append(container_id)

    async def architectuur(self):
        return self.arch

    async def run_container(self, **kw):
        self.sporen.append(("run_container", kw.get("name")))
        return f"nieuw-op-{self.naam}"

    async def ensure_network(self):
        return None


def _host(db, naam="Werkpc", status="gekoppeld"):
    from services.lab.hosts import LabHostService

    svc = LabHostService(db)
    uit = svc.aanmelden(machine_id=f"mid-{naam}", naam=naam,
                        agent_url="http://192.168.2.40:8098", capaciteit={})
    rij = [h for h in svc.lijst() if h.naam == naam][0]
    if status == "gekoppeld":
        svc.koppelen(rij.id, code=uit["koppelcode"],
                     docker_host=f"ssh://nick@{naam.lower()}")
    return rij


def _lab(db, **kw):
    from models.lab import Lab

    nu = "2026-10-15T10:00:00+00:00"
    p = Lab(id=kw.get("id", "lab1"), name=kw.get("name", "Krimpenerwaard"),
            image="python:3", volume_name="labx_lab_abc", status=kw.get("status", "stopped"),
            container_id=kw.get("container_id"), cpu_limit=1, mem_limit_mb=512,
            ttl_hours=4, created_at=nu, updated_at=nu, host_id=kw.get("host_id"),
            kind=kw.get("kind"), host_project_dir=kw.get("host_project_dir"))
    db.add(p)
    db.commit()
    return p


def _svc(db, hier, daar=None, monkeypatch=None):
    from services.lab.lab_service import LabService

    svc = LabService(db, runtime=hier)
    if daar is not None:
        svc._docker_op_host = lambda host_id: daar   # noqa: ARG005
    return svc


# ── het werk raakt niet kwijt ────────────────────────────────────────────────

def test_het_volume_gaat_mee_naar_de_nieuwe_machine(db):
    """De kern: /workspace staat straks op de andere pc."""
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db)

    svc = _svc(db, hier, daar)
    asyncio.run(svc.verplaats(p.id, host_id=host.id))

    assert ("export", "labx_lab_abc") in hier.sporen
    assert ("import", "labx_lab_abc") in daar.sporen
    assert "labx_lab_abc" in daar.volumes
    assert db.get(type(p), p.id).host_id == host.id


def test_het_oude_volume_gaat_pas_weg_als_het_nieuwe_er_staat(db):
    """Andersom zou een mislukte import je werk kosten."""
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db)

    asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))

    stappen = [s[0] for s in hier.sporen]
    assert stappen.index("export") < stappen.index("remove_volume")
    assert "labx_lab_abc" not in hier.volumes


def test_een_mislukte_import_laat_het_oude_volume_staan(db):
    """Als de overkant halverwege afhaakt, is het origineel het enige wat je
    nog hebt. Dat mag dus niet al weg zijn."""
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")

    async def _stuk(volume, bron, **kw):
        raise RuntimeError("ssh: connect to host werkpc port 22: Host is down")

    daar.import_volume = _stuk
    host = _host(db)
    p = _lab(db)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as fout:
        asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))
    assert "Host is down" in str(fout.value.detail)
    assert "labx_lab_abc" in hier.volumes, "het origineel moet er nog zijn"
    db.refresh(p)
    assert p.host_id is None, "en het lab hoort nog hier"
    # Mislukt is niet stuk: er is niets kwijt, dus het lab hoort gewoon weer
    # startbaar te zijn op zijn oude machine en niet op "error" te blijven.
    assert p.status == "stopped"
    assert "Verplaatsen mislukte" in (p.error or "")


def test_verplaatsen_kan_niet_terwijl_er_werk_draait(db):
    """Het volume onder een lopende agent weghalen heeft geen nette afloop."""
    from fastapi import HTTPException

    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    host = _host(db)
    p = _lab(db, status="running")
    svc = _svc(db, hier, daar)
    svc.bezetting = lambda lab_id: {1: 2}   # noqa: ARG005

    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.verplaats(p.id, host_id=host.id))
    assert fout.value.status_code == 409
    assert "2 sessie" in str(fout.value.detail)
    assert hier.sporen == [], "er mag niets aan het volume zijn gebeurd"


# ── wat er NIET meegaat, en dus opnieuw moet ─────────────────────────────────

def test_na_de_verhuizing_moet_het_lab_opnieuw_worden_ingericht(db):
    """De containerlaag blijft achter. Zou provision_status op "ok" blijven
    staan, dan denkt het lab dat zijn pakketten er zijn terwijl de nieuwe
    container kaal is -- en dat merk je pas bij het eerste commando."""
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db)
    p.provision_status = "ok"
    db.commit()

    asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))
    assert db.get(type(p), p.id).provision_status == "pending"


def test_de_oude_container_wordt_opgeruimd_op_de_oude_machine(db):
    """Niet op de nieuwe, en niet achtergelaten: anders staat er een dode
    container op die pc totdat iemand hem handmatig vindt."""
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db, container_id="c-oud")

    svc = _svc(db, hier, daar)
    svc.ensure_workers = lambda lab: []   # noqa: ARG005
    asyncio.run(svc.verplaats(p.id, host_id=host.id))
    assert db.get(type(p), p.id).container_id is None


# ── grenzen ──────────────────────────────────────────────────────────────────

def test_een_hostlab_verhuist_niet(db):
    """Daar is geen volume en geen container -- alleen een map op een pc."""
    from fastapi import HTTPException

    host = _host(db)
    p = _lab(db, kind="host", host_project_dir="/Users/nick/project")
    with pytest.raises(HTTPException) as fout:
        asyncio.run(_svc(db, NepRuntime("server"), NepRuntime("w")).verplaats(
            p.id, host_id=host.id))
    assert "hostlab" in str(fout.value.detail).lower()


def test_naar_dezelfde_machine_doet_niets(db):
    """Zou dit toch exporteren, dan tilde je een volume van en naar dezelfde
    daemon -- met alleen de kans om het kwijt te raken als resultaat."""
    hier = NepRuntime("server")
    hier.volumes.add("labx_lab_abc")
    p = _lab(db)
    asyncio.run(_svc(db, hier).verplaats(p.id, host_id=None))
    assert hier.sporen == []


def test_een_volle_machine_neemt_er_geen_lab_bij(db):
    """`max_labs` moet een grens zijn en niet een getal in een scherm."""
    from fastapi import HTTPException
    from services.lab.hosts import LabHostService

    host = _host(db)
    LabHostService(db).wijzig(host.id, max_labs=1)
    _lab(db, id="lab-er-al", name="Bestaand", host_id=host.id)
    p = _lab(db)

    with pytest.raises(HTTPException) as fout:
        asyncio.run(_svc(db, NepRuntime("server"), NepRuntime("w")).verplaats(
            p.id, host_id=host.id))
    assert fout.value.status_code == 409
    assert "vol" in str(fout.value.detail)


# ── wat alleen de echte proef op een tweede machine liet zien ────────────────

def test_een_draaiend_lab_draait_na_de_verhuizing_weer(db):
    """Dit ging mis bij de eerste echte verhuizing (iMac -> server).

    `verplaats` haalt de containers weg en zet `container_id` op None. Daarna
    `start()` aanroepen kan niet: die weigert juist een lab zonder container
    ("hij moet opnieuw aangemaakt worden"). Het volume was al over, maar het
    lab bleef uit staan met een melding die nergens naar wees.
    """
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db, status="running", container_id="c-oud")

    svc = _svc(db, hier, daar)
    uit = asyncio.run(svc.verplaats(p.id, host_id=host.id))

    assert uit["status"] == "running"
    gemaakt_daar = [x for x in daar.sporen if x[0] == "run_container"]
    gemaakt_hier = [x for x in hier.sporen if x[0] == "run_container"]
    assert gemaakt_daar, "verse container op de NIEUWE machine"
    assert not gemaakt_hier, "en niet op de oude"
    db.refresh(p)
    assert p.container_id == "nieuw-op-werkpc"


def test_een_lab_dat_uit_stond_blijft_uit_na_de_verhuizing(db):
    """Verhuizen is geen reden om hem aan te zetten."""
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db, status="stopped")

    uit = asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))
    assert uit["status"] == "stopped"
    assert not [s for s in daar.sporen if s[0] == "run_container"]


def test_na_het_punt_van_geen_terugkeer_wijst_het_lab_naar_de_NIEUWE_machine(db):
    """De gevaarlijkste van de twee.

    Gaat het mis NADAT het volume op de nieuwe machine staat en van de oude
    weg is, dan is "terugzetten waar hij stond" geen herstel maar een leugen:
    LabX wijst dan naar een machine waar jouw werk niet meer staat. Precies
    dat gebeurde bij de echte proef -- het volume stond op de server en de
    database zei iMac.
    """
    from fastapi import HTTPException

    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")

    async def _stuk(**kw):
        raise RuntimeError("daemon weg tijdens het starten")

    daar.run_container = _stuk
    host = _host(db)
    p = _lab(db, status="running", container_id="c-oud")

    with pytest.raises(HTTPException) as fout:
        asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))

    db.refresh(p)
    assert p.host_id == host.id, "het volume staat daar, dus daar hoort het lab"
    assert "NIEUWE machine" in str(fout.value.detail)
    assert "labx_lab_abc" in daar.volumes
    assert "labx_lab_abc" not in hier.volumes


# ── wat de eerste echte verhuizing naar een Mac liet zien ────────────────────

def test_verhuizen_naar_een_andere_processor_wordt_gemeld(db):
    """Het volume gaat prima mee -- bestanden zijn bestanden. De containerlaag
    wordt opnieuw opgebouwd, en daar bestaat niet alles voor allebei: Google
    Chrome heeft geen arm64-build voor Linux. Dat liep bij de eerste echte
    verhuizing (amd64-server -> Apple Silicon) stuk op een muur apt-uitvoer,
    waar nergens uit bleek dat de ARCHITECTUUR het probleem was.
    """
    hier = NepRuntime("server", arch="x86_64")
    daar = NepRuntime("macbook", arch="aarch64")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db)

    uit = asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))
    assert uit["waarschuwing"]
    assert "aarch64" in uit["waarschuwing"] and "x86_64" in uit["waarschuwing"]


def test_dezelfde_processor_geeft_geen_ruis(db):
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db)
    uit = asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))
    assert uit["waarschuwing"] is None


def test_een_verlopen_lab_krijgt_bij_verhuizen_een_verse_vervaltijd(db):
    """Je verplaatst hem, dus je wilt hem nog. Bleef hij verlopen, dan keek je
    na de verhuizing naar een lab dat niets doet -- en de reden daarvoor was
    iets van vorige week."""
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")
    host = _host(db)
    p = _lab(db, status="expired")
    p.expires_at = "2026-10-11T08:13:09+00:00"
    p.error = "iets ouds"
    db.commit()

    uit = asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))
    db.refresh(p)
    assert p.status == "stopped", "verlopen blijven is hier een doodlopend spoor"
    assert p.expires_at > "2026-10-15"
    assert p.error is None
    assert "verlopen" in (uit.get("melding") or "")


def test_een_waarschuwing_mag_de_verhuizing_nooit_breken(db):
    """Een daemon die geen architectuur wil zeggen is geen reden om een
    geslaagde verhuizing alsnog te laten mislukken."""
    hier, daar = NepRuntime("server"), NepRuntime("werkpc")
    hier.volumes.add("labx_lab_abc")

    async def _stuk():
        raise RuntimeError("docker info weigert")

    daar.architectuur = _stuk
    host = _host(db)
    p = _lab(db)

    uit = asyncio.run(_svc(db, hier, daar).verplaats(p.id, host_id=host.id))
    assert uit["waarschuwing"] is None
    assert db.get(type(p), p.id).host_id == host.id


# ── starten zonder container ─────────────────────────────────────────────────

def test_een_lab_zonder_container_kan_gewoon_gestart_worden(db):
    """Dit maakte een lab onstartbaar op het moment dat starten juist het
    goede antwoord was.

    Na een verhuizing (of een opgeruimde container, of een herbouw die
    halverwege sneuvelde) heeft een lab geen container_id. `start()` weigerde
    dat met "Lab heeft geen container (status error?)" -- een knop die niets
    doet en een melding die naar een foutstatus wijst die er niet is. Terwijl
    het volume er gewoon staat en het image vastligt.
    """
    hier = NepRuntime("server")
    hier.volumes.add("labx_lab_abc")
    p = _lab(db, status="stopped", container_id=None)

    from services.lab.lab_service import LabService

    svc = LabService(db, runtime=hier)
    uit = asyncio.run(svc.start(p.id))

    assert uit["status"] == "running"
    db.refresh(p)
    assert p.container_id == "nieuw-op-server"
    assert [x for x in hier.sporen if x[0] == "run_container"]


def test_starten_zonder_container_zet_de_vervaltijd_opnieuw(db):
    """Starten is gebruik; anders valt hij bij de eerstvolgende ronde meteen
    weer om."""
    hier = NepRuntime("server")
    hier.volumes.add("labx_lab_abc")
    p = _lab(db, status="stopped", container_id=None)
    p.expires_at = "2026-10-11T08:00:00+00:00"
    p.error = "iets ouds"
    db.commit()

    from services.lab.lab_service import LabService

    asyncio.run(LabService(db, runtime=hier).start(p.id))
    db.refresh(p)
    assert p.expires_at > "2026-10-15"
    assert p.error is None
