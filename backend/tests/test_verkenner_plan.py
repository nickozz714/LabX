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


# ── wat het model niet wist is geen waarde ───────────────────────────────────

def test_een_plaatshouder_wordt_een_geheim_en_geen_omgevingsvariabele():
    """Bij de eerste verkenning van een vreemde repo kwam dit terug:

        DB_PASS=<zelfde waarde als MYSQL_PASSWORD>
        JWT_SECRET=<geheim, verplicht: de app start niet zonder>

    Allebei terecht -- die waarden staan niet in de repo. Maar als
    omgevingsvariabele is het rampzalig: goedgekeurd zoals het is krijgt een
    branch-lab die zin letterlijk als JWT-sleutel, en dan draait hij, met een
    sleutel die iedereen kan raden.
    """
    from services.lab.verkenner import _model_vondsten

    vondsten = _model_vondsten({"vaste_diensten": [{
        "naam": "mysql", "image": "mysql:8", "poorten": [3306],
        "lab_omgeving": {
            "DB_HOST": "mysql",
            "DB_PORT": "3306",
            "DB_PASS": "<zelfde waarde als MYSQL_PASSWORD>",
            "JWT_SECRET": "<geheim, verplicht: de app start niet zonder>",
        },
    }]})
    waarde = vondsten[0].waarde
    assert waarde["lab_omgeving"] == {"DB_HOST": "mysql", "DB_PORT": "3306"}
    assert set(waarde["omgeving_uit_kluis"]) == {"DB_PASS", "JWT_SECRET"}
    assert "zelfde waarde als MYSQL_PASSWORD" in waarde["omgeving_uit_kluis"]["DB_PASS"]


def test_een_lege_waarde_telt_ook_als_onbekend():
    from services.lab.verkenner import _splits_plaatshouders

    echt, uit_kluis = _splits_plaatshouders({"A": "", "B": "${DB_PASS}", "C": "echt"})
    assert echt == {"C": "echt"}
    assert set(uit_kluis) == {"A", "B"}


def test_plaatshouders_komen_op_de_lijst_voor_de_kluis(db):
    """Bij een ECHTE dienst van buiten blijft een plaatshouder een vraag.

    Bij een dienst die LabX zelf start is dat juist niet zo -- die vult hij in
    (zie de tests verderop). Het verschil zit in wie de dienst maakt: wat
    LabX aanzet, daarvan kiest LabX ook de inloggegevens.
    """
    uitslag = {
        **{k: v for k, v in UITSLAG.items() if k != "vondsten"},
        "vondsten": [{
            "soort": "extern", "sleutel": "langfuse", "bevestigd": True, "zekerheid": "midden",
            "bron": "detector:omgeving", "toelichting": "Hier bestaat geen emulator van.",
            "bewijs": [{"bestand": "components/api/.env.example", "regel": 20}],
            "waarde": {"sleutels": ["LANGFUSE_SECRET_KEY"]},
        }],
    }
    svc = _svc(db, uitslag)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek")
    c = svc.get_or_404(uit["cluster"]["id"])

    assert "langfuse" in (c.benodigde_geheimen or {})
    assert "langfuse" in uit["geheimen_ontbreken"]


# ── bereikbaarheid is geen vraag voor de gebruiker ───────────────────────────

def test_de_poort_van_de_applicatie_komt_bij_het_cluster_terecht(db):
    """LabX publiceerde standaard 3000 en 7071. Luistert de applicatie op
    8000, dan gebeurt er niets -- zonder een enkele foutmelding. Welke poort
    dat is, staat in de repo en is dus geen vraag voor de gebruiker."""
    uitslag = {
        **{k: v for k, v in UITSLAG.items() if k != "vondsten"},
        "vondsten": [{
            "soort": "lab_poorten", "sleutel": "poorten", "bevestigd": False,
            "zekerheid": "laag", "bron": "model", "toelichting": "", "bewijs": [],
            "waarde": {"poorten": [8000]},
        }],
    }
    svc = _svc(db, uitslag)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek",
                        gekozen=[{"soort": "lab_poorten", "sleutel": "poorten"}])
    assert svc.get_or_404(uit["cluster"]["id"]).lab_poorten == [8000]


def test_een_lab_krijgt_zijn_eigen_adres_mee(db):
    """Vragen over PUBLIC_BASE_URL en CORS_ORIGINS zijn geen beslissingen maar
    gevolgen van hoe het lab gepubliceerd wordt. LabX weet ze, dus geeft hij ze."""
    from services.lab.clusters import ClusterService

    c = ClusterService(db).maak(naam="Hive")
    omgeving = ClusterService._adres_omgeving(c, {8000: 18004})
    assert omgeving["LABX_POORT"] == "8000"
    assert omgeving["LABX_LAB_POORT"] == "18004"
    assert omgeving["LABX_LAB_URL"] == "http://localhost:18004"


def test_op_een_andere_machine_blijft_het_adres_weg(db):
    """Die poort staat daar op de loopback en wordt hierheen getunneld op een
    nummer dat pas bij het openen vastligt. Een adres dat er wel staat maar
    niet klopt is erger dan een dat ontbreekt."""
    from services.lab.clusters import ClusterService

    c = ClusterService(db).maak(naam="Hive")
    c.host_id = 7
    omgeving = ClusterService._adres_omgeving(c, {8000: 18004})
    assert "LABX_LAB_URL" not in omgeving
    assert omgeving["LABX_POORT"] == "8000"


# ── antwoorden op de vragen van het model ────────────────────────────────────

def test_een_antwoord_levert_een_nieuw_plan_naast_het_oude(db):
    """Een vraag die je niet kunt beantwoorden is erger dan geen vraag.

    En een antwoord dat alleen opgeslagen wordt, is een antwoord dat nergens
    heen gaat: de vragen gaan juist over dingen die het startscript
    veranderen. Dus leidt antwoorden tot opnieuw verkennen — met het oude
    plan ernaast, want het verschil is het interessante deel.
    """
    svc, rij = _plan(db)
    nieuw = asyncio.run(svc.opnieuw(rij.id, antwoorden={"Welk ASGI-object?": "server:app"}))

    assert nieuw.id != rij.id
    assert nieuw.vorig_plan_id == rij.id
    assert nieuw.antwoorden == {"Welk ASGI-object?": "server:app"}
    assert svc.plan_of_404(rij.id).status == "voorstel", "het oude plan blijft staan"


def test_eerdere_antwoorden_gaan_mee(db):
    """Anders typ je bij elke ronde alles opnieuw."""
    svc, rij = _plan(db)
    tweede = asyncio.run(svc.opnieuw(rij.id, antwoorden={"A": "een"}))
    derde = asyncio.run(svc.opnieuw(tweede.id, antwoorden={"B": "twee"}))
    assert derde.antwoorden == {"A": "een", "B": "twee"}


def test_een_later_antwoord_overschrijft_een_eerder(db):
    svc, rij = _plan(db)
    tweede = asyncio.run(svc.opnieuw(rij.id, antwoorden={"A": "een"}))
    derde = asyncio.run(svc.opnieuw(tweede.id, antwoorden={"A": "toch anders"}))
    assert derde.antwoorden == {"A": "toch anders"}


def test_antwoorden_komen_bij_het_model_terecht(db):
    """Zonder dit is de hele ronde een dure manier om niets te veranderen."""
    from services.lab import verkenner

    gezien = {}

    class Vangende(verkenner.Verkenner):
        async def verken(self, **kw):
            gezien.update(kw)
            return dict(UITSLAG)

    svc = _svc(db)
    verkenner.Verkenner = Vangende
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    asyncio.run(svc.opnieuw(rij.id, antwoorden={"Welk ASGI-object?": "server:app"}))
    assert gezien.get("antwoorden") == {"Welk ASGI-object?": "server:app"}


def test_een_leeg_antwoord_doet_niets(db):
    """Opnieuw verkennen kost een modelaanroep; dat doe je niet voor niets."""
    from fastapi import HTTPException

    svc, rij = _plan(db)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.opnieuw(rij.id, antwoorden={"A": "   "}))
    assert fout.value.status_code == 400


def test_een_plan_onthoudt_met_welk_geheim_het_gekloond_is(db):
    """Een plan kan ZONDER cluster bestaan -- dat is zelfs de normale
    volgorde. Dan is er bij een antwoordronde niets om op terug te vallen, en
    strandde opnieuw verkennen op een privérepo zonder token."""
    svc = _svc(db)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"], token_geheim="sp-pat"))
    assert rij.cluster_id is None
    assert rij.token_geheim == "sp-pat", "de NAAM, nooit de waarde"

    from services.lab import verkenner

    gezien = {}

    class Vangende(verkenner.Verkenner):
        async def verken(self, **kw):
            gezien.update(kw)
            return dict(UITSLAG)

    verkenner.Verkenner = Vangende
    asyncio.run(svc.opnieuw(rij.id, antwoorden={"A": "een"}))
    assert gezien.get("token_geheim") == "sp-pat"


def test_zonder_token_noemt_de_foutmelding_de_waarschijnlijke_oorzaak():
    """"docker run faalde (128)" is waar en nutteloos. Git zegt bij een
    privérepo zonder token dat hij "not found" is -- want zonder token BESTAAT
    hij niet voor je, en dat is precies de uitleg die je dan nodig hebt."""
    from services.lab.verkenner import _lijkt_prive

    assert _lijkt_prive("remote: Repository not found.")
    assert _lijkt_prive("fatal: Authentication failed for 'https://github.com/...'")
    assert not _lijkt_prive("fatal: could not create work tree dir: disk full")


# ── terugkoppeling hoort niet af te hangen van wat het model vroeg ───────────

def test_een_opmerking_bij_een_onderdeel_is_genoeg(db):
    """Antwoorden kon al, maar alleen op vragen die het model toevallig
    stelde. Een plan zonder vragen liet je met niets -- terwijl je er wel
    iets van kunt vinden."""
    svc, rij = _plan(db)
    nieuw = asyncio.run(svc.opnieuw(rij.id, opmerkingen={"dienst::cosmos": "deze hoort er niet bij"}))
    assert nieuw.vorig_plan_id == rij.id
    assert nieuw.opmerkingen == {"dienst::cosmos": "deze hoort er niet bij"}


def test_een_opmerking_over_het_geheel_is_ook_genoeg(db):
    svc, rij = _plan(db)
    nieuw = asyncio.run(svc.opnieuw(rij.id, opmerking="het lab-image klopt niet, dit is een node-project"))
    assert "node-project" in (nieuw.opmerking or "")


def test_zonder_enige_terugkoppeling_gebeurt_er_niets(db):
    """Opnieuw verkennen kost een modelaanroep en levert zonder iets te zeggen
    hetzelfde plan op."""
    from fastapi import HTTPException

    svc, rij = _plan(db)
    with pytest.raises(HTTPException) as fout:
        asyncio.run(svc.opnieuw(rij.id))
    assert fout.value.status_code == 400


def test_de_terugkoppeling_komt_bij_het_model_terecht(db):
    """Zonder dit is de hele ronde een dure manier om niets te veranderen."""
    from services.lab import verkenner

    gezien = {}

    class Vangende(verkenner.Verkenner):
        async def verken(self, **kw):
            gezien.update(kw)
            return dict(UITSLAG)

    svc = _svc(db)
    verkenner.Verkenner = Vangende
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    asyncio.run(svc.opnieuw(rij.id, opmerking="klopt niet",
                            opmerkingen={"dienst::cosmos": "weg hiermee"},
                            keuze=[{"soort": "lab_image", "sleutel": "node"}]))
    assert gezien["opmerking"] == "klopt niet"
    assert gezien["opmerkingen"] == {"dienst::cosmos": "weg hiermee"}
    assert gezien["keuze"] == [{"soort": "lab_image", "sleutel": "node"}]


def test_wat_je_uitvinkte_gaat_mee_als_signaal(db):
    """Een vinkje uitzetten is ook terugkoppeling, en het model hoort dat te
    weten -- anders stelt het bij elke ronde dezelfde dienst weer voor."""
    from services.lab import verkenner

    gezien = {}

    class Vangende(verkenner.Verkenner):
        async def verken(self, **kw):
            gezien.update(kw)
            return dict(UITSLAG)

    svc = _svc(db)
    verkenner.Verkenner = Vangende
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    asyncio.run(svc.opnieuw(rij.id, opmerking="iets"))
    keuze = {(k["soort"], k["sleutel"]) for k in gezien["keuze"]}
    assert ("dienst", "cosmos") in keuze
    assert ("dienst", "rabbitmq") not in keuze, "onbevestigd stond uit, en dat telt mee"


# ── jouw oordeel gaat boven dat van een detector ─────────────────────────────

def test_wat_je_uitvinkte_komt_terug_maar_staat_uit(db):
    """De samenvoegregel laat de detector winnen van het model, en dat klopt:
    die heeft een vindplaats. Maar het liet ook de GEBRUIKER verliezen --
    zeg je "mailpit hoort er niet bij", dan vond de detector hem de volgende
    ronde gewoon weer, want in de repo is niets veranderd.

    De vondst verdwijnt niet: het bewijs is nog net zo waar. Hij staat alleen
    niet meer vooraf aan.
    """
    from services.lab.verkenner import _pas_terugkoppeling_toe
    from services.lab.verkenner_detectoren import Vondst

    vondsten = [Vondst(soort="dienst", sleutel="mailpit", zekerheid="midden",
                       bron="detector:omgeving", bevestigd=True)]
    _pas_terugkoppeling_toe(vondsten, {"dienst::mailpit": "niet nodig in een lab"},
                            [{"soort": "dienst", "sleutel": "mailpit"}])

    assert vondsten[0].door_gebruiker_uit is True
    assert vondsten[0].bevestigd is True, "het bewijs blijft waar"
    assert vondsten[0].opmerking == "niet nodig in een lab"


def test_een_uitgezet_onderdeel_staat_niet_vooraf_aan(db):
    from services.lab.clusters import ClusterService

    plan = {"vondsten": [
        {"soort": "dienst", "sleutel": "mysql", "bevestigd": True},
        {"soort": "dienst", "sleutel": "mailpit", "bevestigd": True, "door_gebruiker_uit": True},
    ]}
    keuze = {(k["soort"], k["sleutel"]) for k in ClusterService.standaardkeuze(plan)}
    assert ("dienst", "mysql") in keuze
    assert ("dienst", "mailpit") not in keuze


def test_alleen_wat_je_echt_afwees_telt_als_uitgezet(db):
    """Een onderdeel dat je aan laat staan is niet afgewezen, en een dat deze
    ronde nieuw opduikt heb je nooit gezien."""
    from services.lab import verkenner

    gezien = {}

    class Vangende(verkenner.Verkenner):
        async def verken(self, **kw):
            gezien.update(kw)
            return dict(UITSLAG)

    svc = _svc(db)
    verkenner.Verkenner = Vangende
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    # cosmos aanlaten, het geheim en het profiel uitzetten
    asyncio.run(svc.opnieuw(rij.id, opmerking="iets",
                            keuze=[{"soort": "dienst", "sleutel": "cosmos"},
                                   {"soort": "lab_image", "sleutel": "node"}]))

    uit = {(k["soort"], k["sleutel"]) for k in gezien["uitgezet"]}
    assert ("extern", "langfuse") in uit, "die stond aan en je zette hem uit"
    assert ("dienst", "cosmos") not in uit


def test_een_keuze_maken_is_geen_afwijzing(db):
    """Bij het lab-image kan er maar EEN aan staan. De andere drie "door jou
    afgewezen" noemen maakt dat label betekenisloos, en dan leest niemand het
    nog waar het wel toe doet."""
    from services.lab import verkenner

    gezien = {}

    class Vangende(verkenner.Verkenner):
        async def verken(self, **kw):
            gezien.update(kw)
            return dict(VEEL_IMAGES)

    svc = _svc(db, VEEL_IMAGES)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    verkenner.Verkenner = Vangende
    asyncio.run(svc.opnieuw(rij.id, opmerking="iets",
                            keuze=[{"soort": "lab_image", "sleutel": "python"}]))

    soorten = {k["soort"] for k in gezien["uitgezet"]}
    assert "lab_image" not in soorten
    assert "applicatie" not in soorten


# ── een git-fout die ergens over gaat ────────────────────────────────────────

GIT_SSH_FOUT = """git@github.com: Permission denied (publickey).
fatal: Could not read from remote repository.

Please make sure you have the correct access rights
and the repository exists."""


def test_de_oorzaak_wordt_uit_een_git_fout_gehaald():
    """Git spreidt zijn fout over vijf regels en zet de kern bovenaan. De
    laatste regel pakken levert "and the repository exists." op — waar en
    volstrekt nutteloos."""
    from services.lab.verkenner import _git_reden

    assert "Permission denied (publickey)" in _git_reden(GIT_SSH_FOUT)


def test_een_geweigerde_sleutel_telt_als_toegangsprobleem():
    """Anders krijg je geen hint over waar je de deploy key moet neerzetten."""
    from services.lab.verkenner import _lijkt_prive

    assert _lijkt_prive(GIT_SSH_FOUT)


# ── LabX kiest de inloggegevens van wat hij zelf start ───────────────────────

MET_DATABASE = {
    **{k: v for k, v in UITSLAG.items() if k != "vondsten"},
    "vondsten": [{
        "soort": "dienst", "sleutel": "mysql", "bevestigd": True, "zekerheid": "hoog",
        "bron": "detector:compose", "toelichting": "", "bewijs": [{"bestand": "docker/docker-compose.yml"}],
        "waarde": {
            "image": "mysql:8", "poorten": [3306],
            "omgeving": {"MYSQL_ROOT_PASSWORD": "dev-root", "MYSQL_USER": "sp_user",
                         "MYSQL_PASSWORD": "dev-sp", "MYSQL_DATABASE": "sp"},
            "lab_omgeving": {"DB_USER": "sp_user", "DB_PASS": "dev-sp",
                             "DB_URL": "mysql://sp_user:dev-sp@mysql:3306/sp"},
            "omgeving_uit_kluis": {"MYSQL_PASSWORD": "wat moet dit zijn?"},
        },
    }],
}


def test_labx_kiest_het_wachtwoord_van_een_dienst_die_hij_zelf_start(db):
    """Het model maakte een MySQL aan, verzon er een wachtwoord bij, en vroeg
    de gebruiker daarna wat het wachtwoord van de database moest zijn. Die kon
    dat niet weten: de database bestond nog niet."""
    svc = _svc(db, MET_DATABASE)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek")
    c = svc.get_or_404(uit["cluster"]["id"])
    dienst = svc.diensten(c.id)[0]

    gekozen = dienst.omgeving["MYSQL_PASSWORD"]
    assert gekozen not in ("dev-sp", "", None) and len(gekozen) >= 20
    # Root en gebruiker krijgen hetzelfde wachtwoord. Voor een wegwerp-
    # database in een lab is dat geen bezwaar; een stille mismatch wel.
    assert dienst.omgeving["MYSQL_ROOT_PASSWORD"] == gekozen
    assert "dev-root" not in str(dienst.omgeving)


def test_de_applicatie_verbindt_met_hetzelfde_wachtwoord(db):
    """Anders start de database met het ene wachtwoord en verbindt de
    applicatie met het andere — en dat merk je pas als het lab al draait."""
    svc = _svc(db, MET_DATABASE)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek")
    c = svc.get_or_404(uit["cluster"]["id"])
    gekozen = svc.diensten(c.id)[0].omgeving["MYSQL_PASSWORD"]

    assert c.omgeving["DB_PASS"] == gekozen
    assert gekozen in c.omgeving["DB_URL"], "ook in een verbindingsreeks"
    assert "dev-sp" not in str(c.omgeving)


def test_er_wordt_niet_meer_naar_gevraagd(db):
    svc = _svc(db, MET_DATABASE)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek")
    c = svc.get_or_404(uit["cluster"]["id"])

    assert "MYSQL_PASSWORD" not in (c.benodigde_geheimen or {})
    assert "MYSQL_PASSWORD" not in uit["geheimen_ontbreken"]


def test_een_echte_sleutel_blijft_met_rust(db):
    """Een waarde die er al staat en geen voorbeeld is, is van de gebruiker."""
    from services.lab.clusters import _ZWAK

    assert _ZWAK.match("dev-sp") and _ZWAK.match("") and _ZWAK.match("<geheim>")
    assert not _ZWAK.match("Xk9fQ2mLpR4vT8wZ")


TWEE_VERZINSELS = {
    **{k: v for k, v in UITSLAG.items() if k != "vondsten"},
    "vondsten": [
        {"soort": "dienst", "sleutel": "mysql", "bevestigd": True, "zekerheid": "hoog",
         "bron": "detector:compose", "toelichting": "", "bewijs": [{"bestand": "docker-compose.yml"}],
         "waarde": {
             "image": "mysql:8.0", "poorten": [3306],
             "omgeving": {"MYSQL_ROOT_PASSWORD": "${MYSQL_ROOT_PASSWORD:?zet dit in .env}",
                          "MYSQL_PASSWORD": "${DB_PASS:?zet dit in .env}"},
             "lab_omgeving": {"DB_PASS": "labx-mysql-root-7f3k9q",
                              "DB_URL": "mysql://root:labx-mysql-root-7f3k9q@mysql:3306/sp"},
             "omgeving_uit_kluis": {},
         }},
        {"soort": "dienst", "sleutel": "mailpit", "bevestigd": True, "zekerheid": "midden",
         "bron": "detector:omgeving", "toelichting": "", "bewijs": [{"bestand": ".env"}],
         "waarde": {"image": "axllent/mailpit", "poorten": [1025], "omgeving": {},
                    "lab_omgeving": {"SMTP_HOST": "mailpit"},
                    "omgeving_uit_kluis": {"SMTP_USERNAME": "?", "SMTP_PASSWORD": "?"}}},
        {"soort": "geheim", "sleutel": "DB_PASS", "bevestigd": True, "zekerheid": "laag",
         "bron": "detector:omgeving", "toelichting": "", "bewijs": [{"bestand": ".env", "regel": 9}],
         "waarde": {}},
        {"soort": "geheim", "sleutel": "JWT_SECRET", "bevestigd": True, "zekerheid": "laag",
         "bron": "detector:omgeving", "toelichting": "", "bewijs": [{"bestand": ".env", "regel": 18}],
         "waarde": {}},
    ],
}


def test_twee_verzinsels_worden_een_wachtwoord(db):
    """Het model zette in de container `${DB_PASS:?...}` en in de lab-omgeving
    `labx-mysql-root-7f3k9q`. Dan start de database met het ene en verbindt de
    applicatie met het andere — en dat merk je pas als het lab al draait."""
    svc = _svc(db, TWEE_VERZINSELS)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek")
    c = svc.get_or_404(uit["cluster"]["id"])
    mysql = [d for d in svc.diensten(c.id) if d.naam.endswith("mysql")][0]

    ww = mysql.omgeving["MYSQL_PASSWORD"]
    assert ww == mysql.omgeving["MYSQL_ROOT_PASSWORD"], "één wachtwoord per dienst"
    assert "${" not in ww and len(ww) >= 20
    assert c.omgeving["DB_PASS"] == ww
    assert ww in c.omgeving["DB_URL"] and "labx-mysql-root-7f3k9q" not in str(c.omgeving)


def test_naar_de_inlog_van_een_eigen_dienst_wordt_niet_gevraagd(db):
    """Mailpit wordt door LabX gestart en vraagt niet om authenticatie. De
    gebruiker kan die gegevens sowieso niet weten: de dienst bestaat nog niet."""
    svc = _svc(db, TWEE_VERZINSELS)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek")
    c = svc.get_or_404(uit["cluster"]["id"])

    assert "SMTP_USERNAME" not in (c.benodigde_geheimen or {})
    assert "SMTP_PASSWORD" not in (c.benodigde_geheimen or {})


def test_een_sleutel_die_labx_zelf_invult_is_geen_ontbrekend_geheim(db):
    """De detector zag DB_PASS los in een .env staan. Daar los om vragen
    betekent vragen om een wachtwoord dat er allang is."""
    svc = _svc(db, TWEE_VERZINSELS)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek")
    c = svc.get_or_404(uit["cluster"]["id"])

    assert "DB_PASS" not in (c.benodigde_geheimen or {})
    assert "JWT_SECRET" in (c.benodigde_geheimen or {}), "die is wel echt van jou"


def test_shell_expansie_hoort_niet_in_een_container_omgeving():
    """Een compose-bestand vult `${DB_NAME:-slimmer_politiek}` in; `docker run
    -e` doet dat niet. Die tekst komt dan LETTERLIJK in de container terecht,
    en MySQL maakt een database die zo heet — of weigert de naam. Het ziet er
    bovendien uit alsof het klopt, want in de compose stond het er net zo."""
    from services.lab.clusters import _zonder_expansie

    uit = _zonder_expansie({
        "MYSQL_DATABASE": "${DB_NAME:-slimmer_politiek}",
        "MYSQL_USER": "${DB_USER-sp_user}",
        "MYSQL_ROOT_PASSWORD": "${MYSQL_ROOT_PASSWORD:?zet dit in .env}",
        "LOSSE": "$HOST",
        "GEWOON": "utf8mb4",
    })
    assert uit["MYSQL_DATABASE"] == "slimmer_politiek", "de bedoelde waarde staat in de tekst"
    assert uit["MYSQL_USER"] == "sp_user"
    assert uit["GEWOON"] == "utf8mb4"
    # Zonder bedoelde waarde hangt het van de sleutel af. Een inloggegeven
    # blijft staan (leeg), want de stap erna vult hem met een gegenereerd
    # wachtwoord -- weggooien betekende dat MySQL zonder wachtwoord startte.
    assert uit["MYSQL_ROOT_PASSWORD"] == ""
    # Al het andere valt weg; het image gebruikt dan zijn eigen standaard.
    assert "LOSSE" not in uit


def test_de_database_krijgt_een_echte_naam(db):
    uitslag = {
        **{k: v for k, v in UITSLAG.items() if k != "vondsten"},
        "vondsten": [{
            "soort": "dienst", "sleutel": "mysql", "bevestigd": True, "zekerheid": "hoog",
            "bron": "detector:compose", "toelichting": "", "bewijs": [{"bestand": "docker-compose.yml"}],
            "waarde": {"image": "mysql:8", "poorten": [3306],
                       "omgeving": {"MYSQL_DATABASE": "${DB_NAME:-slimmer_politiek}",
                                    "MYSQL_PASSWORD": "${DB_PASS:?zet dit in .env}"},
                       "lab_omgeving": {"DB_NAME": "${DB_NAME:-slimmer_politiek}"},
                       "omgeving_uit_kluis": {}},
        }],
    }
    svc = _svc(db, uitslag)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="SlimmerPolitiek")
    c = svc.get_or_404(uit["cluster"]["id"])
    dienst = svc.diensten(c.id)[0]

    assert dienst.omgeving["MYSQL_DATABASE"] == "slimmer_politiek"
    assert c.omgeving["DB_NAME"] == "slimmer_politiek"
    assert "${" not in str(dienst.omgeving) and "${" not in str(c.omgeving)
    assert len(dienst.omgeving["MYSQL_PASSWORD"]) >= 20, "het wachtwoord wordt alsnog gezet"


# ── de briefing vooraf ───────────────────────────────────────────────────────

def test_een_instructie_vooraf_komt_bij_het_model():
    """Zonder dit kun je pas iets zeggen nadat er een plan ligt, en dat kost
    een hele ronde van een minuut."""
    from services.lab.verkenner import _prompt

    prompt = _prompt(repo_url="x", branch="main", beeld={"boom": []}, inhoud={},
                     vondsten=[], instructie="De agent hoort er niet bij.")
    assert "OPDRACHT VAN DE GEBRUIKER" in prompt
    assert "De agent hoort er niet bij." in prompt
    # Vooraan, niet ergens tussen de bestanden: een briefing onderaan leest
    # als een detail.
    assert prompt.index("OPDRACHT VAN DE GEBRUIKER") < prompt.index("BESTANDEN")


def test_de_instructie_wordt_bewaard_bij_het_plan(db):
    svc = _svc(db)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"],
                                 instructie="Gebruik node 22, niet de devcontainer."))
    assert rij.instructie == "Gebruik node 22, niet de devcontainer."
    assert svc.plan_dict(rij)["instructie"] == "Gebruik node 22, niet de devcontainer."


def test_een_instructie_alleen_is_genoeg_om_opnieuw_te_verkennen(db):
    svc, rij = _plan(db)
    nieuw = asyncio.run(svc.opnieuw(rij.id, instructie="Laat de agent weg."))
    assert nieuw.instructie == "Laat de agent weg."


def test_de_briefing_blijft_gelden_bij_opnieuw_verkennen(db):
    """"Opnieuw verkennen" vanaf de clusterpagina heeft geen invoerveld. Hem
    daar stil laten vallen is de vervelendste uitkomst: je krijgt een plan dat
    alles terugbrengt wat je er net uit had gepraat."""
    svc = _svc(db)
    eerste = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"],
                                    instructie="De agent hoort er niet bij."))
    uit = svc.keur_goed(eerste.id, naam="Hive")
    cluster_id = uit["cluster"]["id"]

    tweede = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"], cluster_id=cluster_id))
    assert tweede.instructie == "De agent hoort er niet bij."


def test_een_nieuwe_briefing_vervangt_de_oude(db):
    svc, rij = _plan(db)
    tweede = asyncio.run(svc.opnieuw(rij.id, instructie="Eerst dit."))
    derde = asyncio.run(svc.opnieuw(tweede.id, instructie="Nee, toch dat."))
    assert derde.instructie == "Nee, toch dat."


def test_een_opdracht_kan_ook_een_detectorvondst_uitzetten():
    """Een detector vindt wat er in de repo STAAT en weet niet wat je ermee
    wilt. Zeg je "de agent hoort er niet bij", dan blijft hij `postgres`
    gewoon vinden — en dan staat die er alsnog aangevinkt, want de detector
    wint van het model."""
    from services.lab.verkenner import _pas_uitsluitingen_toe
    from services.lab.verkenner_detectoren import Vondst

    vondsten = [
        Vondst(soort="dienst", sleutel="postgres", bron="detector:omgeving", bevestigd=True),
        Vondst(soort="dienst", sleutel="cosmos", bron="detector:omgeving", bevestigd=True),
    ]
    _pas_uitsluitingen_toe(vondsten, [
        {"soort": "dienst", "sleutel": "postgres", "waarom": "hoort bij de agent"}])

    assert vondsten[0].uitgesloten is True
    assert vondsten[0].bevestigd is True, "het bewijs blijft waar"
    assert vondsten[0].opmerking == "hoort bij de agent"
    assert vondsten[1].uitgesloten is False


def test_een_uitgesloten_vondst_staat_niet_vooraf_aan():
    from services.lab.clusters import ClusterService

    plan = {"vondsten": [
        {"soort": "dienst", "sleutel": "cosmos", "bevestigd": True},
        {"soort": "dienst", "sleutel": "postgres", "bevestigd": True, "uitgesloten": True},
    ]}
    keuze = {(k["soort"], k["sleutel"]) for k in ClusterService.standaardkeuze(plan)}
    assert ("dienst", "cosmos") in keuze
    assert ("dienst", "postgres") not in keuze


def test_aanvinken_van_azure_cli_zet_het_op_het_cluster(db):
    """Zonder deze schakel blijft de vondst een melding en verandert er
    niets: het lab krijgt de CLI nog steeds niet."""
    uitslag = {
        **{k: v for k, v in UITSLAG.items() if k != "vondsten"},
        "vondsten": [{
            "soort": "omgeving", "sleutel": "azure_cli", "bevestigd": True,
            "zekerheid": "hoog", "bron": "detector:azure", "toelichting": "",
            "bewijs": [{"bestand": ".devcontainer/ensure-access.sh", "regel": 3}],
            "waarde": {"nodig": True},
        }],
    }
    svc = _svc(db, uitslag)
    rij = asyncio.run(svc.verken(repo_url=UITSLAG["repo_url"]))
    uit = svc.keur_goed(rij.id, naam="Hive")
    # SQLite bewaart dit als 0/1; de code leest hem overal als bool.
    assert bool(svc.get_or_404(uit["cluster"]["id"]).lab_azure) is True
    assert uit["cluster"]["lab_azure"] is True
