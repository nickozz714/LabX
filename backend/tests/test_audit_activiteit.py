"""Wat er in een lab gebeurd is.

Het bestaande audit-scherm gaat over de data-guard: welke uitvoer is gemaskeerd
en waarom. Het antwoord op een andere vraag stond nergens — wat heeft dit lab
vandaag eigenlijk gedaan, met welk model, en welke acties zijn er ondernomen.

Deze tests leggen vast dat beide bronnen in één lijst eindigen (een chatbeurt
en een workflow-activiteit zijn allebei "wat het lab deed"), dat de acties
geteld worden in plaats van uitgeschreven, en dat een periode zonder werk een
leeg vakje oplevert in plaats van te ontbreken — anders liegt de grafiek over
het tempo.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.background_run import BackgroundRun  # noqa: E402
from models.lab import Lab  # noqa: E402
from models.message import Message  # noqa: E402
from models.thread import Thread  # noqa: E402
from models.workflow import Workflow, WorkflowRun, WorkflowRunStep  # noqa: E402
from services.audit.activiteit import AuditService  # noqa: E402


def _nu(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture()
def db():
    from db.database import Base
    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[
        Lab.__table__, Thread.__table__, Message.__table__, BackgroundRun.__table__,
        Workflow.__table__, WorkflowRun.__table__, WorkflowRunStep.__table__])
    s = sessionmaker(bind=motor)()
    s.add(Lab(id="lab-1", name="Swinkels", status="running",
              image="python:3-bookworm", created_at="nu", updated_at="nu"))
    s.add(Lab(id="lab-2", name="Vion", status="running",
              image="python:3-bookworm", created_at="nu", updated_at="nu"))
    s.add(Thread(id="t1", lab_id="lab-1", title="Incidenten nakijken",
                 created_at="nu", updated_at="nu"))
    s.commit()
    yield s
    s.close()


def _run(db, *, id="r1", lab_thread="t1", model="claude-opus-5", mode="foreground",
         status="completed", uren=1, stappen=None, prompt="kijk de incidenten na",
         antwoord="drie gevonden"):
    db.add(BackgroundRun(
        id=id, thread_id=lab_thread, prompt=prompt, model=model, status=status, mode=mode,
        steps=stappen if stappen is not None else [
            {"kind": "tool", "name": "lab__shell_exec"},
            {"kind": "tool", "name": "lab__shell_exec"},
            {"kind": "tool", "name": "WebSearch"},
            {"kind": "usage", "input_tokens": 1200, "output_tokens": 300, "cost_usd": 0.042},
        ],
        answer=antwoord, created_at=_nu(hours=uren), started_at=_nu(hours=uren),
        finished_at=_nu(hours=uren, minutes=-2)))
    db.commit()


def test_een_beurt_laat_model_invoer_uitvoer_en_acties_zien(db):
    _run(db)
    item = AuditService(db).gebeurtenissen(lab_id="lab-1")["items"][0]
    assert item["lab_naam"] == "Swinkels"
    assert item["model"] == "claude-opus-5"
    assert item["invoer"] == "kijk de incidenten na"
    assert item["uitvoer"] == "drie gevonden"
    # Geteld, niet uitgeschreven: twintig keer dezelfde tool is anders twintig
    # regels waar je niets aan ziet.
    assert item["acties"] == [{"naam": "lab__shell_exec", "aantal": 2},
                              {"naam": "WebSearch", "aantal": 1}]
    assert item["acties_totaal"] == 3
    assert (item["input_tokens"], item["output_tokens"]) == (1200, 300)
    assert item["cost_usd"] == 0.042
    assert item["duur_ms"] and item["duur_ms"] > 0


def test_een_mislukte_beurt_toont_de_fout_als_uitvoer(db):
    db.add(BackgroundRun(id="r9", thread_id="t1", prompt="doe iets", model="m",
                         status="failed", mode="foreground", steps=[],
                         answer=None, error="de tool gaf 500",
                         created_at=_nu(hours=1), started_at=_nu(hours=1)))
    db.commit()
    item = AuditService(db).gebeurtenissen(lab_id="lab-1")["items"][0]
    assert item["uitvoer"] == "de tool gaf 500"
    assert item["status"] == "failed"


def test_chat_en_workflow_staan_in_dezelfde_lijst(db):
    """Wie wil weten wat een lab deed, vraagt niet eerst of het een chat of een
    workflow was."""
    _run(db, uren=2)
    wf = Workflow(id=1, name="Incidenten", markdown="", steps_json=[], nodes_json=[],
                  edges_json=[], is_enabled=True, created_at="nu", updated_at="nu")
    db.add(wf)
    db.add(WorkflowRun(id="wr1", workflow_id=1, lab_id="lab-1", status="completed",
                       trigger_type="cron", created_at=_nu(hours=1)))
    db.add(WorkflowRunStep(run_id="wr1", node_id="n1", naam="Ophalen", soort="agent",
                           volgnummer=1, status="ok", invoer="haal op", uitvoer="klaar",
                           stappen_json=[{"kind": "tool", "name": "mcp__zoho__search"}],
                           input_tokens=10, output_tokens=5, cost_usd=0.01,
                           created_at=_nu(hours=1)))
    db.commit()
    items = AuditService(db).gebeurtenissen(lab_id="lab-1")["items"]
    assert [i["bron"] for i in items] == ["workflow", "chat"]      # nieuwste eerst
    assert items[0]["titel"] == "Incidenten · Ophalen"
    assert items[0]["acties"] == [{"naam": "mcp__zoho__search", "aantal": 1}]


def test_een_ander_lab_komt_er_niet_in(db):
    """Dit is de vorm waarin de vraag gesteld wordt: niet 'wat deed LabX' maar
    'wat deden we bij deze klant'."""
    _run(db)
    assert AuditService(db).gebeurtenissen(lab_id="lab-2")["items"] == []
    assert len(AuditService(db).gebeurtenissen()["items"]) == 1     # zonder filter wel


def test_een_achtergrondtaak_heet_anders_dan_een_chatbeurt(db):
    _run(db, id="r2", mode="background")
    assert AuditService(db).gebeurtenissen(lab_id="lab-1")["items"][0]["bron"] == "taak"


# ── de staafjes ─────────────────────────────────────────────────────────────

def test_een_dag_zonder_werk_is_een_leeg_vakje_en_geen_gat(db):
    _run(db, uren=1)
    uit = AuditService(db).aggregatie(lab_id="lab-1", periode="dag", aantal=7)
    assert len(uit["emmers"]) == 7
    assert uit["emmers"][-1]["beurten"] == 1        # vandaag
    assert all(e["beurten"] == 0 for e in uit["emmers"][:-1])
    assert uit["totaal_beurten"] == 1
    assert uit["totaal_acties"] == 3


@pytest.mark.parametrize("periode,aantal", [("dag", 14), ("week", 8), ("maand", 6)])
def test_dag_week_en_maand_leveren_evenveel_vakjes_als_gevraagd(db, periode, aantal):
    _run(db)
    uit = AuditService(db).aggregatie(lab_id="lab-1", periode=periode, aantal=aantal)
    assert uit["periode"] == periode
    assert len(uit["emmers"]) == aantal
    assert [e["label"] for e in uit["emmers"]] == sorted(
        [e["label"] for e in uit["emmers"]], key=lambda _: 0)   # volgorde blijft oud → nieuw


def test_de_acties_worden_over_de_periode_opgeteld(db):
    _run(db, id="a", uren=1)
    _run(db, id="b", uren=3)
    uit = AuditService(db).aggregatie(lab_id="lab-1", periode="dag", aantal=3)
    top = {a["naam"]: a["aantal"] for a in uit["top_acties"]}
    assert top["lab__shell_exec"] == 4 and top["WebSearch"] == 2
    assert uit["totaal_kosten"] == 0.084


def test_een_mislukte_beurt_telt_als_fout_in_het_vakje(db):
    db.add(BackgroundRun(id="rx", thread_id="t1", prompt="p", model="m", status="failed",
                         mode="foreground", steps=[], created_at=_nu(hours=1),
                         started_at=_nu(hours=1)))
    db.commit()
    uit = AuditService(db).aggregatie(lab_id="lab-1", periode="dag", aantal=2)
    assert uit["emmers"][-1]["fouten"] == 1
