"""Van verkenning naar cluster: het plan en het goedkeuren.

Hier ligt de regel vast die "model leidend" iets anders maakt dan "model
beslist": **wat de detectoren niet konden staven, gaat niet vanzelf mee.** Het
staat in het plan, met vindplaats, zichtbaar — maar uitgevinkt. Valt die regel
weg, dan draait er een container op grond van een modeluitspraak die niemand
heeft kunnen nakijken, en dat is precies wat deze opzet moest voorkomen.

De tweede regel die hier staat: een goedgekeurd plan is BEVROREN. Opnieuw
verkennen levert een nieuw plan naast dit op, zodat het verschil zichtbaar is
in plaats van dat de tweede de eerste stilletjes overschrijft.
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


#: Een verkenningsuitslag zoals de Verkenner hem oplevert: één gestaafde
#: dienst, één die het model voorstelde maar die nergens terug te vinden was,
#: en de twee lijsten eromheen.
UITSLAG = {
    "repo_url": "https://github.com/beeminds-data/Intelligenthive.git",
    "branch": "main",
    "commit": "abc123",
    "samenvatting": "Een yarn-monorepo met een API en een front-end.",
    "startscript": "#!/bin/sh\nset -e\nyarn install\nyarn build:dependencies\nyarn start:api",
    "vragen": ["Welk env-profiel moet de front-end gebruiken?"],
    "bestanden_gelezen": 23,
    "model": {"gebruikt": True, "fout": None},
    "vondsten": [
        {"soort": "dienst", "sleutel": "cosmos", "bevestigd": True, "zekerheid": "midden",
         "bron": "detector:omgeving", "toelichting": "Uit de omgevingssleutels.",
         "bewijs": [{"bestand": "components/api/.env.example", "regel": 12}],
         "waarde": {"image": "mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview",
                    "poorten": [8081, 1234], "omgeving": {},
                    "lab_omgeving": {"COSMOS_ENDPOINT": "https://cosmos:8081",
                                     "COSMOS_KEY": "publiek"}}},
        {"soort": "dienst", "sleutel": "rabbitmq", "bevestigd": False, "zekerheid": "laag",
         "bron": "model", "toelichting": "Kon ik niet terugvinden in de repo.",
         "bewijs": [{"bestand": "docs/BESTAAT-NIET.md", "regel": None}],
         "waarde": {"image": "rabbitmq:3", "poorten": [5672], "omgeving": {},
                    "lab_omgeving": {"AMQP_URL": "amqp://rabbitmq:5672"}}},
        {"soort": "lab_image", "sleutel": "node", "bevestigd": True, "zekerheid": "hoog",
         "bron": "detector:manifest", "toelichting": "yarn 4 via corepack.",
         "bewijs": [{"bestand": "package.json", "regel": 3}],
         "waarde": {"image": "node:22-bookworm"}},
        {"soort": "omgeving", "sleutel": "env_profiel", "bevestigd": True, "zekerheid": "hoog",
         "bron": "detector:omgeving", "toelichting": "Meerdere profielen.",
         "bewijs": [{"bestand": "components/app/.env.debug", "regel": None}],
         "waarde": {"keuzes": ["debug", "realestate"], "standaard": "debug"}},
        {"soort": "extern", "sleutel": "langfuse", "bevestigd": True, "zekerheid": "midden",
         "bron": "detector:omgeving", "toelichting": "Hier bestaat geen emulator van.",
         "bewijs": [{"bestand": "components/api/.env.example", "regel": 20}],
         "waarde": {"sleutels": ["LANGFUSE_SECRET_KEY"]}},
    ],
}


def _svc(db, uitslag=None):
    """ClusterService met een verkenner die niets aanraakt.

    De containerstappen zijn elders bewezen; hier gaat het om wat er met de
    uitslag gebeurt.
    """
    from services.lab import verkenner
    from services.lab.clusters import ClusterService

    class NepVerkenner:
        def __init__(self, _db):
            pass

        async def verken(self, **kw):
            self.kw = kw
            return dict(uitslag or UITSLAG)

    verkenner.Verkenner = NepVerkenner  # noqa: S3827 — alleen in deze test
    return ClusterService(db)


def _plan(db, **kw):
    svc = _svc(db)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"], **kw))
    return svc, rij


# ── verkennen bewaart, past niet toe ─────────────────────────────────────────

def test_verkenning_wordt_bewaard_als_voorstel(db):
    from models.cluster import Cluster

    svc, rij = _plan(db)
    assert rij.status == "voorstel"
    assert rij.commit == "abc123"
    assert rij.cluster_id is None
    assert db.query(Cluster).count() == 0, "verkennen maakt geen cluster aan"


def test_alleen_gestaafde_vondsten_staan_vooraf_aan(db):
    svc, rij = _plan(db)
    keuze = {(k["soort"], k["sleutel"]) for k in svc.standaardkeuze(rij.plan)}
    assert ("dienst", "cosmos") in keuze
    assert ("dienst", "rabbitmq") not in keuze, "onbevestigd gaat niet vanzelf mee"


# ── goedkeuren ───────────────────────────────────────────────────────────────

def test_goedkeuren_maakt_het_cluster_met_de_gestaafde_diensten(db):
    svc, rij = _plan(db)
    uit = svc.keur_goed(rij.id, naam="IntelligentHive")

    assert uit["diensten_toegevoegd"] == ["ih-cosmos"]
    c = svc.get_or_404(uit["cluster"]["id"])
    assert c.repo_url == UITSLAG["repo_url"]
    assert c.lab_image == "node:22-bookworm"
    assert c.env_profiel == "debug"
    assert "yarn build:dependencies" in (c.start_script or "")


def test_de_dienstnaam_krijgt_een_voorvoegsel_en_de_omgeving_volgt(db):
    """De dienstnaam is ook de DNS-naam. Wijst de omgeving naar de catalogusnaam,
    dan zoekt een branch-lab een host die op dit netwerk niet bestaat."""
    svc, rij = _plan(db)
    uit = svc.keur_goed(rij.id, naam="IntelligentHive")
    c = svc.get_or_404(uit["cluster"]["id"])
    assert c.omgeving["COSMOS_ENDPOINT"] == "https://ih-cosmos:8081"


def test_een_onbevestigde_dienst_gaat_niet_mee(db):
    svc, rij = _plan(db)
    uit = svc.keur_goed(rij.id, naam="IntelligentHive")
    assert "ih-rabbitmq" not in uit["diensten_toegevoegd"]
    c = svc.get_or_404(uit["cluster"]["id"])
    assert "AMQP_URL" not in (c.omgeving or {})


def test_een_onbevestigde_dienst_kan_wel_expliciet_aangevinkt_worden(db):
    """Uitgevinkt is niet hetzelfde als geweigerd -- jij mag hem aanzetten."""
    svc, rij = _plan(db)
    keuze = svc.standaardkeuze(rij.plan) + [{"soort": "dienst", "sleutel": "rabbitmq"}]
    uit = svc.keur_goed(rij.id, naam="IntelligentHive", gekozen=keuze)
    assert "ih-rabbitmq" in uit["diensten_toegevoegd"]


def test_benodigde_geheimen_worden_vastgelegd_en_gemeld(db):
    """Het rijtje waar een branch-lab anders veertig minuten later op omvalt."""
    svc, rij = _plan(db)
    uit = svc.keur_goed(rij.id, naam="IntelligentHive")
    c = svc.get_or_404(uit["cluster"]["id"])
    assert "langfuse" in (c.benodigde_geheimen or {})
    assert "langfuse" in uit["geheimen_ontbreken"], "staat niet in de kluis, dus melden"


def test_goedgekeurd_plan_is_bevroren(db):
    from fastapi import HTTPException

    svc, rij = _plan(db)
    svc.keur_goed(rij.id, naam="IntelligentHive")
    with pytest.raises(HTTPException) as fout:
        svc.keur_goed(rij.id, naam="IntelligentHive")
    assert fout.value.status_code == 409
    assert "opnieuw" in str(fout.value.detail).lower()


def test_opnieuw_verkennen_zet_het_vorige_plan_niet_opzij(db):
    svc, rij = _plan(db)
    uit = svc.keur_goed(rij.id, naam="IntelligentHive")
    cluster_id = uit["cluster"]["id"]

    tweede = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"], cluster_id=cluster_id))
    assert tweede.id != rij.id and tweede.status == "voorstel"
    assert svc.plan_of_404(rij.id).status == "goedgekeurd"
    assert len(svc.plannen(cluster_id)) == 2, "beide blijven, zodat je ze kunt vergelijken"


def test_zonder_naam_heet_het_cluster_naar_de_repo(db):
    svc, rij = _plan(db)
    uit = svc.keur_goed(rij.id)
    assert uit["cluster"]["naam"] == "Intelligenthive"


def test_een_plan_zonder_model_levert_nog_steeds_een_cluster(db):
    """Valt het model om, dan is dat geen mislukte verkenning -- alleen een
    dunner plan. De detectoren hebben er al een neergelegd."""
    kaal = dict(UITSLAG)
    kaal["model"] = {"gebruikt": False, "fout": "geen sleutel"}
    svc = _svc(db, kaal)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    assert rij.model_gebruikt == 0 and rij.fout == "geen sleutel"
    uit = svc.keur_goed(rij.id, naam="Hive")
    assert uit["diensten_toegevoegd"] == ["hiv-cosmos"], "eenwoordsnaam: eerste drie letters"


# ── namen die het model verzint ──────────────────────────────────────────────

def test_een_naam_van_het_model_wordt_een_bruikbare_sleutel():
    """Het model antwoordt met wat het ziet, en dat is soms een hele zin.

    Bij de eerste echte proef (ND3X) kwam er
    `llm-providers (OpenAI/Anthropic/Gemini/Azure Foundry)` terug. Als
    beschrijving klopt dat; als NAAM niet, want deze wordt de DNS-naam van een
    dienst en de naam waaronder je een geheim in de kluis zoekt.
    """
    from services.lab.verkenner import _slug

    assert _slug("Cosmos DB") == "cosmos-db"
    lang = _slug("llm-providers (OpenAI/Anthropic/Gemini/Azure Foundry)")
    assert len(lang) <= 40 and not lang.endswith("-")
    assert not lang.endswith("-az"), "afkappen gebeurt op een woordgrens"


def test_de_zin_van_het_model_blijft_leesbaar_in_de_toelichting():
    from services.lab.verkenner import _model_vondsten

    vondsten = _model_vondsten({"extern": [
        {"naam": "llm-providers (OpenAI/Anthropic)", "waarom": "Betaalde API's."}]})
    assert "llm-providers (OpenAI/Anthropic)" in vondsten[0].toelichting


# ── het lab-image is een keuze, geen lijstje ─────────────────────────────────

#: Zoals een echte verkenning van ND3X het opleverde: vijf kandidaten voor
#: het lab-image, waarvan er twee helemaal geen image zijn maar de applicatie
#: die uit de repo gebouwd wordt.
VEEL_IMAGES = {
    **{k: v for k, v in UITSLAG.items() if k != "vondsten"},
    "vondsten": [
        {"soort": "lab_image", "sleutel": "python", "bevestigd": True, "zekerheid": "hoog",
         "bron": "detector:manifest", "toelichting": "", "bewijs": [{"bestand": "requirements.txt"}],
         "waarde": {"image": "python:3.12-slim"}},
        {"soort": "lab_image", "sleutel": "Dockerfile", "bevestigd": True, "zekerheid": "hoog",
         "bron": "detector:dockerfile", "toelichting": "", "bewijs": [{"bestand": "Dockerfile", "regel": 1}],
         "waarde": {"image": "python:3.12-bookworm"}},
        {"soort": "applicatie", "sleutel": "api", "bevestigd": True, "zekerheid": "hoog",
         "bron": "detector:compose", "toelichting": "", "bewijs": [{"bestand": "docker-compose.yml", "regel": 23}],
         "waarde": {"build": "./backend", "image": None}},
        {"soort": "applicatie", "sleutel": "web", "bevestigd": True, "zekerheid": "hoog",
         "bron": "detector:compose", "toelichting": "", "bewijs": [{"bestand": "docker-compose.yml", "regel": 12}],
         "waarde": {"build": "./frontend", "image": None}},
    ],
}


def test_er_staat_hoogstens_een_lab_image_vooraf_aan(db):
    """Vijf aangevinkte images leveren er alsnog maar een op, en dan bepaalt de
    volgorde van een lus welke je krijgt. Dat is geen keuze maar een loterij."""
    svc = _svc(db, VEEL_IMAGES)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    images = [k for k in svc.standaardkeuze(rij.plan) if k["soort"] == "lab_image"]
    assert len(images) == 1


def test_een_vaststelling_is_geen_keuze(db):
    """Wat uit de repo gebouwd wordt, is wat een branch-lab draait. Daar valt
    niets aan te kiezen, dus het hoort niet in het vinkjeslijstje."""
    svc = _svc(db, VEEL_IMAGES)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    assert not [k for k in svc.standaardkeuze(rij.plan) if k["soort"] == "applicatie"]


def test_een_applicatie_zonder_image_overschrijft_het_lab_image_niet(db):
    """`api` heeft build: en geen image:. Die als lab_image overnemen maakt het
    veld leeg, en dan start een branch-lab op het standaard-image."""
    svc = _svc(db, VEEL_IMAGES)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    alles = [{"soort": v["soort"], "sleutel": v["sleutel"]} for v in rij.plan["vondsten"]]
    uit = svc.keur_goed(rij.id, naam="ND3X", gekozen=alles)
    c = svc.get_or_404(uit["cluster"]["id"])
    assert c.lab_image == "python:3.12-slim", "de eerste echte image wint, lege tellen niet"


# ── de dienstnaam op het netwerk ─────────────────────────────────────────────

def test_de_hostnaam_wordt_in_elke_vorm_vervangen():
    """De dienst heet op het netwerk `nx-ollama`, niet `ollama`.

    De omgeving komt in drie vormen binnen en alle drie moeten mee. De kale
    vorm (`ollama:11434`, zonder schema) was de eerste die misging: dan wijst
    de variabele naar een host die op dit netwerk niet bestaat, en dat merk je
    pas als een lab al draait.
    """
    from services.lab.clusters import _hernoem_host

    assert _hernoem_host("https://cosmos:8081", "cosmos", "ih-cosmos") == "https://ih-cosmos:8081"
    assert _hernoem_host("amqp://gast:gast@rabbitmq:5672/", "rabbitmq", "ih-rabbitmq") \
        == "amqp://gast:gast@ih-rabbitmq:5672/"
    assert _hernoem_host("ollama:11434", "ollama", "nx-ollama") == "nx-ollama:11434"
    assert _hernoem_host("ollama", "ollama", "nx-ollama") == "nx-ollama"


def test_een_halve_naam_wordt_niet_hernoemd():
    """`ollama-proxy` is een andere host dan `ollama`."""
    from services.lab.clusters import _hernoem_host

    assert _hernoem_host("http://ollama-proxy:80", "ollama", "nx-ollama") == "http://ollama-proxy:80"


def test_de_omgeving_van_een_branch_lab_wijst_naar_de_echte_dienstnaam(db):
    svc, rij = _plan(db)
    uit = svc.keur_goed(rij.id, naam="IntelligentHive")
    c = svc.get_or_404(uit["cluster"]["id"])
    assert c.omgeving["COSMOS_ENDPOINT"] == "https://ih-cosmos:8081"
