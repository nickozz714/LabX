"""De lab__secret_*-tools moeten bereikbaar zijn.

Dit ging stil mis: de handlers stonden in `_board_tool`, en de verdeler
stuurt daar alleen `board__*` heen. `lab__secret_list` viel dus door naar het
einde en kreeg een 400 met "tool_id of tool_name is verplicht" — een melding
over iets dat wél was meegegeven, bij een tool die gewoon bestaat. Je kon
daardoor niet zien welke geheimen er voor een lab waren.

Ze hoorden daar ook niet thuis: geheimen hebben niets met een bord te maken,
en in `_board_tool` wordt een run zonder bord afgewezen.
"""
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
        audit, azure_profile, background_run, board, claim_resource, guard_audit,
        guard_rule, lab, lab_extra, lab_host, lab_secret, lab_worker, mcp_server,
        message, notification, plan, schedule, secret, skill, skill_tool, thread,
        time_entry, tool, voice, workflow,
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


def _lab(db):
    from models.lab import Lab

    nu = "2026-10-15T10:00:00+00:00"
    p = Lab(id="lab1", name="Swinkels", image="python:3", volume_name="v",
            status="running", cpu_limit=1, mem_limit_mb=512, ttl_hours=4,
            created_at=nu, updated_at=nu)
    db.add(p)
    db.commit()
    return p


def test_geheimen_opsommen_werkt_zonder_bord(db):
    """Een lab zonder bord is het normale geval, en juist daar ging het mis."""
    from routers.internal_router import _secret_tool

    p = _lab(db)
    uit = _secret_tool(db, "lab__secret_list", p.id, {})
    assert "error" not in uit, uit
    assert "result" in uit


def test_een_onbekende_tool_zegt_wat_er_onbekend_is():
    """De oude melding ("tool_id of tool_name is verplicht") ging over iets
    dat wél was meegegeven, en stuurde je dus het verkeerde pad op."""
    from pathlib import Path

    bron = (Path(__file__).resolve().parents[1] / "src" / "routers"
            / "internal_router.py").read_text()
    assert "Onbekende tool:" in bron


def test_de_verdeler_stuurt_geheimen_tools_naar_de_juiste_plek():
    """Zonder deze regel bestaat de tool wel en komt hij nergens aan."""
    from pathlib import Path

    bron = (Path(__file__).resolve().parents[1] / "src" / "routers"
            / "internal_router.py").read_text()
    verdeler = bron.index('startswith("lab__secret_")')
    bord = bron.index('startswith("board__")')
    assert verdeler < bord, "geheimen moeten vóór de board-tak afgevangen worden"
