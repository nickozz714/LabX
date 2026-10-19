"""Het startscript echt draaien, en bijstellen tot het werkt.

Een plan dat er goed uitziet maar waarvan het script omvalt, heeft zijn werk
niet af. Het model schrijft dat script op uit wat het in de repo LAS, en dat
is een gok die pas bij het uitvoeren waar of onwaar wordt. De eerste echte
poging liep vast op één regel:

    cp: cannot stat 'components/app/.env.example': No such file or directory

Dat voorkom je niet met beter nadenken — je ziet het pas als je het draait.

Twee regels die hier vastliggen en die stil kunnen sneuvelen:

- "af" is NIET "exitcode 0". Af is wanneer de gebruiker zegt dat de branch
  goed is. Daarom blijft het proeflab staan, bereikbaar, tot hij bevestigt.
- De lus heeft een plafond. Een lus zonder plafond is een lus die een uur
  later nog bezig is met de verkeerde aanname.
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
        cluster_env_file, cluster_secret, guard_audit, guard_rule, lab, lab_extra, lab_host,
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


def _cluster(db, *, script="#!/bin/sh\nexec node server.js"):
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    c = svc.maak(naam="Hive")
    c.start_script = script
    c.lab_image = "node:22"
    db.commit()
    return c


def _lab(db, c, branch="labx/proefbouw"):
    from models.lab import Lab

    nu = "2026-10-17T10:00:00+00:00"
    p = Lab(id="proef1", name="Hive · proef", image="node:22", volume_name="v",
            status="running", cpu_limit=2, mem_limit_mb=4096, ttl_hours=24,
            cluster_id=c.id, branch=branch, poort_map={"8000": 18009},
            created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    return p


class NepLus:
    """Een proefbouw waarvan alleen de BUITENkant echt is: geen docker, geen
    model. Wat hier getest wordt is de lus zelf."""

    def __init__(self, db, uitslagen, scripts=None, antwoorden=None,
                 logtekst="npm error ENOENT", luistert=None):
        from services.lab.proefbouw import Proefbouw

        self.echt = Proefbouw(db)
        self.uitslagen = list(uitslagen)
        self.scripts = list(scripts or [])
        #: Per poging: antwoordt de poort? Een lijst, want de lus mag meer dan
        #: één keer kijken en dat is juist het interessante geval.
        self.antwoorden = list(antwoorden if antwoorden is not None else [True])
        self.logtekst = logtekst
        #: Wat er IN de container luistert. Leeg = niets; een ander nummer dan
        #: de containerpoort betekent "op de verkeerde poort".
        self.luistert = set(luistert or ())
        self.provisioned = 0
        self.gestart = 0

    def installeer(self, monkeypatch, db, p):
        from services.lab import proefbouw as mod
        from services.lab.lab_service import LabService

        async def nep_provision(zelf, lab_id, *, force=False):
            self.provisioned += 1
            return self.uitslagen.pop(0) if self.uitslagen else {"status": "ok", "steps": []}

        async def nep_herstel(zelf, c, fout, ronde, p=None):
            return self.scripts.pop(0) if self.scripts else {"script": None, "toelichting": "weet ik niet"}

        async def nep_proeflab(zelf, svc, c, branch):
            return p

        async def nep_antwoordt(zelf, svc, c, lab):
            ja = self.antwoorden.pop(0) if self.antwoorden else False
            # De lus kijkt twee keer bij een geslaagde poging (eenmaal om te
            # oordelen, eenmaal voor het adres); de uitkomst blijft dan staan.
            if not self.antwoorden:
                self.antwoorden = [ja]
            return "http://localhost:18009" if ja else None

        async def nep_start(zelf, lab_id):
            self.gestart += 1
            return {"ok": True, "map": "/workspace/repo"}

        async def nep_log(zelf, lab_id, regels=80):
            return self.logtekst

        async def nep_wachten(zelf, lab_svc, lab, *, seconden=120):
            """Er is hier geen container om op te wachten."""
            return None

        async def nep_poorten(zelf, lab_id):
            return set(self.luistert)

        monkeypatch.setattr(LabService, "provision", nep_provision)
        monkeypatch.setattr(LabService, "start_applicatie", nep_start)
        monkeypatch.setattr(LabService, "start_log", nep_log)
        monkeypatch.setattr(LabService, "luisterpoorten", nep_poorten)
        monkeypatch.setattr(mod.Proefbouw, "_herstel", nep_herstel)
        monkeypatch.setattr(mod.Proefbouw, "_proeflab", nep_proeflab)
        monkeypatch.setattr(mod.Proefbouw, "_wacht_op_container", nep_wachten)
        monkeypatch.setattr(mod.Proefbouw, "_antwoordt", nep_antwoordt)
        return self.echt


FAALT = {"status": "error", "steps": [
    {"key": "setup-script", "status": "error",
     "output": "cp: cannot stat 'components/app/.env.example': No such file or directory"}]}
LUKT = {"status": "ok", "steps": [{"key": "setup-script", "status": "ok", "output": ""}]}


# ── de lus ───────────────────────────────────────────────────────────────────

def test_een_script_dat_meteen_werkt_wacht_op_jouw_oordeel(db, monkeypatch):
    c = _cluster(db)
    p = _lab(db, c)
    lus = NepLus(db, [LUKT]).installeer(monkeypatch, db, p)

    uit = asyncio.run(lus.start(c.id))
    assert uit["status"] == "wacht_op_fiat", "niet 'klaar' — dat zeg jij"
    assert uit["adres"] == "http://localhost:18009"
    assert db.get(type(c), c.id).proef_status == "wacht_op_fiat"


def test_een_fout_wordt_bijgesteld_en_opnieuw_geprobeerd(db, monkeypatch):
    c = _cluster(db)
    p = _lab(db, c)
    nieuw = "#!/bin/sh\n[ -f components/app/.env.example ] && cp ...\nexec node server.js"
    lus = NepLus(db, [FAALT, LUKT], [{"script": nieuw, "toelichting": "dat bestand bestaat niet"}],
                 antwoorden=[True])
    lus_obj = lus.installeer(monkeypatch, db, p)

    uit = asyncio.run(lus_obj.start(c.id))
    assert uit["status"] == "wacht_op_fiat" and uit["ronde"] == 2
    assert db.get(type(c), c.id).start_script == nieuw, "het bewezen script wordt bewaard"
    assert lus.provisioned == 2


def test_de_lus_heeft_een_plafond(db, monkeypatch):
    """Zonder plafond is hij een uur later nog bezig met de verkeerde aanname."""
    from services.lab.proefbouw import MAX_RONDES

    c = _cluster(db)
    p = _lab(db, c)
    lus = NepLus(db, [FAALT] * 10, [{"script": "#!/bin/sh\necho x", "toelichting": "t"}] * 10)
    lus_obj = lus.installeer(monkeypatch, db, p)

    uit = asyncio.run(lus_obj.start(c.id))
    assert uit["status"] == "mislukt"
    assert lus.provisioned == MAX_RONDES


def test_elke_ronde_is_terug_te_lezen(db, monkeypatch):
    """Meelezen in plaats van afwachten: wat er draaide, wat er misging, en
    wat er veranderd is."""
    c = _cluster(db)
    p = _lab(db, c)
    lus = NepLus(db, [FAALT, LUKT],
                 [{"script": "#!/bin/sh\necho x", "toelichting": "pad bestaat niet"}],
                 antwoorden=[True])
    asyncio.run(lus.installeer(monkeypatch, db, p).start(c.id))

    regels = db.get(type(c), c.id).proef_log
    assert any(r["fase"] == "bijgesteld" and "bestaat niet" in (r.get("toelichting") or "")
               for r in regels)
    assert any(r["fase"] == "gelukt" for r in regels)


def test_niets_op_de_poort_is_de_fout_en_het_log_is_het_bewijs(db, monkeypatch):
    """Hier zat de oorspronkelijke denkfout.

    Het startscript draaide `exec npm run dev` -- precies wat de prompt vraagt
    -- en kwam dus nooit terug. Na een half uur sloeg de inrichtingsstap om in
    exitcode 124, en die GESLAAGDE uitvoer ging als "fout" naar het model.
    Dat antwoordde terecht: "Ik zie in deze uitvoer geen fout."

    Een draaiende applicatie herken je aan de poort, niet aan een exitcode die
    er nooit komt. En wat er misging staat in het logbestand.
    """
    c = _cluster(db)
    p = _lab(db, c)
    lus = NepLus(db, [LUKT, LUKT, LUKT], antwoorden=[False],
                 logtekst="Error: Cannot find module 'vite'")
    lus_obj = lus.installeer(monkeypatch, db, p)

    uit = asyncio.run(lus_obj.start(c.id))
    assert uit["status"] == "mislukt"
    gemeld = " ".join(str(r.get("fout") or "") for r in db.get(type(c), c.id).proef_log)
    assert "Cannot find module" in gemeld, "het log is het bewijsmateriaal"
    assert "er luistert niets op" in gemeld


def test_het_startscript_is_geen_inrichtingsstap(db, monkeypatch):
    """Inrichten moet EINDIGEN, een applicatie moet juist blijven draaien.
    Allebei als inrichtingsstap behandelen levert een time-out op die eruit
    ziet als een fout terwijl alles werkt."""
    c = _cluster(db)
    p = _lab(db, c)
    lus = NepLus(db, [LUKT])
    asyncio.run(lus.installeer(monkeypatch, db, p).start(c.id))

    assert lus.gestart == 1, "de applicatie wordt apart gestart, op de achtergrond"
    assert not (db.get(type(p), p.id).setup_script or ""), \
        "het startscript hoort niet in het inrichten terecht te komen"


def test_alleen_het_eigen_script_telt_als_fout():
    """Dat de Azure CLI niet installeerde is vervelend, maar het model daarop
    laten repareren is hem een fout laten oplossen die hij niet maakte."""
    from services.lab.proefbouw import Proefbouw

    fout, van_script = Proefbouw._fout_uit({"status": "error", "steps": [
        {"key": "az", "status": "error", "output": "apt faalde"},
        {"key": "setup-script", "status": "ok", "output": ""}]})
    assert fout is None and van_script is True

    fout, van_script = Proefbouw._fout_uit(FAALT)
    assert "cp:" in (fout or "") and van_script is True


# ── het oordeel ──────────────────────────────────────────────────────────────

def test_pas_bij_jouw_fiat_is_het_cluster_klaar(db, monkeypatch):
    from services.lab.proefbouw import Proefbouw

    c = _cluster(db)
    p = _lab(db, c)
    asyncio.run(NepLus(db, [LUKT]).installeer(monkeypatch, db, p).start(c.id))
    assert db.get(type(c), c.id).status != "draait"

    Proefbouw(db).bevestig(c.id)
    ververst = db.get(type(c), c.id)
    assert ververst.proef_status == "goedgekeurd" and ververst.status == "draait"


def test_afkeuren_zonder_te_zeggen_wat_er_mis_is_doet_niets(db):
    from fastapi import HTTPException

    from services.lab.proefbouw import Proefbouw

    c = _cluster(db)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(Proefbouw(db).afkeuren(c.id, opmerking="  "))
    assert fout.value.status_code == 400


def test_bevestigen_kan_niet_zonder_proefbouw(db):
    from fastapi import HTTPException

    from services.lab.proefbouw import Proefbouw

    c = _cluster(db)
    with pytest.raises(HTTPException):
        Proefbouw(db).bevestig(c.id)


def test_zonder_startscript_valt_er_niets_te_proberen(db):
    from fastapi import HTTPException

    from services.lab.proefbouw import Proefbouw

    c = _cluster(db, script=None)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(Proefbouw(db).start(c.id))
    assert fout.value.status_code == 400


# ── geduld, en weten wanneer je het kunt opgeven ─────────────────────────────

def test_er_wordt_lang_genoeg_gewacht_voor_een_bouw():
    """Dit stond op 45 seconden, en dat was de denkfout: het startscript
    INSTALLEERT ook. Bij een kleine React-app duurde `npm install` alleen al
    twintig seconden; de proefbouw gaf het op terwijl de applicatie een halve
    minuut later gewoon een HTTP 200 gaf."""
    from services.lab.proefbouw import GEREED_SECONDEN

    assert GEREED_SECONDEN >= 600, "een monorepo bouwt niet in een minuut"


def test_een_gevallen_script_hoeft_de_wachttijd_niet_uit_te_zitten(db, monkeypatch):
    """Lang wachten kost niets zolang we merken dat het script gestopt is.
    Anders kijkt de gebruiker een kwartier naar iets dat al gevallen was."""
    import time

    from services.lab import proefbouw as mod

    c = _cluster(db)
    p = _lab(db, c)

    from services.lab.lab_service import LabService

    async def nep_poorten(zelf, lab_id):
        return set()

    async def nep_leeft(zelf, lab):
        return False

    monkeypatch.setattr(LabService, "luisterpoorten", nep_poorten)
    monkeypatch.setattr(mod.Proefbouw, "_leeft", nep_leeft)
    monkeypatch.setattr(mod, "LEEFT_ELKE", 0)

    begin = time.monotonic()
    uit = asyncio.run(mod.Proefbouw(db)._antwoordt(None, c, p, geduld=900))
    assert uit is None
    assert time.monotonic() - begin < 20, "niet 900 seconden blijven wachten"


def test_het_model_ziet_de_variabelen_die_labx_zelf_zet(db):
    """Zonder deze concludeerde het model "LABX_POORT is niet gezet" en ging
    het een workaround schrijven voor een probleem dat niet bestond — die
    variabele stond er gewoon."""
    from services.lab.proefbouw import Proefbouw

    c = _cluster(db)
    p = _lab(db, c)
    c.proef_lab_id = p.id
    db.commit()

    omgeving = Proefbouw(db)._labx_omgeving(c)
    assert omgeving["LABX_POORT"] == "8000"
    assert omgeving["LABX_LAB_POORT"] == "18009"


def test_een_verkeerde_poort_wordt_als_zodanig_benoemd(db, monkeypatch):
    """"Er antwoordt niets" is geen diagnose.

    De applicatie kan prima draaien en op de VERKEERDE poort luisteren — dat
    gebeurde: het model liet Vite op 18002 luisteren, het nummer op de
    machine, terwijl de container 8099 publiceert. Van buiten ziet dat er
    identiek uit als een applicatie die niet start, en dan gaat het model op
    de verkeerde plek zoeken.
    """
    c = _cluster(db)
    p = _lab(db, c)  # poort_map {"8000": 18009}
    lus = NepLus(db, [LUKT] * 3, antwoorden=[False], luistert={18009})
    lus_obj = lus.installeer(monkeypatch, db, p)

    asyncio.run(lus_obj.start(c.id))

    gemeld = " ".join(str(r.get("fout") or "") for r in db.get(type(c), c.id).proef_log)
    assert "verkeerde poort" in gemeld
    assert "$LABX_POORT (8000)" in gemeld
    assert "18009" in gemeld, "zeg ook waar hij WEL luistert"


def test_zonder_iets_dat_luistert_blijft_het_een_gewone_mislukking(db, monkeypatch):
    c = _cluster(db)
    p = _lab(db, c)
    lus = NepLus(db, [LUKT] * 3, antwoorden=[False], luistert=set(),
                 logtekst="Error: Cannot find module 'vite'")
    asyncio.run(lus.installeer(monkeypatch, db, p).start(c.id))

    gemeld = " ".join(str(r.get("fout") or "") for r in db.get(type(c), c.id).proef_log)
    assert "verkeerde poort" not in gemeld
    assert "Cannot find module" in gemeld


def test_poorten_worden_uit_proc_gelezen_en_niet_uit_netstat():
    """`ss` en `netstat` zitten niet in elk image. Een diagnose die afhangt
    van geïnstalleerd gereedschap is er geen."""
    import inspect

    from services.lab.lab_service import LabService

    bron = inspect.getsource(LabService.luisterpoorten)
    # Het COMMANDO telt, niet de uitleg eromheen — die noemt netstat juist om
    # te zeggen waarom we het niet gebruiken.
    opdracht = bron.split('["sh", "-c",', 1)[1].split("]", 1)[0]
    assert "/proc/net/tcp" in opdracht
    assert "netstat" not in opdracht and "ss " not in opdracht


def test_een_vluchtige_poort_telt_niet_als_verkeerde_poort(db, monkeypatch):
    """In de proefbouw bleef 41361 opduiken — een nummer dat het
    besturingssysteem uitdeelt, niet een dat iemand gekozen heeft. Het werd
    gemeld als "hij luistert op het verkeerde nummer" terwijl er helemaal geen
    applicatie luisterde, en dat stuurt het model de verkeerde kant op."""
    c = _cluster(db)
    p = _lab(db, c)
    lus = NepLus(db, [LUKT] * 3, antwoorden=[False], luistert={41361},
                 logtekst="Error: Cannot find module 'vite'")
    asyncio.run(lus.installeer(monkeypatch, db, p).start(c.id))

    gemeld = " ".join(str(r.get("fout") or "") for r in db.get(type(c), c.id).proef_log)
    assert "verkeerde poort" not in gemeld
    assert "Cannot find module" in gemeld


def test_de_vorige_start_wordt_opgeruimd():
    """Zonder dit blijft bij elke ronde een devserver staan, en kreeg de
    tweede poging "Port 8099 is already in use" — een fout over onze eigen
    rommel, die het model netjes ging proberen op te lossen."""
    import inspect

    from services.lab.lab_service import LabService

    from services.lab.lab_service import _LOPER_SCRIPT

    bron = inspect.getsource(LabService.start_applicatie)
    assert "kill -TERM -$(cat" in bron, "de hele procesgroep, niet alleen het script"
    assert "kill -KILL -$(cat" in bron, "en doorpakken als TERM niet genoeg is"
    # Het groepsnummer komt uit het proces ZELF: `$$` onder setsid is de
    # groep. `$!` zou de setsid-wrapper opleveren, en die is al weg tegen de
    # tijd dat we hem willen stoppen.
    assert "echo $$ > .labx-start.pid" in _LOPER_SCRIPT


def test_stdin_blijft_open_voor_de_applicatie():
    """`< /dev/null` lijkt netjes en is het niet.

    Een devserver als Vite luistert op stdin naar toetsen ('r' om te
    herladen) en sluit zichzelf af zodra stdin EOF geeft. Dat gebeurde:
    "ready in 102 ms" en meteen daarna weg, zonder een enkele foutmelding —
    het onaangenaamste soort storing, want alles lijkt goed te gaan.
    """
    import inspect

    from services.lab.lab_service import LabService

    from services.lab.lab_service import _LOPER_SCRIPT

    # De pijp blijft open doordat een `sleep` hem voor schrijven openhoudt.
    assert "mkfifo" in _LOPER_SCRIPT
    assert "sleep 2147483647 > .labx-stdin &" in _LOPER_SCRIPT
    assert "< .labx-stdin" in _LOPER_SCRIPT
    # `2>/dev/null` mag; dat is stderr. Het gaat om STDIN.
    assert "< /dev/null" not in _LOPER_SCRIPT and "0</dev/null" not in _LOPER_SCRIPT

    # En die sleep is een KIND van de loper, dus hij zit in dezelfde
    # procesgroep en gaat vanzelf mee. Een losse `tail -f /dev/null` buiten de
    # groep moest met `pkill -f` opgeruimd worden -- en dat patroon matchte
    # ook de shell die het startcommando uitvoerde, want die had die tekst in
    # zijn eigen commandoregel staan. Die maakte dus zichzelf dood.
    opdracht = inspect.getsource(LabService.start_applicatie)
    assert "pkill" not in opdracht


def test_het_model_krijgt_genoeg_tijd_om_een_script_te_herschrijven():
    """Op 300 seconden sneuvelde een hele ronde aan de tijdslimiet, en dan is
    de rest van die poging ook weg."""
    import inspect

    from services.lab.proefbouw import Proefbouw

    bron = inspect.getsource(Proefbouw._herstel)
    assert "timeout=900.0" in bron


def test_de_poort_wordt_in_het_lab_gecontroleerd_en_niet_erbuiten():
    """De hardnekkigste fout van allemaal.

    De controle maakte een TCP-verbinding naar 127.0.0.1:<machinepoort>, maar
    LabX draait zelf in een container — en daar is die poort niet; hij staat
    op de loopback van de HOST. Dus faalde elke proefbouw op "er antwoordt
    niets", terwijl een curl vanaf de host al rondenlang HTTP 200 gaf.

    Binnen het lab kijken werkt bovendien ook als het lab op een andere
    machine draait, waar we sowieso niet bij de loopback kunnen.
    """
    import inspect

    from services.lab.proefbouw import Proefbouw

    bron = inspect.getsource(Proefbouw._antwoordt)
    assert "luisterpoorten" in bron, "de vraag hoort in het lab gesteld te worden"
    assert "open_connection" not in bron
    assert not hasattr(Proefbouw, "_open"), "de verbinding van buitenaf is weg"


# ── een inrichtfout is geen scriptfout ───────────────────────────────────────

def test_een_mislukte_inrichtstap_levert_de_echte_uitvoer_op():
    """Hier stond eerst een zin van mij — "Het inrichten is mislukt vóór het
    startscript" — en dat is precies wat je niet kunt gebruiken: geen
    commando, geen foutregel, niets om op te handelen. Het model antwoordde
    dan ook, volkomen terecht, dat het er niets uit kon afleiden."""
    from services.lab.proefbouw import Proefbouw

    fout, van_script = Proefbouw._fout_uit({"status": "error", "steps": [
        {"key": "base-tools", "label": "bash, curl, jq", "status": "error",
         "exit_code": 100, "output": "E: Unable to locate package jq"}]})
    assert van_script is False, "hier helpt een ander startscript niet tegen"
    assert "Unable to locate package jq" in fout
    assert "exitcode 100" in fout
    assert "bash, curl, jq" in fout


def test_te_vroeg_kijken_is_geen_mislukking():
    """`provision()` keert meteen terug met "Lab draait niet" als de container
    nog aan het opkomen is. Dat las ik als een mislukking, en dan kreeg het
    model een zin zonder enige uitvoer."""
    from services.lab.proefbouw import Proefbouw

    fout, van_script = Proefbouw._fout_uit({"ok": False, "status": "error",
                                            "reason": "Lab draait niet"})
    assert van_script is False
    assert "Lab draait niet" in fout


def test_een_inrichtfout_gaat_niet_naar_het_model(db, monkeypatch):
    """Een mislukte apt of een lab dat niet opkomt is met geen enkel
    startscript te repareren. Hem dat toch voorleggen kost een ronde waarin
    hij terecht om de echte uitvoer vraagt."""
    from services.lab import proefbouw as mod

    c = _cluster(db)
    p = _lab(db, c)
    kapot = {"status": "error", "steps": [
        {"key": "base-tools", "label": "bash", "status": "error",
         "exit_code": 100, "output": "E: Unable to locate package jq"}]}
    lus = NepLus(db, [kapot])
    lus_obj = lus.installeer(monkeypatch, db, p)

    gevraagd = {"n": 0}

    async def tel_herstel(zelf, cl, fout, ronde):
        gevraagd["n"] += 1
        return {"script": None, "toelichting": "t"}

    monkeypatch.setattr(mod.Proefbouw, "_herstel", tel_herstel)

    uit = asyncio.run(lus_obj.start(c.id))
    assert uit["status"] == "mislukt"
    assert gevraagd["n"] == 0, "het model is hier niet aan zet"
    regels = db.get(type(c), c.id).proef_log
    assert any(r["fase"] == "inrichten mislukt" for r in regels)
    assert any("Unable to locate package jq" in str(r.get("fout") or "") for r in regels)


def test_de_eerste_poort_telt_en_niet_de_laagste(db):
    """De volgorde komt van de detector, die het hoofd-Dockerfile vooraan
    zet. Bij IntelligentHive is dat [8080, 3000, 80]; numeriek sorteren koos
    80 — de nginx-poort uit het productie-image — en dan wacht LabX een
    kwartier op een applicatie die daar nooit komt."""
    from services.lab.proefbouw import Proefbouw

    class NepLab:
        poort_map = {"8080": 18004, "3000": 18005, "80": 18006}

    assert Proefbouw._containerpoort(NepLab()) == 8080


def test_ook_de_omgeving_krijgt_de_eerste_poort(db):
    from services.lab.clusters import ClusterService

    c = ClusterService(db).maak(naam="Hive")
    omgeving = ClusterService._adres_omgeving(c, {8080: 18004, 3000: 18005, 80: 18006})
    assert omgeving["LABX_POORT"] == "8080"
    assert omgeving["LABX_LAB_POORT"] == "18004"


def test_alle_poorten_moeten_antwoorden(db, monkeypatch):
    """Bij IntelligentHive draaide de front-end op 5173 en viel de API op
    7071 om met "Missing API_KEY". Ik keek naar de eerste, kreeg een 200, en
    noemde het geslaagd. Een cluster waarvan de helft dood is, is niet
    klaar — en het ergste is dat het er geslaagd uitziet.
    """
    from models.lab import Lab

    c = _cluster(db)
    nu = "2026-10-17T10:00:00+00:00"
    p = Lab(id="proef2", name="Hive · proef", image="node:22", volume_name="v",
            status="running", cpu_limit=2, mem_limit_mb=4096, ttl_hours=24,
            cluster_id=c.id, branch="labx/proefbouw",
            poort_map={"5173": 18004, "7071": 18005}, created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()

    # Alleen de front-end luistert.
    lus = NepLus(db, [LUKT] * 3, antwoorden=[False], luistert={5173},
                 logtekst="Error: Missing API_KEY environment variable.")
    asyncio.run(lus.installeer(monkeypatch, db, p).start(c.id))

    gemeld = " ".join(str(r.get("fout") or "") for r in db.get(type(c), c.id).proef_log)
    assert "5173" in gemeld and "7071" in gemeld
    assert "Missing API_KEY" in gemeld
    assert db.get(type(c), c.id).proef_status == "mislukt", "half is niet klaar"


def test_het_model_hoort_dat_er_al_een_azure_sessie_is(db):
    """Zonder deze regel schreef het model een script dat zelf inlogt met een
    service principal — en omdat AZURE_CLIENT_ID/SECRET er niet zijn, sloeg
    het de hele geheimen-stap over. De front-end draaide, de API viel om op
    "Missing API_KEY", terwijl `az keyvault secret list` in dat lab gewoon
    21 geheimen opsomt."""
    from services.lab.verkenner import _prompt

    prompt = _prompt(repo_url="x", branch="m", beeld={"boom": []}, inhoud={},
                     vondsten=[], azure_sessie="nick@beeminds.nl")
    assert "ER IS AL EEN AZURE-AANMELDING" in prompt
    assert "nick@beeminds.nl" in prompt
    assert "service principal" in prompt


def test_zonder_profiel_staat_er_niets_over_azure(db):
    from services.lab.verkenner import _prompt

    prompt = _prompt(repo_url="x", branch="m", beeld={"boom": []}, inhoud={}, vondsten=[])
    assert "AZURE-AANMELDING" not in prompt


def test_de_foutregels_worden_apart_opgediept(db, monkeypatch):
    """Eén logbestand, twee processen: een devserver die doorpraat en een API
    die omvalt. De staart is dan gevuld met yarn- en Vite-ruis en de crash is
    allang weggescrold. Het model kreeg netjes de laatste tachtig regels — en
    daar stond niets in."""
    from models.lab import Lab
    from services.lab.lab_service import LabService

    c = _cluster(db)
    nu = "2026-10-18T10:00:00+00:00"
    p = Lab(id="proef3", name="x", image="node:22", volume_name="v", status="running",
            cpu_limit=2, mem_limit_mb=4096, ttl_hours=24, cluster_id=c.id,
            branch="labx/proefbouw", poort_map={"5173": 18004, "7071": 18005},
            created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()

    async def nep_foutregels(zelf, lab_id, regels=40):
        return "Error: Cosmos gaf 404 — database bestaat niet"

    lus = NepLus(db, [LUKT] * 3, antwoorden=[False], luistert={5173},
                 logtekst="YN0060: react is listed by your project with version 19.2.4")
    lus_obj = lus.installeer(monkeypatch, db, p)
    monkeypatch.setattr(LabService, "foutregels", nep_foutregels)

    asyncio.run(lus_obj.start(c.id))
    gemeld = " ".join(str(r.get("fout") or "") for r in db.get(type(c), c.id).proef_log)
    assert "REGELS DIE OP EEN FOUT LIJKEN" in gemeld
    assert "database bestaat niet" in gemeld
    assert "react is listed" in gemeld, "de gewone staart blijft er ook bij"


def test_het_model_krijgt_de_bestandslijst(db):
    """Zonder dit moet het model repareren zonder te weten wat er in de repo
    staat — en dat zei het ook: "ik kan dit niet betrouwbaar repareren, want
    ik kon de repo niet lezen"."""
    from models.cluster import ClusterPlan
    from services.lab.proefbouw import Proefbouw

    c = _cluster(db)
    nu = "2026-10-18T10:00:00+00:00"
    db.add(ClusterPlan(cluster_id=c.id, repo_url="x", branch="main", status="goedgekeurd",
                       plan={"boom": ["deploy/pr/seed-emulator.mjs", "package.json"]},
                       keuzes={}, created_at=nu, updated_at=nu))
    db.commit()

    boom = Proefbouw(db)._boom(c)
    assert "seed-emulator.mjs" in boom


# ── bijvragen: paden zijn geen inhoud ────────────────────────────────────────
#
# Het model kreeg wel de PADEN uit de repo mee, maar niet wat erin stond, en
# zei dat ook met zoveel woorden: "Ik heb geen bestanden uit de repo kunnen
# lezen, dus het is niet gecontroleerd of database-setup.ts de containers
# aanmaakt." Het noemde het juiste bestand en moest vervolgens gokken wat
# erin stond. Het proeflab heeft de repo uitgecheckt staan, dus die vraag is
# te beantwoorden -- één keer per ronde.


class _NepProvider:
    """Antwoordt met wat je erin stopt en onthoudt wat het gevraagd kreeg."""

    def __init__(self, antwoorden):
        self.antwoorden = list(antwoorden)
        self.prompts = []

    async def chat(self, prompt, **_):
        self.prompts.append(prompt)

        class _Uit:
            text = self.antwoorden.pop(0)

        return _Uit()


def _zet_nep_provider(monkeypatch, provider):
    """Geen echte CLI: `_herstel` haalt de provider en de instellingen pas op
    als hij ze nodig heeft, dus allebei op moduleniveau vervangen."""
    import services.agent.claude_cli_provider as cli
    import services.settings_service as ss

    monkeypatch.setattr(cli, "ClaudeCliProvider", lambda *a, **k: provider)
    monkeypatch.setattr(ss, "get_settings", lambda _db: type(
        "S", (), {"oauth_token": "t", "cli_path": "claude"})())


def test_het_model_mag_een_bestand_opvragen_en_krijgt_de_inhoud(db, monkeypatch):
    """Vraagt het om bestanden, dan gaan die inhoud én de oude prompt mee
    terug -- anders beantwoordt het de tweede keer een andere vraag."""
    from services.lab.proefbouw import Proefbouw

    c = _cluster(db)
    p = _lab(db, c)
    provider = _NepProvider([
        '{"bestanden": ["utilities/database-setup.ts"]}',
        '{"toelichting": "containers aanmaken", "startscript": "#!/bin/sh\\nexec node x"}',
    ])
    _zet_nep_provider(monkeypatch, provider)

    pb = Proefbouw(db)

    async def nep_lezen(_p, paden):
        assert paden == ["utilities/database-setup.ts"]
        return "=== utilities/database-setup.ts\nenum Container { CATALOG }\n"

    monkeypatch.setattr(pb, "_lees_uit_lab", nep_lezen)

    uit = asyncio.run(pb._herstel(c, "7071 luistert niet", 1, p))

    assert uit["script"] == "#!/bin/sh\nexec node x"
    assert len(provider.prompts) == 2, "er had een tweede vraag moeten komen"
    assert "enum Container { CATALOG }" in provider.prompts[1]
    assert "7071 luistert niet" in provider.prompts[1], "de fout moet mee terug"


def test_bijvragen_mag_maar_een_keer(db, monkeypatch):
    """Anders kan een model de hele ronde opsouperen met bladeren, en komt er
    nooit een script."""
    from services.lab.proefbouw import Proefbouw

    c = _cluster(db)
    p = _lab(db, c)
    provider = _NepProvider(['{"bestanden": ["a.ts"]}', '{"bestanden": ["b.ts"]}'])
    _zet_nep_provider(monkeypatch, provider)

    pb = Proefbouw(db)
    monkeypatch.setattr(pb, "_lees_uit_lab", lambda _p, _q: _klaar("=== a.ts\nx\n"))

    uit = asyncio.run(pb._herstel(c, "stuk", 1, p))

    assert uit["script"] is None
    assert len(provider.prompts) == 2, "precies één keer bijvragen, niet meer"


async def _klaar(waarde):
    return waarde


def test_een_opgevraagd_pad_komt_nooit_in_een_commando_terecht(db, monkeypatch):
    """De paden komen van het model. Ze gaan via STDIN naar een lezertje in de
    container; een pad met aanhalingstekens erin hoort een onleesbaar bestand
    op te leveren, geen commando."""
    from services.lab.proefbouw import Proefbouw, _LEZER

    c = _cluster(db)
    p = _lab(db, c)
    gezien = {}

    class _RT:
        async def exec(self, cid, cmd, **kw):
            gezien["cmd"] = cmd
            gezien["stdin"] = kw.get("stdin")
            return {"output": "=== ok"}

    from services.lab.lab_service import LabService
    monkeypatch.setattr(LabService, "runtime_voor", lambda self, _p: _RT())
    monkeypatch.setattr(LabService, "ensure_workers",
                        lambda self, _p: [type("W", (), {"container_id": "c1"})()])

    stout = 'x.ts"; rm -rf /; echo "'
    asyncio.run(Proefbouw(db)._lees_uit_lab(p, [stout]))

    assert stout not in " ".join(gezien["cmd"]), "pad mag niet in het commando staan"
    assert gezien["cmd"][2] == _LEZER
    assert stout.encode() in gezien["stdin"], "het pad hoort via stdin te gaan"


def test_bijvragen_weigert_buiten_de_repo():
    """\"Lees een bestand\" mag niet stilletjes \"lees het bestandssysteem\"
    worden: absolute paden en .. gaan er in het lezertje uit."""
    from services.lab.proefbouw import _LEZER

    assert "/*|*..*" in _LEZER
    assert "geweigerd" in _LEZER


def test_een_hergebruikt_proeflab_krijgt_de_huidige_omgeving(db, monkeypatch):
    """Containeromgeving wordt alleen bij het AANMAKEN gezet.

    Een bestaand proeflab werd hergebruikt met een vroege return, dus het
    bleef praten tegen het adres van toen: COSMOS_ENDPOINT was op het cluster
    allang gecorrigeerd en het lab probeerde rondenlang `https://ih-cosmos:8081`
    -- een naam en een protocol die allebei niet meer klopten. Niets in het
    log wees daarop; het leek een fout in het startscript.
    """
    import asyncio as _a

    from services.lab.clusters import ClusterService
    from services.lab.proefbouw import Proefbouw

    c = _cluster(db)
    p = _lab(db, c)
    c.proef_lab_id = p.id
    db.commit()

    opnieuw = []

    async def nep_start(zelf, lab_id):
        opnieuw.append(lab_id)
        return {"ok": True}

    monkeypatch.setattr(ClusterService, "start_branch_lab", nep_start)

    svc = ClusterService(db)
    uit = _a.run(Proefbouw(db)._proeflab(svc, c, "labx/proefbouw"))

    assert uit.id == p.id, "hetzelfde lab, dus de werkkopie blijft staan"
    assert opnieuw == [p.id], "de container hoort opnieuw te worden gemaakt"


# ── welke regels uit het log als fout gelden ─────────────────────────────────

_LOGFRAGMENT = [
    ("➤ YN0060: │ react is listed by your project with version 19.2.4 (p81185), "
     "which doesn't satisfy what react-diff-viewer-continued and other "
     "dependencies request (but they have non-overlapping ranges!).", False),
    ("➤ YN0002: │ @beeminds/openai@workspace:packages/openai doesn't provide "
     "openai (p60569), requested by @langchain/community.", False),
    ("➤ YN0086: │ Some peer dependencies are incorrectly met by your project", False),
    ("  VITE v7.3.1  ready in 146 ms", False),
    ("💥 API is exiting on unhandledRejection:", True),
    ("    at httpRequest (/workspace/x/node_modules/@azure/cosmos/RequestHandler.js:115:31)", True),
    ("Error: connect ECONNREFUSED 10.0.0.1:8081", True),
    ("[lab] seeden mislukt", True),
]


def test_het_foutpatroon_vangt_de_crash_en_niet_de_waarschuwingen():
    """Twee keer stil misgegaan, en allebei de keren kostte dat een ronde.

    `at [A-Za-z]` stond er voor JS-stackframes maar zonder anker, dus de
    yarn-regel "...satisfy what react-diff-viewer..." kwam als fout binnen.
    En de enige regel die telde -- "API is exiting on unhandledRejection" --
    matchte op geen enkel woord. Het model kreeg een lijst waarschuwingen
    zonder de crash die ertoe deed.
    """
    import re

    from services.lab.lab_service import FOUTPATROON

    # [[:space:]] is POSIX; Python kent het niet. De shell draait grep -E,
    # dus voor de test vertalen we die ene klasse.
    patroon = re.compile(FOUTPATROON.replace("[[:space:]]", r"\s"),
                         re.IGNORECASE | re.MULTILINE)

    for regel, hoort_erbij in _LOGFRAGMENT:
        raak = bool(patroon.search(regel))
        assert raak == hoort_erbij, (
            f"{'had moeten matchen' if hoort_erbij else 'is ruis'}: {regel[:80]}")


def test_de_scripttaal_van_het_model_telt_mee():
    """De scripts die het model schrijft loggen in het Nederlands; "[lab]
    seeden mislukt" is vaak de enige regel die zegt wat er gebeurde."""
    from services.lab.lab_service import FOUTPATROON

    assert "mislukt" in FOUTPATROON


def test_twee_inrichtingen_tegelijk_wachten_op_elkaar(db, monkeypatch):
    """`start_branch_lab` zet het inrichten als ACHTERGRONDtaak weg en keert
    meteen terug; daarna doet de proefbouw zijn eigen `provision()`. Dat zijn
    twee apt-processen in dezelfde container, en dan krijg je

        Could not get lock /var/lib/apt/lists/lock. It is held by process N

    Die melding kostte een hele proefbouw om te herkennen, want hij ziet
    eruit als een kapot image.
    """
    import asyncio as _a

    from services.lab import lab_service as mod

    c = _cluster(db)
    p = _lab(db, c)
    tegelijk, hoogste = {"n": 0}, {"n": 0}

    async def nep(zelf, lab_id, *, force=False):
        tegelijk["n"] += 1
        hoogste["n"] = max(hoogste["n"], tegelijk["n"])
        await _a.sleep(0.05)
        tegelijk["n"] -= 1
        return {"ok": True, "status": "ok", "steps": []}

    monkeypatch.setattr(mod.LabService, "_provision", nep)
    mod._PROVISION_SLOTEN.clear()

    svc = mod.LabService(db)

    async def allebei():
        await _a.gather(svc.provision(p.id), svc.provision(p.id), svc.provision(p.id))

    _a.run(allebei())
    assert hoogste["n"] == 1, f"er liepen er {hoogste['n']} tegelijk in hetzelfde lab"


def test_het_slot_geldt_per_lab_en_niet_globaal(db, monkeypatch):
    """Twee verschillende labs hebben elk hun eigen container en dus hun eigen
    apt-staat. Die op elkaar laten wachten maakt het inrichten onnodig traag."""
    import asyncio as _a

    from models.lab import Lab
    from services.lab import lab_service as mod

    c = _cluster(db)
    p1 = _lab(db, c)
    nu = "2026-10-19T10:00:00+00:00"
    p2 = Lab(id="proef2", name="tweede", image="node:22", volume_name="v2",
             status="running", cpu_limit=2, mem_limit_mb=4096, ttl_hours=24,
             cluster_id=c.id, branch="labx/tweede", poort_map={"8000": 18010},
             created_at=nu, updated_at=nu)
    db.add(p2)
    db.commit()

    tegelijk, hoogste = {"n": 0}, {"n": 0}

    async def nep(zelf, lab_id, *, force=False):
        tegelijk["n"] += 1
        hoogste["n"] = max(hoogste["n"], tegelijk["n"])
        await _a.sleep(0.05)
        tegelijk["n"] -= 1
        return {"ok": True, "status": "ok", "steps": []}

    monkeypatch.setattr(mod.LabService, "_provision", nep)
    mod._PROVISION_SLOTEN.clear()
    svc = mod.LabService(db)

    async def allebei():
        await _a.gather(svc.provision(p1.id), svc.provision(p2.id))

    _a.run(allebei())
    assert hoogste["n"] == 2, "verschillende labs horen niet op elkaar te wachten"
