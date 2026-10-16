"""De kluis van een cluster.

Waarom dit apart moest van de gedeelde kluis: daar is de naam globaal uniek —
bewust, want "een naam die twee dingen kan betekenen is geen geheim maar een
gok". Maar een cluster verwijst naar geheimen met de naam van een
OMGEVINGSVARIABELE: JWT_SECRET, DB_PASS, API_KEY. Die namen zijn juist niet
uniek; elke applicatie heeft ze.

Zonder eigen kluis vechten twee clusters dus om dezelfde `JWT_SECRET`, en wie
het laatst opslaat wint — zonder dat iemand het merkt, want de waarde is
nergens te zien. Dat is het soort fout waar je weken later achter komt, in de
verkeerde omgeving.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

#: Een echte Fernet-sleutel. De tekenreeks die andere testbestanden gebruiken
#: is geen geldige sleutel -- dat valt daar niet op omdat er niets versleuteld
#: wordt, maar hier wel.
_TESTSLEUTEL = "LzE2blwyqch7JzfpwAN5GYZybrHjsSFAvahyKYaQTPU="


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

    # settings leest de sleutel bij het IMPORTEREN van config, en _fernet is
    # lru_cached -- een env-variabele achteraf zetten komt dus te laat. Dit
    # maakt de test onafhankelijk van hoe pytest gestart is.
    import config
    from utils import crypto

    monkeypatch.setattr(config.settings, "FERNET_KEY",
                        _TESTSLEUTEL, raising=False)
    crypto._fernet.cache_clear()
    monkeypatch.setenv("LABX_FERNET_KEY",
                       _TESTSLEUTEL)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()
        crypto._fernet.cache_clear()


def _kluis(db):
    from services.lab.cluster_kluis import ClusterKluis

    return ClusterKluis(db)


def _cluster(db, naam):
    from services.lab.clusters import ClusterService

    return ClusterService(db).maak(naam=naam)


# ── het hele punt ────────────────────────────────────────────────────────────

def test_twee_clusters_hebben_elk_een_eigen_jwt_secret(db):
    een, twee = _cluster(db, "Hive"), _cluster(db, "Politiek")
    kluis = _kluis(db)
    kluis.zet(een.id, naam="JWT_SECRET", waarde="sleutel-van-hive")
    kluis.zet(twee.id, naam="JWT_SECRET", waarde="sleutel-van-politiek")

    assert kluis.waarde(een.id, "JWT_SECRET") == "sleutel-van-hive"
    assert kluis.waarde(twee.id, "JWT_SECRET") == "sleutel-van-politiek"


def test_het_specifiekere_geheim_wint(db):
    """Andersom is een geheim per cluster zinloos: het wordt dan overschaduwd
    door precies het gedeelde geheim dat je wilde vermijden."""
    from services.secrets.vault import VaultService

    c = _cluster(db, "Hive")
    VaultService(db).zet(naam="API_KEY", waarde="gedeeld")
    assert _kluis(db).waarde(c.id, "API_KEY") == "gedeeld", "zonder eigen geheim telt de gedeelde"

    _kluis(db).zet(c.id, naam="API_KEY", waarde="van-dit-cluster")
    assert _kluis(db).waarde(c.id, "API_KEY") == "van-dit-cluster"


def test_een_gedeeld_geheim_blijft_bruikbaar(db):
    """Zo kan een toegangstoken één keer bestaan en overal gelden, terwijl
    DB_PASS per cluster verschilt."""
    from services.secrets.vault import VaultService

    c = _cluster(db, "Hive")
    VaultService(db).zet(naam="repo-pat", waarde="ghp_xyz")
    assert _kluis(db).waarde(c.id, "repo-pat") == "ghp_xyz"
    assert _kluis(db).bestaat(c.id, "repo-pat")


# ── wat eruit komt, en wat niet ──────────────────────────────────────────────

def test_de_waarde_komt_niet_in_het_antwoord(db):
    c = _cluster(db, "Hive")
    rij = _kluis(db).zet(c.id, naam="JWT_SECRET", waarde="heelgeheim", omschrijving="voor auth")
    uit = _kluis(db).to_dict(rij)

    assert "heelgeheim" not in str(uit)
    assert uit["lengte"] == len("heelgeheim"), "lengte wel: genoeg om te zien of je de juiste plakte"
    assert uit["omschrijving"] == "voor auth"


def test_een_ontbrekend_geheim_levert_geen_lege_variabele_op(db):
    """Erger dan een variabele die ontbreekt: een applicatie die JWT_SECRET=""
    krijgt start vaak gewoon — met een sleutel die iedereen kan raden."""
    c = _cluster(db, "Hive")
    _kluis(db).zet(c.id, naam="DB_PASS", waarde="wachtwoord")

    omgeving = _kluis(db).los_op(c.id, {"DB_PASS": "", "JWT_SECRET": ""})
    assert omgeving == {"DB_PASS": "wachtwoord"}
    assert "JWT_SECRET" not in omgeving


def test_een_nieuw_geheim_zonder_waarde_wordt_geweigerd(db):
    from fastapi import HTTPException

    c = _cluster(db, "Hive")
    with pytest.raises(HTTPException):
        _kluis(db).zet(c.id, naam="JWT_SECRET")


def test_bijwerken_zonder_waarde_laat_de_waarde_staan(db):
    """Het scherm krijgt de waarde toch nooit terug, dus een leeg veld is de
    normale toestand als je alleen de omschrijving aanpast."""
    c = _cluster(db, "Hive")
    _kluis(db).zet(c.id, naam="JWT_SECRET", waarde="origineel")
    _kluis(db).zet(c.id, naam="JWT_SECRET", omschrijving="nu met uitleg")

    assert _kluis(db).waarde(c.id, "JWT_SECRET") == "origineel"
    assert _kluis(db).haal(c.id, "JWT_SECRET").omschrijving == "nu met uitleg"


# ── wat het cluster ermee doet ───────────────────────────────────────────────

def test_een_gevuld_geheim_telt_niet_meer_als_ontbrekend(db):
    from services.lab.clusters import ClusterService

    svc = ClusterService(db)
    c = _cluster(db, "Hive")
    c.benodigde_geheimen = {"JWT_SECRET": "verplicht", "DB_PASS": "van de database"}
    db.commit()

    assert svc.to_dict(c)["geheimen_ontbreken"] == ["DB_PASS", "JWT_SECRET"]
    _kluis(db).zet(c.id, naam="JWT_SECRET", waarde="x")
    assert svc.to_dict(c)["geheimen_ontbreken"] == ["DB_PASS"]


def test_de_kluis_gaat_mee_als_het_cluster_weg_gaat(db):
    """Achterblijvende geheimen van een cluster dat niet meer bestaat zijn
    onvindbaar en onverwijderbaar — en ze staan er nog wel."""
    from models.cluster_secret import ClusterSecret
    from services.lab.clusters import ClusterService

    class NepDocker:
        async def remove(self, cid, **kw):
            pass

        async def verwijder_netwerk(self, naam):
            pass

    svc = ClusterService(db)
    svc.runtime = NepDocker()
    c = _cluster(db, "Hive")
    _kluis(db).zet(c.id, naam="JWT_SECRET", waarde="x")

    asyncio.run(svc.verwijder(c.id))
    assert db.query(ClusterSecret).count() == 0
