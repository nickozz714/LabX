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
