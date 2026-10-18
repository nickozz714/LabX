"""Env-bestanden die bij een cluster horen.

De bestanden die een applicatie lokaal laten werken staan vaak in .gitignore
en nergens anders. `components/app/.env.example` van IntelligentHive zegt
letterlijk "ask these values from another developer", en de start-dev-skill
van die repo kopieert `.env.local` uit een bestaande hoofdcheckout. Een verse
kloon -- en dus elk branch-lab -- kan daar niet bij.

Het cluster is de hoofdcheckout geworden: importeer ze daar één keer, en elk
lab eronder krijgt ze bij het starten.

Twee dingen liggen hier vast die stil kunnen sneuvelen:

- De WAARDEN komen nooit uit een lees-API. Het scherm hoort te zien wat er
  staat (pad, namen van de sleutels) zonder dat iemand de waarden kan
  aflezen.
- Het pad blijft binnen de werkkopie. Zonder die controle is "importeer een
  env-bestand" ook "schrijf een willekeurig bestand in de container", en het
  pad komt uit een formulier.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

INHOUD = """# van de MacBook Pro
VITE_TENANT_ID=7048a888-8a1d-4248-bb6d-3926e8dc3e3d
export VITE_LOCIZE_API_KEY="zeer-geheim-$(whoami)`id`"

VITE_CHAT_ENABLED=false
"""


@pytest.fixture()
def db(monkeypatch):
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.database import Base
    from models import (  # noqa: F401
        audit, azure_profile, background_run, board, claim_resource, cluster,
        cluster_env_file, cluster_secret, guard_audit, guard_rule, lab,
        lab_extra, lab_host, lab_secret, lab_worker, mcp_server, message,
        model_runner, notification, plan, schedule, secret, skill, skill_tool,
        thread, time_entry, tool, voice, workflow,
    )

    # Een ECHTE sleutel: deze test versleutelt en ontsleutelt werkelijk, want
    # dat is de helft van wat hier getest wordt.
    #
    # De env-variabele alleen zetten helpt niet: `config.settings` is bij het
    # importeren al gelezen en `utils.crypto._fernet` is lru_cached. Dus het
    # veld zelf zetten en de cache leeggooien -- anders valt de helft van deze
    # tests om op "LABX_FERNET_KEY is niet gezet", precies zoals de oudere
    # kluis-tests dat al doen.
    import utils.crypto as crypto
    from config import settings as _settings

    monkeypatch.setattr(_settings, "FERNET_KEY", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _cluster(db):
    from models.cluster import Cluster

    nu = "2026-10-18T10:00:00+00:00"
    c = Cluster(naam="Hive", netwerk="labx-cluster-hive", repo_url="https://x/Intelligenthive.git",
                created_at=nu, updated_at=nu)
    db.add(c)
    db.commit()
    return c


def test_de_waarden_komen_nooit_uit_de_lees_api(db):
    """Het scherm mag zien WAT er staat, niet wat de waarden zijn."""
    from services.lab.cluster_env import ClusterEnvBestanden

    c = _cluster(db)
    svc = ClusterEnvBestanden(db)
    svc.zet(c.id, "components/app/.env.local", INHOUD)

    uit = svc.lijst(c.id)
    tekst = repr(uit)

    assert "zeer-geheim" not in tekst, "de waarde lekt uit de lijst"
    assert "7048a888-8a1d-4248-bb6d-3926e8dc3e3d" not in tekst
    assert uit[0]["sleutels"] == ["VITE_TENANT_ID", "VITE_LOCIZE_API_KEY", "VITE_CHAT_ENABLED"]
    assert uit[0]["aantal"] == 3
    assert uit[0]["pad"] == "components/app/.env.local"


def test_export_en_commentaar_worden_goed_gelezen(db):
    """`export FOO=` is een sleutel, `# FOO=` niet."""
    from services.lab.cluster_env import _sleutelnamen

    namen = _sleutelnamen("# UIT=1\nA=1\nexport B=2\n\n  C =3\nniet-een-sleutel\n")
    assert namen == ["A", "B", "C"]


@pytest.mark.parametrize("pad", [
    "/etc/passwd",
    "../../etc/passwd",
    "components/../../buiten/.env",
    "",
    "   ",
])
def test_een_pad_buiten_de_werkkopie_wordt_geweigerd(db, pad):
    """Het pad komt uit een formulier. Zonder deze controle is "importeer een
    env-bestand" ook "schrijf een willekeurig bestand in de container"."""
    from fastapi import HTTPException

    from services.lab.cluster_env import ClusterEnvBestanden

    c = _cluster(db)
    with pytest.raises(HTTPException) as fout:
        ClusterEnvBestanden(db).zet(c.id, pad, "A=1\n")
    assert fout.value.status_code == 400


def test_opnieuw_importeren_vervangt_en_stapelt_niet(db):
    """Twee versies van hetzelfde pad betekent dat één ervan stil genegeerd
    wordt, en dan zoek je je scheel naar welke waarde nu geldt."""
    from services.lab.cluster_env import ClusterEnvBestanden

    c = _cluster(db)
    svc = ClusterEnvBestanden(db)
    svc.zet(c.id, "components/api/.env", "A=1\n")
    svc.zet(c.id, "components/api/.env", "A=2\nB=3\n")

    uit = svc.lijst(c.id)
    assert len(uit) == 1
    assert uit[0]["sleutels"] == ["A", "B"]


def test_een_te_groot_bestand_is_geen_env_bestand(db):
    from fastapi import HTTPException

    from services.lab.cluster_env import MAX_BYTES, ClusterEnvBestanden

    c = _cluster(db)
    with pytest.raises(HTTPException) as fout:
        ClusterEnvBestanden(db).zet(c.id, "components/api/.env", "A=" + "x" * MAX_BYTES)
    assert fout.value.status_code == 400


def test_de_inhoud_gaat_ongeschonden_via_stdin_naar_het_lab(db, monkeypatch):
    """Een env-bestand bevat aanhalingstekens, backticks en dollartekens. Als
    argument meegegeven overleeft dat de reis door `sh -c` niet -- en een
    half aangekomen sleutel is erger dan geen sleutel, want hij faalt pas bij
    gebruik."""
    from services.lab.cluster_env import ClusterEnvBestanden
    from services.lab.lab_service import LabService

    c = _cluster(db)
    ClusterEnvBestanden(db).zet(c.id, "components/app/.env.local", INHOUD)

    gezien = {}

    class _RT:
        async def exec(self, cid, cmd, **kw):
            gezien["cmd"] = cmd
            gezien["stdin"] = kw.get("stdin")
            return {"output": ""}

    monkeypatch.setattr(LabService, "runtime_voor", lambda self, _p: _RT())
    monkeypatch.setattr(LabService, "ensure_workers",
                        lambda self, _p: [type("W", (), {"container_id": "c1"})()])

    p = type("P", (), {"id": "lab1"})()
    uit = asyncio.run(ClusterEnvBestanden(db).schrijf_naar_lab(p, c))

    assert uit == ["components/app/.env.local"]
    assert gezien["stdin"].decode() == INHOUD, "de inhoud hoort ongewijzigd aan te komen"
    assert "zeer-geheim" not in " ".join(gezien["cmd"]), "waarde mag niet in het commando staan"
    # De werkkopie staat in een submap met de naam van de repo, niet in /workspace.
    assert "/workspace/Intelligenthive/components/app/.env.local" in " ".join(gezien["cmd"])
    assert "chmod 600" in " ".join(gezien["cmd"]), "niet leesbaar voor anderen in de container"


def test_zonder_bestanden_gebeurt_er_niets(db):
    """Een cluster zonder import hoort geen enkele dockeraanroep te doen."""
    from services.lab.cluster_env import ClusterEnvBestanden

    c = _cluster(db)
    p = type("P", (), {"id": "lab1"})()
    assert asyncio.run(ClusterEnvBestanden(db).schrijf_naar_lab(p, c)) == []


# ── welke plekken een project verwacht ───────────────────────────────────────
#
# De inhoud levert de gebruiker; de PLEKKEN hoort LabX te weten. Anders moet
# iemand vijf paden uit het hoofd intypen, en een typefout levert een bestand
# op dat nergens gelezen wordt -- stil, want niets klaagt over een bestand dat
# er niet is.


def test_een_voorbeeldbestand_wijst_het_echte_pad_aan():
    """`components/api/.env.example` staat in de repo; `components/api/.env`
    is wat de applicatie leest en wat in .gitignore staat."""
    from services.lab.verkenner_detectoren import detecteer_env_paden

    vondsten = detecteer_env_paden({
        "components/api/.env.example": "COSMOS_ENDPOINT=\nCOSMOS_KEY=\n",
        "components/app/.env.example": "VITE_TENANT_ID=\n",
        "utilities/env.template": "FOO=1\n",
    })
    paden = [v.sleutel for v in vondsten]

    assert paden == ["components/api/.env", "components/app/.env", "utilities/.env"]
    assert all(v.soort == "envbestand" for v in vondsten)
    assert all(v.bewijs for v in vondsten), "zonder vindplaats is het een gok"


def test_een_variant_is_zijn_eigen_doel():
    """`.env.debug` is geen voorbeeld van iets anders -- het IS het bestand."""
    from services.lab.verkenner_detectoren import detecteer_env_paden, doelpad

    assert doelpad("components/app/.env.debug") == "components/app/.env.debug"
    assert doelpad("components/app/.env.example") == "components/app/.env"
    assert doelpad("env.sample") == ".env"
    assert doelpad(".env") == ".env"

    paden = [v.sleutel for v in detecteer_env_paden({"components/app/.env.debug": "A=1\n"})]
    assert paden == ["components/app/.env.debug"]


def test_voorbeeld_en_echt_bestand_leveren_een_slot_en_niet_twee():
    """Staan ze er allebei, dan is het nog steeds één plek. Twee regels voor
    hetzelfde pad is twee keer dezelfde vraag."""
    from services.lab.verkenner_detectoren import detecteer_env_paden

    paden = [v.sleutel for v in detecteer_env_paden({
        "components/api/.env.example": "A=\n",
        "components/api/.env": "A=1\n",
    })]
    assert paden == ["components/api/.env"]


def test_het_model_mag_een_gitignored_pad_aanvullen():
    """De detector loopt langs wat IN de repo staat. `.env.local` staat in
    .gitignore en bestaat in een verse kloon niet, terwijl de start-dev-skill
    van IntelligentHive hem bij naam noemt. Documentatie lezen kan een model
    wel en een patroon niet."""
    from services.lab.verkenner import _model_vondsten

    uit = _model_vondsten({"env_bestanden": ["components/app/.env.local", "  ", ""]})
    env = [v for v in uit if v.soort == "envbestand"]

    assert [v.sleutel for v in env] == ["components/app/.env.local"]
    assert env[0].bron == "model"
    assert env[0].bevestigd is False, "een modelvoorstel hoort niet vooraf aangevinkt te staan"


def test_de_boom_telt_mee_niet_alleen_de_gelezen_inhoud():
    """Het snapshot leest maar een deel van de bestanden. IntelligentHive
    heeft negen .env.example-bestanden en de inhoud haalde er maar een paar;
    voor "waar hoort dit" is het pad genoeg."""
    from services.lab.verkenner_detectoren import detecteer_env_paden

    boom = ["components/api/.env.example", "components/app/.env.example",
            "components/e2e/.env.example", "scripts/lib/child-env.mjs",
            "assets/infra/services/fabric/environment.yml"]
    beeld = {"components/api/.env.example": "A=\nB=\n"}

    vondsten = detecteer_env_paden(beeld, boom)
    paden = [v.sleutel for v in vondsten]

    assert paden == ["components/api/.env", "components/app/.env", "components/e2e/.env"]
    assert "child-env.mjs" not in repr(paden), "een .mjs is geen env-bestand"
    assert "environment.yml" not in repr(paden)
    # Waar we de inhoud hebben, telt hij de sleutels; waar niet, zegt hij dat.
    op_pad = {v.sleutel: v.bewijs[0].fragment for v in vondsten}
    assert op_pad["components/api/.env"] == "2 sleutels"
    assert op_pad["components/app/.env"] == "staat in de repo"


# ── de kluis hoeft niet te vragen wat een bestand al levert ──────────────────


def test_een_sleutel_uit_een_env_bestand_hoeft_niet_in_de_kluis(db):
    """Anders staat er een rode "ontbreekt" bij een sleutel die allang
    geleverd wordt, en vraagt het scherm om een waarde die je niet hoeft te
    geven -- precies het werk dat importeren wegnam.

    De routerfunctie wordt rechtstreeks aangeroepen: een TestClient draait in
    een andere thread, en een in-memory SQLite deelt daar niet mee.
    """
    from routers.cluster_router import geheimen
    from services.lab.cluster_env import ClusterEnvBestanden

    c = _cluster(db)
    c.benodigde_geheimen = {"VITE_TENANT_ID": "voor het inloggen",
                            "NIET_GELEVERD": "staat nergens"}
    db.commit()
    ClusterEnvBestanden(db).zet(c.id, "components/app/.env.local",
                                "VITE_TENANT_ID=7048a888\n")

    uit = geheimen(c.id, db)
    op_naam = {r["naam"]: r for r in uit["benodigd"]}

    assert op_naam["VITE_TENANT_ID"]["in_env_bestand"] == "components/app/.env.local"
    assert op_naam["VITE_TENANT_ID"]["gevonden"] is True
    assert op_naam["NIET_GELEVERD"]["in_env_bestand"] is None
    assert op_naam["NIET_GELEVERD"]["gevonden"] is False
    # De WAARDE blijft binnen: dit endpoint geeft namen, nooit inhoud.
    assert "7048a888" not in repr(uit)


def test_het_startscript_krijgt_te_horen_welke_bestanden_labx_levert(db, monkeypatch):
    """Via de startopdracht, NIET via de containeromgeving.

    Die wordt alleen bij het aanmaken van de container gezet, en een
    applicatie-herstart draait in de bestaande container. Toen de variabele
    daar stond, bereikte hij het script nooit: het haalde alsnog instellingen
    op en schreef over de bestanden heen die er net waren neergezet.
    """
    import asyncio as _a

    from models.lab import Lab
    from services.lab.cluster_env import ClusterEnvBestanden
    from services.lab.lab_service import LabService

    c = _cluster(db)
    c.start_script = "#!/bin/sh\nexec node x"
    nu = "2026-10-18T10:00:00+00:00"
    p = Lab(id="lab1", name="Hive", image="node:22", volume_name="v", status="running",
            cpu_limit=2, mem_limit_mb=4096, ttl_hours=24, cluster_id=c.id,
            branch="labx/proefbouw", poort_map={"5173": 18004},
            created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    ClusterEnvBestanden(db).zet(c.id, "components/api/.env", "A=1\n")
    ClusterEnvBestanden(db).zet(c.id, "components/app/.env", "B=2\n")

    opdrachten = []

    class _RT:
        async def exec(self, cid, cmd, **kw):
            opdrachten.append(cmd[-1] if cmd else "")
            return {"output": "", "exit_code": 0}

    monkeypatch.setattr(LabService, "runtime_voor", lambda self, _p: _RT())
    monkeypatch.setattr(LabService, "ensure_workers",
                        lambda self, _p: [type("W", (), {"container_id": "c1"})()])

    _a.run(LabService(db).start_applicatie(p.id))

    start = next((o for o in opdrachten if "setsid" in o), "")
    assert "LABX_ENV_BESTANDEN=" in start, "het script moet weten wat LabX levert"
    assert "components/api/.env" in start and "components/app/.env" in start
    assert "export LABX_ENV_BESTANDEN" in start


# ── env-bestanden al BIJ het verkennen ──────────────────────────────────────
#
# Een plan bestaat normaal zonder cluster, en env-bestanden hingen aan het
# cluster. Wie zijn .env pas na goedkeuring kon aanleveren, liet het model
# raden naar sleutels die het had kunnen weten -- en dan verzint het
# startscript een naam waar jij er al een had.


def test_de_namen_gaan_naar_het_model_en_de_waarden_niet(db):
    """De scherpste grens van deze functie."""
    from services.lab.verkenner import _prompt

    prompt = _prompt(
        repo_url="https://x/y.git", branch="main", beeld={}, inhoud={}, vondsten=[],
        env_bestanden={"components/api/.env": ["COSMOS_KEY", "API_KEY"]})

    assert "COSMOS_KEY" in prompt and "API_KEY" in prompt
    assert "components/api/.env" in prompt
    assert "Vraag er NIET om in de kluis" in prompt


def test_zonder_bestanden_staat_er_niets_over_in_de_prompt(db):
    """Een leeg blok met een kopje erboven is ruis die het model moet wegdenken."""
    from services.lab.verkenner import _prompt

    prompt = _prompt(repo_url="https://x/y.git", branch="main", beeld={},
                     inhoud={}, vondsten=[])
    assert "ENV-BESTANDEN AANGELEVERD" not in prompt


def test_meegegeven_bestanden_worden_versleuteld_op_het_plan_bewaard(db, monkeypatch):
    """Niet in platte tekst in de database, ook niet in een plan."""
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    rijen = svc._env_rijen([{"pad": "components/api/.env", "inhoud": "A=geheim123\nB=2\n"}], None)

    assert rijen[0]["pad"] == "components/api/.env"
    assert rijen[0]["sleutels"] == ["A", "B"]
    assert "geheim123" not in rijen[0]["inhoud"], "de inhoud hoort versleuteld te zijn"


def test_zonder_meegegeven_bestanden_pakt_hij_die_van_het_cluster(db):
    """Dat maakt "opnieuw verkennen" zinvol nadat je ze alsnog hebt
    geimporteerd: het model krijgt de namen die het de vorige keer miste."""
    from services.lab.cluster_env import ClusterEnvBestanden
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    c = svc.maak(naam="Slim")
    db.commit()
    ClusterEnvBestanden(db).zet(c.id, "components/api/.env", "COSMOS_KEY=x\n")

    rijen = svc._env_rijen(None, c.id)
    assert [r["pad"] for r in rijen] == ["components/api/.env"]
    assert rijen[0]["sleutels"] == ["COSMOS_KEY"]


def test_een_pad_buiten_de_repo_wordt_ook_hier_geweigerd(db):
    """Dezelfde controle als bij importeren; dit is een tweede ingang."""
    from fastapi import HTTPException

    from services.lab.clusters import ClusterService

    with pytest.raises(HTTPException):
        ClusterService(db)._env_rijen([{"pad": "../../etc/passwd", "inhoud": "A=1"}], None)


def test_vooraf_gekozen_pakketten_en_modelvoorstellen_sluiten_elkaar_niet_uit(db):
    """Het model kent de repo, jij kent de bedoeling. Bij goedkeuren horen
    allebei te tellen -- anders moet je na elk plan alsnog aanvinken."""
    from models.cluster import ClusterPlan
    from services.lab.clusters import ClusterService

    nu = "2026-10-18T10:00:00+00:00"
    plan = ClusterPlan(
        repo_url="https://x/y.git", branch="main", status="voorstel",
        plan={"vondsten": [{"soort": "extra", "sleutel": "uv", "waarde": None}]},
        keuzes={}, antwoorden={}, opmerkingen={},
        lab_extras=["node"], created_at=nu, updated_at=nu)
    db.add(plan)
    db.commit()

    uit = ClusterService(db).keur_goed(
        plan.id, naam="Slim",
        gekozen=[{"soort": "extra", "sleutel": "uv"}])

    from models.cluster import Cluster
    c = db.get(Cluster, uit["id"] if isinstance(uit, dict) and "id" in uit else plan.cluster_id)
    assert set(c.lab_extras or []) == {"node", "uv"}
