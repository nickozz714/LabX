"""Een lab dat op de machine zelf draait, binnen één projectmap.

Waarom dit bestaat: een klant kan eisen dat je aanmeldt vanaf een apparaat dat
zij kennen. Een container is geen apparaat en kan die claim niet afgeven -- en
hoort dat ook niet te kunnen. Op de host draait het commando als de gebruiker,
met de `az`-sessie die die zelf in een browser heeft opgezet.

Dat is een ander beveiligingsmodel dan de rest van LabX, en daarom staan hier
drie dingen vast:

1. **Een lab zonder `kind` is en blijft een container.** Geen enkel bestaand lab
   mag hierdoor van gedrag veranderen.
2. **Wat op een host geen betekenis heeft, slaagt niet stil.** Opschalen,
   opnieuw opbouwen, een extra werker: die geven een duidelijke fout. Een knop
   die niets doet is waar je het langst naar zoekt.
3. **Het agent-token komt nooit terug uit de API.**

De grens zelf -- de zandbak -- wordt getest in test_hostlab_zandbak.py, want
die draait echte commando's en hoort alleen op macOS.
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
    # Alle tabellen, niet een selectie: `_to_dict` telt de bezetting van een
    # lab en raakt daarvoor background_runs en de planning aan. Een fixture die
    # alleen de "eigen" tabellen maakt, valt daar steeds opnieuw over.
    # Elk model importeren -- niet een handvol. Een foreign key wijst naar een
    # tabel die je anders net niet meegenomen hebt, en dan valt create_all om
    # op iets dat niets met deze test te maken heeft.
    from models import (  # noqa: F401
        audit, azure_profile, background_run, board, claim_resource, guard_audit,
        guard_rule, lab, lab_extra, lab_secret, lab_worker, mcp_server, message,
        notification, plan, schedule, secret, skill, skill_tool, thread,
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


def _svc(db):
    from services.lab.lab_service import LabService
    return LabService(db)


async def _maak_hostlab(db, **kw):
    svc = _svc(db)
    return await svc.create(
        name=kw.get("name", "Klant A"), kind="host",
        host_project_dir=kw.get("project", "/Users/iemand/klant-a"),
        host_agent_token=kw.get("token", "geheimtoken"))


# ── het onderscheid ──────────────────────────────────────────────────────────

def test_een_lab_zonder_kind_blijft_een_container(db):
    """De belangrijkste regel. Elk bestaand lab heeft `kind` NULL, en mag
    daardoor niets anders gaan doen."""
    from models.lab import Lab
    from services.lab.lab_service import LabService

    p = Lab(id="oud", name="Bestaand", image="python:3", volume_name="v",
            status="running", cpu_limit=1, mem_limit_mb=512, ttl_hours=4,
            created_at="2026-01-01", updated_at="2026-01-01")
    assert p.kind is None
    assert LabService.is_host(p) is False


def test_een_hostlab_heeft_geen_container_nodig(db):
    import asyncio

    uit = asyncio.run(_maak_hostlab(db))
    assert uit["kind"] == "host"
    assert uit["host_project_dir"] == "/Users/iemand/klant-a"
    # Een hostlab start niet mee: de agent moet eerst bereikbaar zijn, en dat
    # wordt pas bij "aanzetten" gecontroleerd.
    assert uit["status"] == "stopped"


def test_het_agent_token_komt_nooit_uit_de_api(db):
    import asyncio
    import json

    uit = asyncio.run(_maak_hostlab(db, token="heelgeheim"))
    assert uit["heeft_agent_token"] is True
    assert "heelgeheim" not in json.dumps(uit)


def test_zonder_projectmap_of_token_kan_het_niet(db):
    import asyncio

    svc = _svc(db)
    with pytest.raises(Exception) as fout:
        asyncio.run(svc.create(name="Geen map", kind="host",
                               host_agent_token="t"))
    assert "projectmap" in str(fout.value.detail)

    with pytest.raises(Exception) as fout:
        asyncio.run(svc.create(name="Geen token", kind="host",
                               host_project_dir="/Users/iemand/x"))
    assert "LABX_HOST_AGENT_TOKEN" in str(fout.value.detail)


# ── wat een hostlab niet kan, zegt dat ook ───────────────────────────────────

@pytest.mark.parametrize("actie,woord", [
    ("scale", "Opschalen"),
    ("rebuild", "Opnieuw opbouwen"),
    ("ensure_extra_worker", "Een extra werker starten"),
])
def test_containerdingen_slagen_niet_stil(db, actie, woord):
    """Een knop die stilzwijgend niets doet, is erger dan een knop die zegt
    waarom hij niet kan."""
    import asyncio

    lab = asyncio.run(_maak_hostlab(db))
    svc = _svc(db)
    fn = getattr(svc, actie)
    with pytest.raises(Exception) as fout:
        if actie == "scale":
            asyncio.run(fn(lab["id"], min_workers=2))
        else:
            asyncio.run(fn(lab["id"]))
    detail = str(fout.value.detail)
    assert woord in detail
    assert "projectmap" in detail, "zeg waar het dan wél om draait"


def test_een_hostlab_start_niet_zonder_agent(db, monkeypatch):
    """Op "running" zetten terwijl de agent er niet is, zou betekenen dat het
    eerste commando pas de storing laat zien."""
    import asyncio

    from services.lab.host_runtime import HostRuntime

    lab = asyncio.run(_maak_hostlab(db))

    async def _weg(self):
        return False

    monkeypatch.setattr(HostRuntime, "is_bereikbaar", _weg)
    with pytest.raises(Exception) as fout:
        asyncio.run(_svc(db).start(lab["id"]))
    assert "hostagent" in str(fout.value.detail).lower()


def test_met_agent_gaat_hij_wel_aan(db, monkeypatch):
    import asyncio

    from services.lab.host_runtime import HostRuntime

    lab = asyncio.run(_maak_hostlab(db))

    async def _er(self):
        return True

    monkeypatch.setattr(HostRuntime, "is_bereikbaar", _er)
    uit = asyncio.run(_svc(db).start(lab["id"]))
    assert uit["status"] == "running"

    # En uitzetten betekent hier: LabX voert er niets meer in uit. Er gaat
    # geen machine af.
    uit = asyncio.run(_svc(db).stop(lab["id"]))
    assert uit["status"] == "stopped"


def test_de_chat_kan_een_hostlab_vanzelf_aanzetten(db, monkeypatch):
    """ensure_running is de weg waarlangs de chat een lab aanzet dat vanzelf
    uit is gegaan. Die eiste een container_id -- en dan strandt een hostlab
    meteen op "heeft geen container", bij een lab dat prima kon werken."""
    import asyncio

    from services.lab.host_runtime import HostRuntime

    lab = asyncio.run(_maak_hostlab(db))

    async def _er(self):
        return True

    monkeypatch.setattr(HostRuntime, "is_bereikbaar", _er)
    uit = asyncio.run(_svc(db).ensure_running(lab["id"]))
    assert uit["ok"] is True and uit["started"] is True
    # En nog eens: dan staat hij al aan en gebeurt er niets.
    uit = asyncio.run(_svc(db).ensure_running(lab["id"]))
    assert uit["started"] is False


# ── de runtime kiest op het lab, niet op een instelling ──────────────────────

def test_een_containerlab_houdt_de_docker_runtime(db):
    from models.lab import Lab
    from services.lab.docker_runtime import DockerRuntime

    p = Lab(id="c", name="Container", image="python:3", volume_name="v",
            status="running", cpu_limit=1, mem_limit_mb=512, ttl_hours=4,
            created_at="x", updated_at="x")
    db.add(p)
    db.commit()

    svc = _svc(db)
    assert isinstance(svc.runtime_voor(p), DockerRuntime)


def test_een_hostlab_krijgt_de_host_runtime(db):
    import asyncio

    from services.lab.host_runtime import HostRuntime

    lab = asyncio.run(_maak_hostlab(db))
    svc = _svc(db)
    rt = svc.runtime_voor(svc.get(lab["id"]))
    assert isinstance(rt, HostRuntime)
    assert rt.project_dir == "/Users/iemand/klant-a"


def test_de_host_runtime_weigert_containerwerk():
    """Niet stil slagen maar een duidelijke fout: er is geen container."""
    import asyncio

    from services.lab.host_runtime import HostAgentFout, HostRuntime

    rt = HostRuntime(project_dir="/Users/iemand/x", token="t")
    for fn in (rt.run_container, rt.run_ephemeral):
        with pytest.raises(HostAgentFout) as fout:
            asyncio.run(fn())
        assert "geen container" in str(fout.value)


def test_argv_wordt_correct_een_shell_regel(monkeypatch):
    """Gevonden door het echt te draaien. `exec_command` geeft argv door waarin
    het derde element een SCRIPT is en het vierde `$0` daarbinnen:

        ["sh", "-lc", "if command -v bash …; then exec bash -c \"$0\"; fi",
         "pwd && whoami"]

    Met spaties aan elkaar plakken las de shell dat script als losse woorden:
    "syntax error near unexpected token `then'". Elk deel apart aanhalen is de
    enige vertaling die de betekenis van docker behoudt.
    """
    import asyncio

    from services.lab.host_runtime import HostRuntime

    gezien = {}

    class _NepAntwoord:
        status_code = 200

        @staticmethod
        def json():
            return {"exit_code": 0, "output": "", "truncated": False}

    class _NepClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            gezien["command"] = json["command"]
            return _NepAntwoord()

    import httpx
    echt = httpx.AsyncClient
    httpx.AsyncClient = _NepClient
    try:
        rt = HostRuntime(project_dir="/Users/iemand/x", token="t")
        script = 'if command -v bash >/dev/null 2>&1; then exec bash -c "$0"; fi'
        asyncio.run(rt.exec("", ["sh", "-lc", script, "pwd && whoami"]))
    finally:
        httpx.AsyncClient = echt

    # Het script hoort als ÉÉN argument te blijven staan, en `-lc` wordt `-c`:
    # een login-shell leest je profiel uit de afgesloten thuismap, en zou
    # bovendien jouw aliassen en PATH mee laten doen met wat de agent draait.
    assert gezien["command"] == f"sh -c {chr(39)}{script}{chr(39)} 'pwd && whoami'"


def test_stdin_gaat_niet_naar_de_host():
    """Een tweede weg om inhoud binnen te brengen zou een eigen stel
    randgevallen meebrengen; write_file is de enige weg."""
    import asyncio

    from services.lab.host_runtime import HostAgentFout, HostRuntime

    rt = HostRuntime(project_dir="/Users/iemand/x", token="t")
    with pytest.raises(HostAgentFout) as fout:
        asyncio.run(rt.exec("", ["cat"], stdin=b"iets"))
    assert "stdin" in str(fout.value)
