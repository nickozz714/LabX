"""Oude workflows omzetten naar activiteiten.

Een workflow was een lijst stappen; nu is het een graaf. Bij lezen en uitvoeren
werd zo'n oude workflow al als rechte keten ingelezen, dus kapot was er niets —
maar dat gebeurde elke keer opnieuw en in het geheugen. Gevolg: de posities op
het doek werden telkens opnieuw verzonnen, en een workflow die je nooit opende
bleef half in de oude wereld hangen.

Deze tests leggen vast dat de omzetting één keer echt gebeurt, dat hij niets
overschrijft wat er al staat, en dat hij een tweede keer draaien niets meer
doet — want hij draait bij élke start van de backend.
"""
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models.workflow import Workflow  # noqa: E402
from services.workflows.migratie import zet_oude_workflows_om  # noqa: E402


@pytest.fixture()
def db():
    from db.database import Base
    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[Workflow.__table__])
    sessie = sessionmaker(bind=motor)()
    yield sessie
    sessie.close()


def _wf(db, **velden):
    basis = dict(name="Oud", description=None, markdown="", steps_json=[],
                 nodes_json=[], edges_json=[], is_enabled=True,
                 created_at="nu", updated_at="nu")
    basis.update(velden)
    rij = Workflow(**basis)
    db.add(rij)
    db.commit()
    return rij


def test_stappen_worden_activiteiten_in_een_rechte_keten(db):
    wf = _wf(db, steps_json=[
        {"index": 1, "title": "Ophalen", "instruction": "haal op"},
        {"index": 2, "title": "Controleren", "instruction": "controleer"},
        {"index": 3, "title": "Melden", "instruction": "meld"},
    ])
    assert zet_oude_workflows_om(db) == 1
    db.refresh(wf)
    assert [n["naam"] for n in wf.nodes_json] == ["Ophalen", "Controleren", "Melden"]
    assert all(n["type"] == "agent" for n in wf.nodes_json)
    assert [n["prompt"] for n in wf.nodes_json] == ["haal op", "controleer", "meld"]
    assert wf.edges_json == [
        {"van": "n1", "naar": "n2", "soort": "succes"},
        {"van": "n2", "naar": "n3", "soort": "succes"},
    ]


def test_de_activiteiten_krijgen_een_eigen_plek_onder_elkaar(db):
    """Zonder opgeslagen posities verzint het doek ze elke keer opnieuw, en kun
    je een workflow niet netjes neerzetten zonder hem eerst te bewerken."""
    wf = _wf(db, steps_json=[{"index": i, "title": f"S{i}", "instruction": "x"}
                             for i in range(1, 4)])
    zet_oude_workflows_om(db)
    db.refresh(wf)
    ys = [n["positie"]["y"] for n in wf.nodes_json]
    assert ys == sorted(ys) and len(set(ys)) == 3, "ze horen onder elkaar te staan"
    assert len({n["positie"]["x"] for n in wf.nodes_json}) == 1


def test_een_workflow_die_al_een_graaf_heeft_blijft_ongemoeid(db):
    """De belangrijkste: dit draait bij elke start van de backend. Zou hij
    overschrijven, dan was je eigen tekening elke herstart weg."""
    eigen = [{"id": "x", "type": "agent", "naam": "Zelf getekend", "prompt": "doe",
              "sleutel": "zelf", "positie": {"x": 500, "y": 500}, "herhaal_max": 25,
              "mag_falen": False}]
    wf = _wf(db, steps_json=[{"index": 1, "title": "Oud", "instruction": "oud"}],
             nodes_json=eigen, edges_json=[])
    assert zet_oude_workflows_om(db) == 0
    db.refresh(wf)
    assert wf.nodes_json == eigen


def test_alleen_markdown_is_ook_genoeg(db):
    """Een workflow die via de API met alleen markdown is aangemaakt: de
    stappen zaten er wel in, ze waren alleen nooit uitgelezen."""
    wf = _wf(db, markdown="## Stap 1 — Ophalen\nhaal op\n\n## Stap 2 — Melden\nmeld\n")
    assert zet_oude_workflows_om(db) == 1
    db.refresh(wf)
    assert [n["naam"] for n in wf.nodes_json] == ["Ophalen", "Melden"]


def test_een_lege_workflow_levert_niets_op(db):
    _wf(db)
    assert zet_oude_workflows_om(db) == 0


def test_twee_keer_draaien_doet_de_tweede_keer_niets(db):
    _wf(db, steps_json=[{"index": 1, "title": "Ophalen", "instruction": "haal op"}])
    assert zet_oude_workflows_om(db) == 1
    assert zet_oude_workflows_om(db) == 0
