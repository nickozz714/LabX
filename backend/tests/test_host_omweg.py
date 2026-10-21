"""Dezelfde machine, een ander adres.

Een laptop die van huis gaat en via de VPN terugkomt zit thuis op
192.168.2.65 en door de tunnel op 10.13.13.x. `agent_url` volgt dat vanzelf
-- de agent meldt het bronadres waarmee hij LabX bereikt -- maar
`docker_host` niet, want die staat er met de hand in. Zonder omweg draait er
dan geen enkel lab meer op een machine die gewoon bereikbaar is.
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
        cluster_bundel, cluster_env_file, cluster_secret, guard_audit, guard_rule,
        lab, lab_extra, lab_host, lab_secret, lab_worker, mcp_server, message,
        model_runner, notification, plan, schedule, secret, skill, skill_tool,
        thread, time_entry, tool, voice, workflow,
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


NU = "2026-10-21T02:00:00+00:00"


def _machine(db, **kw):
    from models.lab_host import LabHost

    velden = {"machine_id": "m1", "naam": "MacBook", "status": "gekoppeld",
              "docker_host": "ssh://nick@192.168.2.65",
              "agent_url": "http://10.13.13.4:8098",
              "created_at": NU, "updated_at": NU}
    velden.update(kw)
    h = LabHost(**velden)
    db.add(h)
    db.commit()
    return h


def _antwoorden(monkeypatch, werkend):
    """Welke adressen antwoorden; de rest doet dat niet."""
    from services.lab import host_omweg

    async def nep(adres):
        return adres in werkend

    monkeypatch.setattr(host_omweg, "_antwoordt", nep)


def test_het_gemelde_adres_wordt_de_omweg(db, monkeypatch):
    from services.lab import host_omweg

    h = _machine(db)
    _antwoorden(monkeypatch, {"ssh://nick@10.13.13.4"})

    uit = asyncio.run(host_omweg.bepaal(db))

    assert h.docker_host_omweg == "ssh://nick@10.13.13.4"
    assert uit == [{"machine": "MacBook", "omweg": "ssh://nick@10.13.13.4"}]


def test_de_gebruiker_gaat_mee_want_die_verandert_niet(db, monkeypatch):
    from services.lab import host_omweg

    h = _machine(db, docker_host="ssh://nickduchatinier@192.168.2.65")
    _antwoorden(monkeypatch, {"ssh://nickduchatinier@10.13.13.4"})

    asyncio.run(host_omweg.bepaal(db))

    assert h.docker_host_omweg == "ssh://nickduchatinier@10.13.13.4"


def test_het_ingestelde_adres_blijft_staan(db, monkeypatch):
    """Anders is na één keer van huis gaan niet meer te zien wat de
    bedoeling was."""
    from services.lab import host_omweg

    h = _machine(db)
    _antwoorden(monkeypatch, {"ssh://nick@10.13.13.4"})

    asyncio.run(host_omweg.bepaal(db))

    assert h.docker_host == "ssh://nick@192.168.2.65"


def test_werkt_het_ingestelde_adres_weer_dan_gaat_de_omweg_eruit(db, monkeypatch):
    from services.lab import host_omweg

    h = _machine(db, docker_host_omweg="ssh://nick@10.13.13.4")
    _antwoorden(monkeypatch, {"ssh://nick@192.168.2.65"})

    uit = asyncio.run(host_omweg.bepaal(db))

    assert h.docker_host_omweg is None
    assert uit == [{"machine": "MacBook", "omweg": None}]


def test_een_machine_die_nergens_antwoordt_wordt_met_rust_gelaten(db, monkeypatch):
    """Dan valt er niets te kiezen, en "uit" is net zo misleidend als "aan"."""
    from services.lab import host_omweg

    h = _machine(db, docker_host_omweg="ssh://nick@10.13.13.4")
    _antwoorden(monkeypatch, set())

    asyncio.run(host_omweg.bepaal(db))

    assert h.docker_host_omweg == "ssh://nick@10.13.13.4"


def test_zonder_nieuw_adres_gebeurt_er_niets(db, monkeypatch):
    """De agent meldt zich van hetzelfde adres; dan is er geen omweg."""
    from services.lab import host_omweg

    h = _machine(db, agent_url="http://192.168.2.65:8098")
    _antwoorden(monkeypatch, set())

    asyncio.run(host_omweg.bepaal(db))

    assert h.docker_host_omweg is None


def test_de_omweg_wordt_ook_echt_gebruikt(db):
    """Een veld dat nergens gelezen wordt, repareert niets."""
    from services.lab.lab_service import LabService

    h = _machine(db, docker_host_omweg="ssh://nick@10.13.13.4")
    rt = LabService(db)._docker_op_host(h.id)

    assert rt._docker_host == "ssh://nick@10.13.13.4"


def test_de_omweg_staat_in_beeld(db):
    """Een machine die via een ander adres bereikt wordt dan jij hebt
    ingesteld, is iets wat je wilt weten."""
    from services.lab.hosts import LabHostService

    h = _machine(db, docker_host_omweg="ssh://nick@10.13.13.4")

    assert LabHostService(db).to_dict(h)["docker_host_omweg"] == "ssh://nick@10.13.13.4"
