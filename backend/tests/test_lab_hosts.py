"""Computers in je netwerk die labs draaien.

Het beveiligingsmodel staat of valt met één regel: **wat zich meldt, mag
zichzelf niet toelaten.** Een aanmelding komt van iets op je netwerk dat zegt
dat het een lab-host is — dat is geen bewijs. Daarom komt een melder binnen als
`nieuw` en krijgt hij niets tot een mens de code bevestigt die op het scherm
van díé machine staat.

Verder: een lab zonder host blijft op deze server draaien. Elk bestaand lab
heeft `host_id` NULL, en daar mag niets aan veranderen.
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


def _svc(db):
    from services.lab.hosts import LabHostService
    return LabHostService(db)


def _meld(db, **kw):
    return _svc(db).aanmelden(
        machine_id=kw.get("machine_id", "mid-1"),
        naam=kw.get("naam", "Werkpc"),
        agent_url=kw.get("agent_url", "http://192.168.2.40:8098"),
        docker_host=kw.get("docker_host"),
        capaciteit=kw.get("capaciteit") or {"cpus": 8, "geheugen_mb": 16384})


# ── aanmelden geeft niets weg ────────────────────────────────────────────────

def test_wie_zich_meldt_krijgt_nog_niets(db):
    """De kern. Een aanmelding zet een rij op `nieuw` — meer niet."""
    uit = _meld(db)
    assert uit["status"] == "nieuw"
    assert uit["koppelcode"], "er moet een code zijn om over te typen"
    assert uit["wakker_houden"] is False, "niet wakker blijven voor werk dat er niet is"

    host = _svc(db).lijst()[0]
    assert host.docker_host is None or host.status == "nieuw"


def test_dezelfde_machine_komt_niet_twee_keer_in_de_lijst(db):
    """Een laptop op wifi krijgt een ander IP. Herkennen gaat op machine_id,
    niet op adres — anders groeit de lijst elke dag."""
    _meld(db, agent_url="http://192.168.2.40:8098")
    _meld(db, agent_url="http://192.168.2.77:8098")

    hosts = _svc(db).lijst()
    assert len(hosts) == 1
    assert hosts[0].agent_url.endswith(":8098")


def test_de_koppelcode_lekt_niet_meer_na_het_koppelen(db):
    """Zou hij blijven terugkomen, dan is het een wachtwoord dat niemand
    vervangt — en elke aanmelding van buiten kan hem uitlezen."""
    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")

    opnieuw = _meld(db)
    assert opnieuw["status"] == "gekoppeld"
    assert opnieuw["koppelcode"] is None


def test_een_verkeerde_code_koppelt_niet(db):
    svc = _svc(db)
    _meld(db)
    host = svc.lijst()[0]
    with pytest.raises(Exception) as fout:
        svc.koppelen(host.id, code="000000", docker_host="ssh://nick@werkpc")
    assert "koppelcode" in str(fout.value.detail).lower()
    assert svc.get_or_404(host.id).status == "nieuw"


def test_koppelen_zonder_docker_adres_kan_niet(db):
    """Een gekoppelde host zonder adres is een host waar je labs op zet die
    nooit starten."""
    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    with pytest.raises(Exception) as fout:
        svc.koppelen(host.id, code=uit["koppelcode"], docker_host="")
    assert "ssh://" in str(fout.value.detail)


def test_wakker_houden_pas_na_koppelen(db):
    """Een machine die niet is toegelaten, hoeft ook niet wakker te blijven."""
    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    assert _meld(db)["wakker_houden"] is True


# ── labs en hosts ────────────────────────────────────────────────────────────

def _lab(db, **kw):
    from models.lab import Lab

    nu = "2026-10-15T10:00:00+00:00"
    p = Lab(id=kw.get("id", "lab1"), name=kw.get("name", "Fabric"), image="python:3",
            volume_name="v", status="running", cpu_limit=1, mem_limit_mb=512,
            ttl_hours=4, created_at=nu, updated_at=nu, host_id=kw.get("host_id"))
    db.add(p)
    db.commit()
    return p


def test_een_lab_zonder_host_draait_hier(db):
    """Elk bestaand lab heeft host_id NULL en moet exact blijven doen wat het
    deed: de Docker van deze server."""
    from services.lab.docker_runtime import DockerRuntime
    from services.lab.lab_service import LabService

    p = _lab(db)
    rt = LabService(db).runtime_voor(p)
    assert isinstance(rt, DockerRuntime)
    assert rt._docker_host is None, "geen SSH-omweg voor een gewoon lab"


def test_een_lab_op_een_gekoppelde_host_praat_via_ssh(db):
    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")

    p = _lab(db, host_id=host.id)
    rt = LabService(db).runtime_voor(p)
    assert rt._docker_host == "ssh://nick@werkpc"


def test_een_lab_op_een_ongekoppelde_host_start_niet(db):
    """Anders kan alles wat zich op je netwerk als lab-host voordoet, labs
    gaan draaien."""
    from services.lab.lab_service import LabService

    _meld(db)
    host = _svc(db).lijst()[0]
    p = _lab(db, host_id=host.id)

    with pytest.raises(Exception) as fout:
        LabService(db).runtime_voor(p)
    assert "niet gekoppeld" in str(fout.value.detail)


def test_een_host_met_labs_erop_gaat_er_niet_uit(db):
    """Anders wijzen die labs naar een machine die niet meer bestaat."""
    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    _lab(db, host_id=host.id, name="Krimpenerwaard")

    with pytest.raises(Exception) as fout:
        svc.verwijder(host.id)
    assert "Krimpenerwaard" in str(fout.value.detail)


# ── wat alleen door het te draaien bleek ─────────────────────────────────────

def test_daemon_up_kijkt_ook_naar_de_uitvoer():
    """`docker info --format ...` geeft exit-code 0 terwijl het faalt:

        error during connect: ... exec: "ssh": executable file not found
        $ echo $?
        0

    Op alleen de code vertrouwen gaf een groene "verbinding testen" voor een
    machine die er niet is.
    """
    import inspect

    from services.lab.docker_runtime import DockerRuntime

    bron = inspect.getsource(DockerRuntime.daemon_up)
    assert "out" in bron and "strip()" in bron, "de uitvoer moet meetellen"


def test_het_image_heeft_een_ssh_client():
    """Zonder openssh-client in de backend-image mislukt élke lab-host in het
    netwerk, en de melding wijst naar docker in plaats van naar ssh."""
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "openssh-client" in dockerfile


# ── alles wat met een lab gebeurt, gebeurt op ZIJN machine ───────────────────

def test_een_lab_op_een_netwerkmachine_overleeft_een_herstart(db, monkeypatch):
    """Reconcile keek alleen naar de Docker van deze server. Een lab op een
    netwerkmachine stond daar niet in de lijst, en werd dus bij élke herstart
    van LabX op "error" gezet met "Container niet meer gevonden"."""
    import asyncio

    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    p = _lab(db, host_id=host.id)
    p.container_id = "c-op-de-werkpc"
    db.commit()

    class Daemon:
        def __init__(self, rijen):
            self.rijen = rijen

        async def list_managed(self):
            return list(self.rijen)

        async def remove(self, cid, **kw):
            raise AssertionError(f"niets opruimen: {cid}")

    lab_svc = LabService(db, runtime=Daemon([]))          # deze server: leeg
    monkeypatch.setattr(
        "services.lab.lab_service.DockerRuntime",
        lambda **kw: Daemon([{"id": "c-op-de-werkpc", "name": "labx-lab-abc",
                              "state": "running"}]))

    asyncio.run(lab_svc.reconcile_on_start())
    db.refresh(p)
    assert p.status == "running"
    assert not p.error


def test_een_machine_die_uit_staat_meldt_geen_labs_kapot(db, monkeypatch):
    """"Ik kon er niet bij" is iets anders dan "de container is weg". Dat
    verschil niet maken betekent dat een pc die een nacht uit staat al je labs
    erop op error zet."""
    import asyncio

    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    p = _lab(db, host_id=host.id)
    p.container_id = "c-op-de-werkpc"
    db.commit()

    class Leeg:
        async def list_managed(self):
            return []

        async def remove(self, cid, **kw):
            return None

    class Weg:
        async def list_managed(self):
            raise RuntimeError("ssh: connect to host werkpc port 22: Host is down")

    monkeypatch.setattr("services.lab.lab_service.DockerRuntime", lambda **kw: Weg())
    asyncio.run(LabService(db, runtime=Leeg()).reconcile_on_start())
    db.refresh(p)
    assert p.status == "running", "onbereikbaar is geen bewijs dat de container weg is"


# ── het script op die andere computer krijgen ────────────────────────────────

def test_het_agent_script_is_zonder_login_op_te_halen():
    """De enige plek waar nog niemand kan inloggen.

    Op de computer die mee gaat doen is geen LabX-account, en er valt er ook
    geen te maken. Zou dit eindpunt een token eisen, dan moest je dat token in
    een curl-regel plakken en bleef het achter in de shell-geschiedenis van een
    vreemde machine. Er staat niets geheims in: het is clientcode, hij voert
    geen commando's uit, en hij krijgt pas iets zodra een MENS de koppelcode
    bevestigt.
    """
    from fastapi.testclient import TestClient

    import server

    # Geen `with`: dat draait de lifespan, en die registreert geplande taken
    # die maar één keer mogen bestaan in een proces. Voor een routevraag is de
    # opstart van de app niet nodig.
    r = TestClient(server.app).get("/api/lab-hosts/agent")
    assert r.status_code == 200, r.text
    assert "labx_lab_host" in r.text
    compile(r.text, "labx_lab_host.py", "exec")   # en het is geen half bestand


def test_de_beveiligde_lab_host_routes_blijven_dicht():
    """Spiegelbeeld van hierboven: alleen het script is open."""
    from fastapi.testclient import TestClient

    import server

    client = TestClient(server.app)
    for pad in ("/api/lab-hosts", "/api/lab-hosts/sleutel", "/api/lab-hosts/1"):
        assert client.get(pad).status_code == 401, pad


def test_de_open_router_staat_voor_de_beveiligde():
    """Dit ging mis en kostte een 401 op de ene plek waar niemand kan inloggen.

    `/lab-hosts/{host_id}` vangt ook `/lab-hosts/agent` af, en de
    router-dependency van de beveiligde router weigert VOORDAT iemand kijkt of
    "agent" wel een getal is. De volgorde van include_router is hier dus geen
    smaak maar gedrag.
    """
    from pathlib import Path

    bron = (Path(__file__).resolve().parents[1] / "src" / "server.py").read_text()
    open_op = bron.index("lab_host_router.open_router")
    beveiligd_op = bron.index("lab_host_router.router, prefix")
    assert open_op < beveiligd_op, "de open router moet eerst ge-include worden"


# ── welk ADRES heeft die machine? ────────────────────────────────────────────


def _nep_verzoek(headers=None, peer="172.29.0.4"):
    """Een verzoek zoals het binnenkomt, zonder HTTP eromheen.

    Bewust geen TestClient: die draait het verzoek in een andere thread, en
    een in-geheugen SQLite geeft daar een LEGE database -- dan test je de
    opzet van je test in plaats van je code.
    """
    from starlette.datastructures import Headers

    class Client:
        host = peer

    class Verzoek:
        def __init__(self):
            self.headers = Headers(headers or {})
            self.client = Client()

    return Verzoek()


def test_achter_een_proxy_wordt_niet_het_adres_van_de_proxy_voorgesteld(db):
    """Dit ging echt mis bij de eerste koppeling op de server.

    De agent meldt zich via nginx, dus het adres van de VERBINDING is de
    nginx-container. LabX stelde daardoor `ssh://nick@172.29.0.4` voor, en
    inloggen daarop geeft "connection refused" op een adres dat je nergens
    kunt thuisbrengen -- terwijl de melding naar je sleutel en naar Remote
    Login wijst, die allebei in orde zijn.

    Wat de agent zelf gemeten heeft, wint daarom.
    """
    from routers.lab_host_router import aanmelden

    aanmelden({"machine_id": "m1", "naam": "Werkpc", "agent_poort": 8098,
               "agent_ip": "192.168.2.68"},
              _nep_verzoek(), db)

    host = [h for h in _svc(db).lijst() if h.machine_id == "m1"][0]
    assert "192.168.2.68" in (host.agent_url or "")
    assert "172.29" not in (host.agent_url or ""), "het adres van de proxy, niet van de machine"


def test_zonder_eigen_opgave_telt_de_forwarded_kop(db):
    """Een oudere agent meet zijn eigen IP nog niet. Dan is de kop van onze
    eigen proxy nog altijd beter dan het adres van die proxy zelf."""
    from routers.lab_host_router import aanmelden

    aanmelden({"machine_id": "m2", "naam": "Oude agent", "agent_poort": 8098},
              _nep_verzoek({"x-forwarded-for": "192.168.2.70, 172.29.0.4"}), db)

    host = [h for h in _svc(db).lijst() if h.machine_id == "m2"][0]
    assert "192.168.2.70" in (host.agent_url or "")


def test_zonder_proxy_blijft_het_adres_van_de_verbinding_gelden(db):
    """Draait LabX zonder proxy ervoor, dan was er nooit iets mis en moet dat
    zo blijven."""
    from routers.lab_host_router import aanmelden

    aanmelden({"machine_id": "m3", "naam": "Direct", "agent_poort": 8098},
              _nep_verzoek(peer="192.168.2.90"), db)

    host = [h for h in _svc(db).lijst() if h.machine_id == "m3"][0]
    assert "192.168.2.90" in (host.agent_url or "")


def test_de_proxy_geeft_de_keten_door():
    """De backend kan X-Forwarded-For alleen lezen als nginx hem zet."""
    from pathlib import Path

    conf = (Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf").read_text()
    assert "X-Forwarded-For" in conf


def test_de_agent_meet_zijn_eigen_adres_richting_labx():
    """Een laptop heeft makkelijk drie adressen (wifi, ethernet, VPN). De
    vraag is niet "wat is mijn IP" maar "over welk adres loopt het verkeer
    naar LabX" -- en dat beantwoordt het besturingssysteem zelf."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab-host"))
    import labx_lab_host

    # Een route naar buiten bestaat altijd; welk adres eruit komt doet er niet
    # toe, als het maar een adres van DEZE machine is en geen lege waarde.
    ip = labx_lab_host.eigen_ip("http://192.168.2.15:8080")
    assert ip and ip.count(".") == 3, ip
    assert labx_lab_host.eigen_ip("niet-eens-een-url") is None


def test_de_pad_reparatie_wordt_niet_overgeslagen_als_docker_al_in_ons_pad_staat(tmp_path, monkeypatch):
    """De fout die de MacBook een avond kostte.

    De agent draait in een gewone terminal, waar /usr/local/bin gewoon in het
    PATH zit. De controle "staat docker al in het PATH?" was dan waar, dus
    sloeg hij de reparatie over -- op precies de machines die hem nodig
    hadden. Het PATH dat telt is namelijk dat van een NIET-interactieve
    SSH-sessie, en dat is altijd kaal. Resultaat: "docker: command not found"
    bij het testen van de verbinding, terwijl docker er gewoon draaide.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab-host"))
    import labx_lab_host

    monkeypatch.setattr(labx_lab_host.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(labx_lab_host, "docker_pad", lambda: "/usr/local/bin/docker")
    # Precies de situatie die het oversloeg: docker staat al in ONS pad.
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    monkeypatch.setattr(labx_lab_host.Path, "home", staticmethod(lambda: tmp_path))

    uit = labx_lab_host.zorg_voor_ssh_pad()
    inhoud = (tmp_path / ".zshenv").read_text()
    assert uit, "er had iets geschreven moeten worden"
    assert "/usr/local/bin" in inhoud
    assert "LabX lab-host" in inhoud


def test_de_pad_reparatie_schrijft_niet_twee_keer(tmp_path, monkeypatch):
    """Een agent die elke start een blok aanplakt, laat na een maand een
    .zshenv achter die niemand meer wil openen."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab-host"))
    import labx_lab_host

    monkeypatch.setattr(labx_lab_host.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(labx_lab_host, "docker_pad", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(labx_lab_host.Path, "home", staticmethod(lambda: tmp_path))

    labx_lab_host.zorg_voor_ssh_pad()
    labx_lab_host.zorg_voor_ssh_pad()
    assert (tmp_path / ".zshenv").read_text().count("LabX lab-host") == 2  # begin + eind van één blok


def test_de_pad_reparatie_laat_bestaande_inhoud_staan(tmp_path, monkeypatch):
    """Het is iemands eigen bestand; er staat vaker wel dan niet al iets in."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lab-host"))
    import labx_lab_host

    (tmp_path / ".zshenv").write_text('. "$HOME/.cargo/env"\n')
    monkeypatch.setattr(labx_lab_host.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(labx_lab_host, "docker_pad", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(labx_lab_host.Path, "home", staticmethod(lambda: tmp_path))

    labx_lab_host.zorg_voor_ssh_pad()
    inhoud = (tmp_path / ".zshenv").read_text()
    assert ".cargo/env" in inhoud
    assert "/usr/local/bin" in inhoud


# ── een machine die terugkomt ────────────────────────────────────────────────

def test_een_machine_die_herstart_laat_zijn_labs_startbaar_achter(db):
    """De vraag die hierachter zit: zet ik de andere computers uit en LabX
    aan, herstelt dat dan als ze terugkomen?

    Bij het opstarten slaat LabX een onbereikbare machine over -- terecht,
    want "ik kon er niet bij" is iets anders dan "de container is weg". Maar
    daarna keek niemand meer. Een Mac die 's nachts herstart heeft geen
    labcontainers meer (die starten met --restart no), terwijl LabX nog
    "running" zegt; dan faalt pas je eerste commando.

    Een verdwenen container levert hier `stopped` op en geen `error`: er is
    niets stuk, het volume staat er nog, en starten maakt een nieuwe aan.
    """
    import asyncio

    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")

    p = _lab(db, host_id=host.id)
    p.status = "running"
    p.container_id = "c-van-voor-de-herstart"
    db.commit()

    class LeegNaHerstart:
        async def list_managed(self):
            return []          # de machine is herstart: niets meer over

    lab_svc = LabService(db)
    lab_svc._docker_op_host = lambda h: LeegNaHerstart()   # noqa: ARG005
    aantal = asyncio.run(lab_svc.reconcile_host(host.id))

    db.refresh(p)
    assert aantal == 1
    assert p.status == "stopped", "geen 'error': er is niets stuk"
    assert p.container_id is None, "zodat starten een verse container maakt"
    assert not p.error


def test_een_lab_dat_gewoon_nog_draait_blijft_met_rust(db):
    """Terugkomen is geen reden om iets te veranderen aan wat het nog doet."""
    import asyncio

    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")

    p = _lab(db, host_id=host.id)
    p.status = "running"
    p.container_id = "c-leeft-nog"
    db.commit()

    class NogSteedsDaar:
        async def list_managed(self):
            return [{"id": "c-leeft-nog", "name": "labx-lab-abc", "state": "running"}]

    lab_svc = LabService(db)
    lab_svc._docker_op_host = lambda h: NogSteedsDaar()   # noqa: ARG005
    asyncio.run(lab_svc.reconcile_host(host.id))

    db.refresh(p)
    assert p.status == "running"
    assert p.container_id == "c-leeft-nog"


def test_een_machine_die_nog_steeds_weg_is_verandert_niets(db):
    """Onbereikbaar blijft onbereikbaar: dan weten we nog steeds niets, en
    raden is erger dan wachten."""
    import asyncio

    from services.lab.lab_service import LabService

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")
    p = _lab(db, host_id=host.id)
    p.status = "running"
    p.container_id = "c1"
    db.commit()

    class Weg:
        async def list_managed(self):
            raise RuntimeError("ssh: Host is down")

    lab_svc = LabService(db)
    lab_svc._docker_op_host = lambda h: Weg()   # noqa: ARG005
    assert asyncio.run(lab_svc.reconcile_host(host.id)) == 0
    db.refresh(p)
    assert p.status == "running"


def test_terugkomen_na_offline_zet_het_nakijken_in_gang(db, monkeypatch):
    """De aanmelding zelf mag er niet op wachten -- de agent hangt eraan --
    maar hij moet het wél in gang zetten."""
    from services.lab import hosts as hosts_mod

    svc = _svc(db)
    uit = _meld(db)
    host = svc.lijst()[0]
    svc.koppelen(host.id, code=uit["koppelcode"], docker_host="ssh://nick@werkpc")

    # Hem lang geleden voor het laatst gezien = offline.
    host.laatst_gezien = "2026-10-01T10:00:00+00:00"
    db.commit()

    gezien = []
    monkeypatch.setattr(hosts_mod, "_reconcile_in_achtergrond", gezien.append)
    _meld(db)
    assert gezien == [host.id]

    # En een gewone hartslag van een machine die gewoon online was, niet.
    gezien.clear()
    _meld(db)
    assert gezien == []


def test_de_achtergrondtaak_blijft_leven_tot_hij_klaar_is():
    """Dit deed zich voor als "er gebeurt niets".

    asyncio houdt alleen een ZWAKKE verwijzing naar een taak. Een
    `create_task` waarvan niemand het resultaat vasthoudt, mag halverwege
    worden opgeruimd -- en dat gebeurde: de taak startte, en er kwam nooit een
    succes- én nooit een foutregel. Hij verdween gewoon.
    """
    import asyncio

    from services.lab import hosts as hosts_mod

    gedaan = []

    async def main():
        async def _traag():
            await asyncio.sleep(0.05)
            gedaan.append(True)

        # Dezelfde opzet als in de echte code: taak starten, verwijzing
        # bewaren, en de enige sterke verwijzing weggooien.
        taak = asyncio.get_running_loop().create_task(_traag())
        hosts_mod._LOPEND.add(taak)
        taak.add_done_callback(hosts_mod._LOPEND.discard)
        del taak
        import gc
        gc.collect()
        await asyncio.sleep(0.2)

    asyncio.run(main())
    assert gedaan == [True], "de taak mag niet verdwijnen voordat hij klaar is"
    assert not hosts_mod._LOPEND, "en daarna moet hij zich wel opruimen"
