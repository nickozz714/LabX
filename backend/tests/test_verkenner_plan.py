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
