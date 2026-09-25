"""Reserveren van het spul waar er maar één van is in een lab.

Sinds een planning meerdere tickets in dezelfde werker kan zetten, zitten er
twee agents tegelijk achter dezelfde sandbox-pc. Het meeste kan naast elkaar;
de browser, een playground of een vaste poort niet. Twee agents in dezelfde
browser levert geen foutmelding op maar een raadsel: je tabblad is weg en je
login verdwenen, en in het verslag staat niets waaruit dat blijkt.

Wat hier vastligt: een claim houdt een ander tegen, dezelfde houder mag hem
gewoon nog eens pakken, een verlopen claim telt niet meer mee, en een resource
die niet bestaat wordt NIET stilzwijgend geaccepteerd — anders reserveert een
typefout niets en denkt de agent dat het geregeld is.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from db.database import Base  # noqa: E402
import models.claim_resource  # noqa: E402,F401  (registreert de tabellen)
from services.lab.resources import ResourceService, seed_builtin_resources  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        models.claim_resource.ClaimResource.__table__,
        models.claim_resource.ResourceClaim.__table__,
    ])
    sessie = sessionmaker(bind=engine)()
    seed_builtin_resources(sessie)
    yield sessie
    sessie.close()


LAB = SimpleNamespace(id="lab-1", claim_resources=None)


def _claim(svc, houder, resource="chrome-browser", worker_id=1, label=None):
    return svc.probeer(lab=LAB, worker_id=worker_id, resource_key=resource,
                       houder=houder, houder_label=label or houder)


# ── de catalogus ────────────────────────────────────────────────────────────

def test_de_meegeleverde_resources_staan_er(db):
    keys = {r.key for r in ResourceService(db).catalogus()}
    assert {"chrome-browser", "playground", "az-sessie"} <= keys


def test_seeden_overschrijft_jouw_aanpassing_niet(db):
    svc = ResourceService(db)
    svc.werk_bij("chrome-browser", timeout_minutes=5, label="Mijn browser")
    seed_builtin_resources(db)
    rij = svc.resource("chrome-browser")
    assert rij.timeout_minutes == 5 and rij.label == "Mijn browser"


def test_zonder_eigen_lijst_gelden_de_standaarden(db):
    """NULL is niet hetzelfde als een lege lijst: een bestaand lab hoort de
    browser te kunnen reserveren zonder dat iemand eerst een vinkje zet."""
    svc = ResourceService(db)
    keys = {r.key for r in svc.voor_lab(SimpleNamespace(id="x", claim_resources=None))}
    assert "chrome-browser" in keys
    assert "poort-8000" not in keys, "die staat standaard uit"


def test_een_lege_lijst_betekent_echt_niets(db):
    svc = ResourceService(db)
    assert svc.voor_lab(SimpleNamespace(id="x", claim_resources=[])) == []


# ── claimen ─────────────────────────────────────────────────────────────────

def test_een_vrije_resource_krijg_je(db):
    res = _claim(ResourceService(db), "sessie-a")
    assert res["ok"] is True and res["resource"] == "chrome-browser"


def test_een_bezette_resource_krijg_je_niet_en_je_hoort_van_wie(db):
    svc = ResourceService(db)
    _claim(svc, "sessie-a", label="SWI-12 (silver laden)")
    res = _claim(svc, "sessie-b")
    assert res["ok"] is False
    assert res["bezet_door"] == "SWI-12 (silver laden)"
    assert res["sinds"]


def test_dezelfde_houder_mag_nog_eens_claimen(db):
    """Een agent weet niet altijd meer of hij hem al had. 'Ja, hij is van jou'
    is dan het juiste antwoord — een fout zou hem onnodig laten wachten."""
    svc = ResourceService(db)
    _claim(svc, "sessie-a")
    res = _claim(svc, "sessie-a")
    assert res["ok"] is True and res["al_van_jou"] is True


def test_een_andere_werker_is_een_andere_browser(db):
    """De browser hangt aan een container. Twee werkers zijn twee containers,
    dus twee browsers — die horen elkaar niet te blokkeren."""
    svc = ResourceService(db)
    assert _claim(svc, "sessie-a", worker_id=1)["ok"] is True
    assert _claim(svc, "sessie-b", worker_id=2)["ok"] is True


def test_een_onbekende_naam_wordt_niet_stil_geaccepteerd(db):
    """Anders reserveert een typefout niets en denkt de agent dat het geregeld
    is — het ergste van beide werelden."""
    res = _claim(ResourceService(db), "sessie-a", resource="chroom-browser")
    assert res["ok"] is False and res["onbekend"] is True
    assert "chrome-browser" in res["melding"]


def test_een_resource_die_niet_voor_dit_lab_geldt_kan_niet(db):
    svc = ResourceService(db)
    lab = SimpleNamespace(id="lab-2", claim_resources=["playground"])
    res = svc.probeer(lab=lab, worker_id=1, resource_key="chrome-browser",
                      houder="sessie-a")
    assert res["ok"] is False and res["onbekend"] is True


# ── vrijgeven en verlopen ───────────────────────────────────────────────────

def test_vrijgeven_maakt_de_weg_vrij(db):
    svc = ResourceService(db)
    _claim(svc, "sessie-a")
    assert svc.release(lab_id="lab-1", houder="sessie-a") == 1
    assert _claim(svc, "sessie-b")["ok"] is True


def test_vrijgeven_zonder_lijst_geeft_alles_van_deze_houder(db):
    svc = ResourceService(db)
    _claim(svc, "sessie-a", resource="chrome-browser")
    _claim(svc, "sessie-a", resource="playground")
    assert svc.release(lab_id="lab-1", houder="sessie-a") == 2


def test_vrijgeven_raakt_de_claim_van_een_ander_niet(db):
    svc = ResourceService(db)
    _claim(svc, "sessie-a", resource="chrome-browser")
    _claim(svc, "sessie-b", resource="playground")
    svc.release(lab_id="lab-1", houder="sessie-a")
    assert [c["resource"] for c in svc.actief("lab-1")] == ["playground"]


def test_een_verlopen_claim_houdt_niemand_meer_tegen(db):
    """Een agent die crasht of afgebroken wordt, geeft niets meer vrij. Zonder
    houdbaarheid staat de browser dan voor altijd op slot."""
    svc = ResourceService(db)
    _claim(svc, "sessie-a")
    rij = db.query(models.claim_resource.ResourceClaim).first()
    rij.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db.commit()
    assert _claim(svc, "sessie-b")["ok"] is True
    assert rij.released_reason == "verlopen"


def test_verlopen_claims_verdwijnen_ook_uit_het_overzicht(db):
    svc = ResourceService(db)
    _claim(svc, "sessie-a")
    rij = db.query(models.claim_resource.ResourceClaim).first()
    rij.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    db.commit()
    assert svc.actief("lab-1") == []
    assert svc.ruim_verlopen_op() == 1


# ── wachten ─────────────────────────────────────────────────────────────────

def test_wachten_op_een_weiger_resource_gebeurt_niet(db):
    """Bij 'weigeren' is meteen antwoord de bedoeling: de agent doet iets
    anders in plaats van vijf minuten stil te staan."""
    svc = ResourceService(db)
    svc.probeer(lab=LAB, worker_id=1, resource_key="poort-8000", houder="sessie-a")
    lab = SimpleNamespace(id="lab-1", claim_resources=["poort-8000"])
    svc.probeer(lab=lab, worker_id=1, resource_key="poort-8000", houder="sessie-a")
    res = asyncio.run(svc.claim(lab=lab, worker_id=1, resource_key="poort-8000",
                                houder="sessie-b", wacht_seconden=300))
    assert res["ok"] is False
    assert "gewacht_seconden" not in res, "hij hoort niet te hebben gewacht"
