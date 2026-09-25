"""Een schedule die zijn workflow met invoer start.

De parameterwaarden staan op de SCHEDULE en niet op de workflow: dezelfde
workflow hoort elke nacht voor een andere klant te kunnen draaien zonder dat je
hem kopieert. Deze tests leggen vast dat die waarden ook echt bij de run
terechtkomen, aangevuld met de standaardwaarden — en dat een verplichte
parameter die niemand invulde de schedule laat mislukken in plaats van hem half
te laten draaien.
"""
import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.board import Board  # noqa: E402
from models.lab import Lab  # noqa: E402
from models.schedule import Schedule, ScheduleRun  # noqa: E402
from models.workflow import Workflow  # noqa: E402
from services.scheduling import cron  # noqa: E402
from services.workflows import parameters as params  # noqa: E402


@pytest.fixture()
def db():
    from db.database import Base
    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[
        Workflow.__table__, Schedule.__table__, ScheduleRun.__table__, Lab.__table__,
        Board.__table__])
    sessie = sessionmaker(bind=motor)()
    sessie.add(Lab(id="lab-1", name="Fabric", status="running",
                   image="python:3-bookworm", created_at="nu", updated_at="nu"))
    sessie.commit()
    yield sessie
    sessie.close()


def _opzet(db, parameters, waarden):
    w = Workflow(name="Incidenten", markdown="", steps_json=[], nodes_json=[], edges_json=[],
                 parameters_json=params.normaliseer(parameters),
                 is_enabled=True, created_at="nu", updated_at="nu")
    db.add(w)
    db.commit()
    s = Schedule(name="Elke nacht", cron_expression="0 2 * * *", lab_id="lab-1",
                 kind="workflow", workflow_id=w.id, parameters_json=waarden,
                 is_enabled=True, created_at="nu", updated_at="nu")
    db.add(s)
    run = ScheduleRun(id="sr-1", schedule_id=1, scheduled_for="nu", status="pending",
                      created_at="nu")
    db.add(run)
    db.commit()
    return s, run


def _vang_de_run(monkeypatch):
    """De motor vervangen, zodat we zien waarmee de run gestart zou worden."""
    gezien = {}

    def _maak_run(_db, workflow, *, lab_id, trigger_type, trigger_ref=None,
                  invoer=None, worker_id=None):
        gezien["invoer"] = invoer
        return type("Run", (), {"id": "wfr-12345678"})()

    from services.workflows import engine
    monkeypatch.setattr(engine, "maak_run", _maak_run)
    monkeypatch.setattr(engine, "start_in_achtergrond", lambda _id: True)
    return gezien


def test_de_schedule_geeft_zijn_waarden_mee_aangevuld_met_de_standaard(db, monkeypatch):
    sched, run = _opzet(db,
                        [{"naam": "klant", "verplicht": True},
                         {"naam": "omgeving", "soort": "keuze", "opties": ["dev", "prd"],
                          "standaard": "prd"}],
                        {"klant": "Vion"})
    gezien = _vang_de_run(monkeypatch)
    asyncio.run(cron._run_agent_schedule(db, sched, run))
    assert gezien["invoer"] == {"klant": "Vion", "omgeving": "prd"}
    assert run.status == "completed"
    assert "klant=Vion" in run.output


def test_twee_schedules_op_dezelfde_workflow_draaien_voor_een_andere_klant(db, monkeypatch):
    """Precies waarvoor dit bestaat: niet de workflow kopiëren per klant."""
    sched, run = _opzet(db, [{"naam": "klant"}], {"klant": "Swinkels"})
    gezien = _vang_de_run(monkeypatch)
    asyncio.run(cron._run_agent_schedule(db, sched, run))
    assert gezien["invoer"] == {"klant": "Swinkels"}

    sched.parameters_json = {"klant": "Vion"}
    db.commit()
    asyncio.run(cron._run_agent_schedule(db, sched, run))
    assert gezien["invoer"] == {"klant": "Vion"}


def test_een_verplichte_parameter_die_niemand_invulde_laat_de_schedule_mislukken(db, monkeypatch):
    """Een nachtelijke run die met een half ingevulde opdracht begint, kost een
    agent-beurt en levert niets op."""
    sched, run = _opzet(db, [{"naam": "klant", "verplicht": True}], None)
    gezien = _vang_de_run(monkeypatch)
    asyncio.run(cron._run_agent_schedule(db, sched, run))
    assert run.status == "failed"
    assert "klant" in run.error
    assert "invoer" not in gezien        # er is niets gestart
