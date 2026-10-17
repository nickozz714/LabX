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
        cluster_secret, guard_audit, guard_rule, lab, lab_extra, lab_host,
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

    def __init__(self, db, uitslagen, scripts=None, antwoordt=True):
        from services.lab.proefbouw import Proefbouw

        self.echt = Proefbouw(db)
        self.uitslagen = list(uitslagen)
        self.scripts = list(scripts or [])
        self.antwoordt = antwoordt
        self.provisioned = 0

    def installeer(self, monkeypatch, db, p):
        from services.lab import proefbouw as mod
        from services.lab.lab_service import LabService

        async def nep_provision(zelf, lab_id, *, force=False):
            self.provisioned += 1
            return self.uitslagen.pop(0) if self.uitslagen else {"status": "ok", "steps": []}

        async def nep_herstel(zelf, c, fout, ronde):
            return self.scripts.pop(0) if self.scripts else {"script": None, "toelichting": "weet ik niet"}

        async def nep_proeflab(zelf, svc, c, branch):
            return p

        async def nep_antwoordt(zelf, svc, c, lab):
            return "http://localhost:18009" if self.antwoordt else None

        monkeypatch.setattr(LabService, "provision", nep_provision)
        monkeypatch.setattr(mod.Proefbouw, "_herstel", nep_herstel)
        monkeypatch.setattr(mod.Proefbouw, "_proeflab", nep_proeflab)
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
    lus = NepLus(db, [FAALT, LUKT],
                 [{"script": nieuw, "toelichting": "dat bestand bestaat niet"}])
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
                 [{"script": "#!/bin/sh\necho x", "toelichting": "pad bestaat niet"}])
    asyncio.run(lus.installeer(monkeypatch, db, p).start(c.id))

    regels = db.get(type(c), c.id).proef_log
    assert any(r["fase"] == "bijgesteld" and "bestaat niet" in (r.get("toelichting") or "")
               for r in regels)
    assert any(r["fase"] == "gelukt" for r in regels)


def test_een_script_dat_klaar_is_maar_niets_laat_luisteren_telt_als_gelukt_zonder_adres(db, monkeypatch):
    """Een script dat klaar is en een applicatie die antwoordt zijn twee
    dingen. Geen adres is geen fout, maar je hoort het wel te zien."""
    c = _cluster(db)
    p = _lab(db, c)
    lus = NepLus(db, [LUKT], antwoordt=False).installeer(monkeypatch, db, p)

    uit = asyncio.run(lus.start(c.id))
    assert uit["status"] == "wacht_op_fiat" and uit["adres"] is None


def test_alleen_het_eigen_script_telt_als_fout():
    """Dat de Azure CLI niet installeerde is vervelend, maar het model daarop
    laten repareren is hem een fout laten oplossen die hij niet maakte."""
    from services.lab.proefbouw import Proefbouw

    assert Proefbouw._fout_uit({"status": "error", "steps": [
        {"key": "az", "status": "error", "output": "apt faalde"},
        {"key": "setup-script", "status": "ok", "output": ""}]}) is None
    assert "cp:" in (Proefbouw._fout_uit(FAALT) or "")


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
