"""Het guardmodel over meer machines verdelen.

De data-guard vraagt een klein lokaal model om een tweede mening. Dat draaide
op één plek -- dezelfde server die ook alle labs doet -- dus een drukke
guard-ronde ging ten koste van het werk zelf.

Wat hier wordt vastgelegd is vooral wat er NIET mag gebeuren: de pool is een
uitbreiding en mag nooit een reden zijn dat de controle niet draait.
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
        message, model_runner, notification, plan, schedule, secret, skill,
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


def _host(db, naam="Werkpc", runners=1, status="gekoppeld"):
    from models.lab_host import LabHost

    nu = "2026-10-16T09:00:00+00:00"
    h = LabHost(naam=naam, machine_id=f"mid-{naam}", status=status,
                docker_host=f"ssh://nick@{naam.lower()}", ollama_runners=runners,
                created_at=nu, updated_at=nu)
    db.add(h)
    db.commit()
    return h


class NepDocker:
    def __init__(self):
        self.gestart, self.verwijderd = [], []

    async def run_model_container(self, *, naam, image, volume, verre_poort, **kw):
        self.gestart.append((naam, verre_poort))
        return f"container-{naam}"

    async def remove(self, cid, **kw):
        self.verwijderd.append(cid)


def _pool(db, docker=None):
    from services.lab.modelpool import ModelPool

    pool = ModelPool(db)
    if docker is not None:
        from services.lab.lab_service import LabService
        LabService._docker_op_host = lambda self, h: docker    # noqa: ARG005
    return pool


# ── starten en stoppen naar wat JIJ instelt ──────────────────────────────────

def test_er_komen_zoveel_runners_als_je_instelt(db):
    d = NepDocker()
    _host(db, runners=2)
    uit = asyncio.run(_pool(db, d).verzoen())
    assert uit["erbij"] == 2
    assert len(d.gestart) == 2


def test_runners_publiceren_op_de_loopback_van_die_machine(db):
    """Net als een labpoort: er hoort niets van dit alles op je LAN te staan,
    LabX komt er via een SSH-tunnel bij."""
    d = NepDocker()
    _host(db, runners=1)
    asyncio.run(_pool(db, d).verzoen())
    naam, poort = d.gestart[0]
    assert poort >= 21434


def test_twee_runners_krijgen_niet_dezelfde_poort(db):
    d = NepDocker()
    _host(db, runners=2)
    asyncio.run(_pool(db, d).verzoen())
    poorten = [p for _, p in d.gestart]
    assert len(set(poorten)) == 2


def test_het_aantal_omlaag_zetten_ruimt_op(db):
    d = NepDocker()
    h = _host(db, runners=2)
    asyncio.run(_pool(db, d).verzoen())
    h.ollama_runners = 0
    db.commit()
    uit = asyncio.run(_pool(db, d).verzoen())
    assert uit["eraf"] == 2
    assert len(d.verwijderd) == 2


def test_een_ongekoppelde_machine_krijgt_niets(db):
    """Dezelfde regel als bij labs: wie niet is toegelaten, krijgt geen werk."""
    d = NepDocker()
    _host(db, runners=2, status="nieuw")
    uit = asyncio.run(_pool(db, d).verzoen())
    assert uit["erbij"] == 0


# ── kiezen bij een vraag ─────────────────────────────────────────────────────

def test_de_minst_bezette_runner_wint(db, monkeypatch):
    from services.lab.modelpool import ModelPool

    d = NepDocker()
    _host(db, runners=2)
    asyncio.run(_pool(db, d).verzoen())
    for r in ModelPool(db).runners():
        r.status = "klaar"
    db.commit()

    async def _tunnel(docker_host, verre):
        return 50000 + verre

    monkeypatch.setattr("services.lab.poort_tunnel.tunnels.lokale_poort", _tunnel)

    eerste, _ = asyncio.run(ModelPool(db).kies())
    ModelPool.bezet(eerste)
    tweede, _ = asyncio.run(ModelPool(db).kies())
    assert tweede != eerste, "alles naar dezelfde sturen is geen pool"
    ModelPool.vrij(eerste)


def test_zonder_runners_geeft_de_pool_gewoon_niets(db):
    """Geen fout: dan gebruikt de guard de url uit de instellingen, precies
    zoals het altijd werkte. De pool mag nooit een reden zijn dat de controle
    niet draait."""
    from services.lab.modelpool import ModelPool

    assert asyncio.run(ModelPool(db).kies()) is None


def test_een_volle_pool_laat_de_guard_terugvallen(db, monkeypatch):
    """Liever de server even laten wachten dan in de rij gaan staan achter
    een andere guard-controle."""
    from services.lab.modelpool import ModelPool, MAX_GELIJKTIJDIG_PER_MACHINE

    d = NepDocker()
    _host(db, runners=1)
    asyncio.run(_pool(db, d).verzoen())
    r = ModelPool(db).runners()[0]
    r.status = "klaar"
    db.commit()

    async def _tunnel(docker_host, verre):
        return 50001

    monkeypatch.setattr("services.lab.poort_tunnel.tunnels.lokale_poort", _tunnel)
    for _ in range(MAX_GELIJKTIJDIG_PER_MACHINE):
        ModelPool.bezet(r.id)
    try:
        assert asyncio.run(ModelPool(db).kies()) is None
    finally:
        for _ in range(MAX_GELIJKTIJDIG_PER_MACHINE):
            ModelPool.vrij(r.id)


def test_een_mislukte_tunnel_haalt_de_runner_uit_de_pool(db, monkeypatch):
    from services.lab.modelpool import ModelPool

    d = NepDocker()
    _host(db, runners=1)
    asyncio.run(_pool(db, d).verzoen())
    r = ModelPool(db).runners()[0]
    r.status = "klaar"
    db.commit()

    async def _stuk(docker_host, verre):
        raise RuntimeError("Host is down")

    monkeypatch.setattr("services.lab.poort_tunnel.tunnels.lokale_poort", _stuk)
    assert asyncio.run(ModelPool(db).kies()) is None
    db.refresh(r)
    assert r.status == "fout" and "tunnel" in (r.fout or "").lower()


def test_de_teller_loopt_terug_ook_als_het_misgaat(db):
    """Blijft hij staan, dan lijkt die runner voorgoed vol en krijgt hij nooit
    meer werk."""
    from services.lab.modelpool import ModelPool

    ModelPool.bezet(99)
    ModelPool.vrij(99)
    ModelPool.vrij(99)          # twee keer vrijgeven mag niet negatief worden
    from services.lab.modelpool import _ONDERWEG
    assert _ONDERWEG.get(99, 0) == 0


def test_kiezen_noteert_wanneer_er_werk_heen_ging(db, monkeypatch):
    """Bij het KIEZEN en niet pas bij een geslaagd antwoord: een runner die
    steeds in een time-out loopt zou er anders ongebruikt uitzien, terwijl hij
    juist te veel krijgt."""
    from services.lab.modelpool import ModelPool

    d = NepDocker()
    _host(db, runners=1)
    asyncio.run(_pool(db, d).verzoen())
    r = ModelPool(db).runners()[0]
    r.status = "klaar"
    db.commit()
    assert r.laatst_gebruikt is None

    async def _tunnel(docker_host, verre):
        return 50002

    monkeypatch.setattr("services.lab.poort_tunnel.tunnels.lokale_poort", _tunnel)
    rid, _ = asyncio.run(ModelPool(db).kies())
    ModelPool.vrij(rid)
    db.refresh(r)
    assert r.laatst_gebruikt


# ── wat de meting op een echte MacBook liet zien ─────────────────────────────

def test_meer_containers_op_een_machine_is_geen_extra_capaciteit(db, monkeypatch):
    """Vier runners op één machine mogen niet acht vragen tegelijk aannemen.

    Gemeten op een MacBook: één runner met het model geladen stond op 1,44 GB,
    de drie ongebruikte op 17 MB. Ze delen bovendien dezelfde kernen, dus vier
    vragen tegelijk maken elke vraag trager zonder dat er meer af komt. De
    winst komt van een extra MACHINE, en de pool hoort dat ook zo te
    behandelen.
    """
    from services.lab.modelpool import ModelPool, MAX_GELIJKTIJDIG_PER_MACHINE

    d = NepDocker()
    _host(db, runners=4)
    asyncio.run(_pool(db, d).verzoen())
    for r in ModelPool(db).runners():
        r.status = "klaar"
    db.commit()

    async def _tunnel(docker_host, verre):
        return 50000 + verre

    monkeypatch.setattr("services.lab.poort_tunnel.tunnels.lokale_poort", _tunnel)

    gekozen = []
    for _ in range(MAX_GELIJKTIJDIG_PER_MACHINE):
        keus = asyncio.run(ModelPool(db).kies())
        assert keus, "binnen de grens hoort er gewoon een runner te zijn"
        gekozen.append(keus[0])
        ModelPool.bezet(keus[0])
    try:
        assert asyncio.run(ModelPool(db).kies()) is None, (
            "vier containers op één machine zijn geen vier machines")
    finally:
        for rid in gekozen:
            ModelPool.vrij(rid)


def test_het_werk_spreidt_over_machines_en_niet_over_containers(db, monkeypatch):
    """Met twee machines hoort de tweede vraag naar de ANDERE machine te gaan,
    ook als er op de eerste nog een container 'vrij' staat."""
    from services.lab.modelpool import ModelPool

    d = NepDocker()
    a = _host(db, naam="Werkpc", runners=2)
    b = _host(db, naam="iMac", runners=2)
    asyncio.run(_pool(db, d).verzoen())
    for r in ModelPool(db).runners():
        r.status = "klaar"
    db.commit()

    async def _tunnel(docker_host, verre):
        return 50000 + verre

    monkeypatch.setattr("services.lab.poort_tunnel.tunnels.lokale_poort", _tunnel)

    eerste = asyncio.run(ModelPool(db).kies())
    ModelPool.bezet(eerste[0])
    tweede = asyncio.run(ModelPool(db).kies())
    try:
        host_van = {r.id: r.host_id for r in ModelPool(db).runners()}
        assert host_van[eerste[0]] != host_van[tweede[0]], (
            "de tweede vraag hoort naar de andere machine")
    finally:
        ModelPool.vrij(eerste[0])


def test_meer_dan_vier_vragen_wordt_niet_stil_afgekapt(db):
    """Je vroeg er vijf en kreeg er vier, zonder dat ergens stond dat dat
    gebeurde."""
    from fastapi import HTTPException

    from services.lab.hosts import LabHostService

    h = _host(db, runners=0)
    with pytest.raises(HTTPException) as fout:
        LabHostService(db).wijzig(h.id, ollama_runners=5)
    assert "Maximaal 4" in str(fout.value.detail)
    assert "extra MACHINE" in str(fout.value.detail)
