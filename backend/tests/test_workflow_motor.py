"""Een workflow die LabX zelf uitvoert.

Vroeger gingen alle stappen in één prompt naar het model en was de rest aan
hem. Daar viel niets op te vertakken (tijdens het draaien bestond er geen
stap), niets te herhalen en niets te loggen: een run bewaarde de eindtekst en
verder niets.

Nu voert LabX de activiteiten stuk voor stuk uit. Deze tests leggen vast wat
daarbij niet mag schuiven: dat een bestaande workflow zonder graaf gewoon
blijft draaien, dat een `als` de juiste tak kiest, dat een herhaling per
element één stap oplevert, en dat er van élke activiteit een spoor overblijft
met invoer, uitvoer en verbruik.
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.workflows import engine, expressies, graph  # noqa: E402


# ── expressies ──────────────────────────────────────────────────────────────

CONTEXT = {
    "stap": {
        "controle": {"status": "ok", "uitvoer": "8 rijen geladen",
                     "json": {"rijen": 8, "fouten": [], "tabel": "dbo.klant"}},
        "shell": {"status": "fout", "uitvoer": "boem", "exit_code": 2},
    },
    "invoer": {"omgeving": "acc"},
    "item": {"naam": "klant"},
    "iteratie": 2,
}


@pytest.mark.parametrize("verwijzing,verwacht", [
    ("stap.controle.uitvoer", "8 rijen geladen"),
    ("stap.controle.json.rijen", 8),
    ("stap.controle.json.tabel", "dbo.klant"),
    ("stap.shell.exit_code", 2),
    ("stap.shell.status", "fout"),
    ("invoer.omgeving", "acc"),
    ("item.naam", "klant"),
    ("iteratie", 2),
    ("stap.bestaatniet.json.x", None),
    ("", None),
])
def test_verwijzingen_wijzen_naar_het_juiste(verwijzing, verwacht):
    assert expressies.los_op(verwijzing, CONTEXT) == verwacht


def test_een_verwijzing_die_niets_oplevert_is_geen_fout():
    """De andere tak van een `als` heeft niet gedraaid. Daar naar verwijzen
    hoort een lege waarde te geven, niet de hele run om te gooien."""
    assert expressies.los_op("stap.nooit_gedraaid.uitvoer", CONTEXT) is None


def test_tekst_wordt_ingevuld_en_laat_geen_accolades_achter():
    """Een model dat `{{ stap.x.y }}` te lezen krijgt, gaat erover fantaseren."""
    uit = expressies.vul_in("Er zijn {{ stap.controle.json.rijen }} rijen in "
                            "{{ stap.controle.json.tabel }} ({{ stap.weg.uitvoer }})", CONTEXT)
    assert uit == "Er zijn 8 rijen in dbo.klant ()"


@pytest.mark.parametrize("conditie,verwacht", [
    ({"links": "stap.controle.json.rijen", "operator": ">", "rechts": 0}, True),
    ({"links": "stap.controle.json.rijen", "operator": ">", "rechts": 100}, False),
    ({"links": "stap.controle.json.rijen", "operator": "==", "rechts": "8"}, True),
    ({"links": "stap.controle.json.fouten", "operator": "is_leeg"}, True),
    ({"links": "stap.controle.uitvoer", "operator": "bevat", "rechts": "geladen"}, True),
    ({"links": "stap.controle.uitvoer", "operator": "bevat_niet", "rechts": "mislukt"}, True),
    ({"links": "stap.shell.status", "operator": "==", "rechts": "fout"}, True),
])
def test_voorwaarden(conditie, verwacht):
    assert expressies.evalueer(conditie, CONTEXT) is verwacht


def test_een_onzinnige_voorwaarde_is_niet_waar():
    """Bij twijfel de nee-tak: doorgaan alsof alles goed is, is precies wat je
    niet wilt als je niet weet wat er staat."""
    assert expressies.evalueer(
        {"links": "stap.controle.uitvoer", "operator": ">", "rechts": 5}, CONTEXT) is False
    assert expressies.evalueer({"links": "stap.weg.x", "operator": "==", "rechts": "a"},
                               CONTEXT) is False


# ── de graaf ────────────────────────────────────────────────────────────────

def test_een_workflow_zonder_graaf_wordt_een_rechte_keten():
    """De belofte aan alles wat er al stond: een oude workflow blijft werken en
    niemand hoeft iets opnieuw te tekenen."""
    wf = SimpleNamespace(nodes_json=[], edges_json=[], steps_json=[
        {"index": 1, "title": "Ophalen", "instruction": "haal op"},
        {"index": 2, "title": "Controleren", "instruction": "controleer"},
    ])
    nodes, edges = graph.zorg_voor_graaf(wf)
    assert [n["naam"] for n in nodes] == ["Ophalen", "Controleren"]
    assert all(n["type"] == "agent" for n in nodes)
    assert edges == [{"van": "n1", "naar": "n2", "soort": "succes"}]


def test_de_startactiviteit_is_die_waar_niets_naartoe_wijst():
    nodes, edges = graph.normaliseer(
        [{"id": "a", "naam": "A"}, {"id": "b", "naam": "B"}],
        [{"van": "b", "naar": "a", "soort": "succes"}])
    assert graph.startnode(nodes, edges)["id"] == "b"


def test_altijd_telt_bij_elke_afloop_mee():
    """De verbinding voor 'ruim op' en 'meld het' — juist die moet lopen als er
    iets misging."""
    nodes, edges = graph.normaliseer(
        [{"id": "a", "naam": "A"}, {"id": "ok", "naam": "OK"}, {"id": "altijd", "naam": "Altijd"}],
        [{"van": "a", "naar": "ok", "soort": "succes"},
         {"van": "a", "naar": "altijd", "soort": "altijd"}])
    na_succes = [n["id"] for n in graph.volgende(nodes, edges, "a", "succes")]
    na_fout = [n["id"] for n in graph.volgende(nodes, edges, "a", "fout")]
    assert na_succes == ["ok", "altijd"]
    assert na_fout == ["altijd"]


def test_een_verbinding_naar_een_verwijderde_activiteit_verdwijnt():
    nodes, edges = graph.normaliseer([{"id": "a", "naam": "A"}],
                                     [{"van": "a", "naar": "weg", "soort": "succes"}])
    assert edges == []


def test_validatie_benoemt_wat_er_mist():
    nodes, edges = graph.normaliseer(
        [{"id": "a", "type": "agent", "naam": "Leeg"},
         {"id": "b", "type": "als", "naam": "Check", "conditie": {"links": "stap.a.uitvoer"}}],
        [])
    meldingen = " ".join(graph.valideer(nodes, edges))
    assert "geen opdracht" in meldingen
    assert "ja/nee" in meldingen
    assert "Niet verbonden" in meldingen


# ── de motor ────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    from db.database import Base
    import models.lab  # noqa: F401  (threads verwijzen ernaar)
    import models.thread  # noqa: F401
    import models.workflow  # noqa: F401
    from models.lab import Lab
    from models.thread import Thread
    from models.workflow import Workflow, WorkflowRun, WorkflowRunStep

    motor = create_engine("sqlite://")
    Base.metadata.create_all(motor, tables=[
        Workflow.__table__, WorkflowRun.__table__, WorkflowRunStep.__table__,
        Lab.__table__, Thread.__table__,
    ])
    sessie = sessionmaker(bind=motor)()
    # Elke run hoort bij een lab, en de motor zet dat lab aan als het uit staat.
    # De meeste tests hier gaan niet over het lab, dus dat draait gewoon.
    sessie.add(Lab(id="lab-1", name="Fabric", status="running",
                   image="python:3-bookworm", created_at="nu", updated_at="nu"))
    sessie.commit()
    yield sessie
    sessie.close()


def _workflow(db, nodes, edges):
    from models.workflow import Workflow
    nn, ee = graph.normaliseer(nodes, edges)
    w = Workflow(name="Test", markdown="", steps_json=[], nodes_json=nn, edges_json=ee,
                 is_enabled=True, created_at="nu", updated_at="nu")
    db.add(w)
    db.commit()
    return w


def _draai(db, monkeypatch, workflow, antwoorden):
    """De run helemaal doorlopen, met een agent die vooraf bepaalde antwoorden
    geeft. Zo test dit de MOTOR en niet het model."""
    from db import database
    beurten = []

    async def _nep_beurt(_db, run, node, opdracht):
        beurten.append({"node": node["naam"], "opdracht": opdracht})
        antwoord = antwoorden.get(node["naam"], "klaar")
        if callable(antwoord):
            antwoord = antwoord(len(beurten))
        return (antwoord, [{"kind": "tool", "name": "lab__shell_exec"}],
                {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}, "sessie-1", None)

    monkeypatch.setattr(engine, "_agent_beurt", _nep_beurt)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    run = engine.maak_run(db, workflow, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    db.refresh(run)
    return run, beurten


def _stappen(db, run):
    from models.workflow import WorkflowRunStep
    return (db.query(WorkflowRunStep).filter(WorkflowRunStep.run_id == run.id)
            .order_by(WorkflowRunStep.volgnummer, WorkflowRunStep.id).all())


def test_de_activiteiten_draaien_op_volgorde_en_laten_een_spoor_na(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Ophalen", "prompt": "haal op"},
        {"id": "b", "type": "agent", "naam": "Verwerken", "prompt": "verwerk"},
    ], [{"van": "a", "naar": "b", "soort": "succes"}])
    run, beurten = _draai(db, monkeypatch, wf, {})
    assert run.status == "completed"
    assert [b["node"] for b in beurten] == ["Ophalen", "Verwerken"]
    stappen = _stappen(db, run)
    assert [s.naam for s in stappen] == ["Ophalen", "Verwerken"]
    assert all(s.invoer and s.uitvoer for s in stappen), "invoer én uitvoer horen bewaard"
    assert stappen[0].stappen_json, "de tool-aanroepen zijn de redenatie"
    assert run.totals_json["input_tokens"] == 20


def test_de_uitvoer_van_een_stap_is_bruikbaar_in_de_volgende(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Tellen", "prompt": "tel"},
        {"id": "b", "type": "agent", "naam": "Melden",
         "prompt": "Meld dit: {{ stap.tellen.uitvoer }}"},
    ], [{"van": "a", "naar": "b", "soort": "succes"}])
    _, beurten = _draai(db, monkeypatch, wf, {"Tellen": "42 rijen"})
    assert beurten[1]["opdracht"] == "Meld dit: 42 rijen"


def test_een_als_kiest_de_ja_tak(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Tellen", "prompt": "tel",
         "json_schema": "{}"},
        {"id": "c", "type": "als", "naam": "Genoeg?",
         "conditie": {"links": "stap.tellen.json.rijen", "operator": ">", "rechts": 0}},
        {"id": "ja", "type": "agent", "naam": "Doorgaan", "prompt": "ga door"},
        {"id": "nee", "type": "agent", "naam": "Waarschuwen", "prompt": "waarschuw"},
    ], [{"van": "a", "naar": "c", "soort": "succes"},
        {"van": "c", "naar": "ja", "soort": "ja"},
        {"van": "c", "naar": "nee", "soort": "nee"}])
    run, beurten = _draai(db, monkeypatch, wf, {"Tellen": '{"rijen": 7}'})
    assert [b["node"] for b in beurten] == ["Tellen", "Doorgaan"]
    conditie = [s for s in _stappen(db, run) if s.soort == "als"][0]
    assert conditie.tak == "ja"


def test_een_als_kiest_de_nee_tak(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Tellen", "prompt": "tel", "json_schema": "{}"},
        {"id": "c", "type": "als", "naam": "Genoeg?",
         "conditie": {"links": "stap.tellen.json.rijen", "operator": ">", "rechts": 0}},
        {"id": "ja", "type": "agent", "naam": "Doorgaan", "prompt": "ga door"},
        {"id": "nee", "type": "agent", "naam": "Waarschuwen", "prompt": "waarschuw"},
    ], [{"van": "a", "naar": "c", "soort": "succes"},
        {"van": "c", "naar": "ja", "soort": "ja"},
        {"van": "c", "naar": "nee", "soort": "nee"}])
    _, beurten = _draai(db, monkeypatch, wf, {"Tellen": '{"rijen": 0}'})
    assert [b["node"] for b in beurten] == ["Tellen", "Waarschuwen"]


def test_herhalen_over_een_lijst_geeft_een_stap_per_element(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Lijst", "prompt": "geef lijst",
         "json_schema": "{}"},
        {"id": "b", "type": "agent", "naam": "Verwerk",
         "prompt": "verwerk {{ item }} (ronde {{ iteratie }})",
         "herhaal_over": "stap.lijst.json.tabellen"},
    ], [{"van": "a", "naar": "b", "soort": "succes"}])
    run, beurten = _draai(db, monkeypatch, wf,
                          {"Lijst": '{"tabellen": ["klant", "order", "product"]}'})
    opdrachten = [b["opdracht"] for b in beurten if b["node"] == "Verwerk"]
    assert opdrachten == ["verwerk klant (ronde 1)", "verwerk order (ronde 2)",
                          "verwerk product (ronde 3)"]
    rondes = [s for s in _stappen(db, run) if s.naam == "Verwerk"]
    assert [s.iteratie for s in rondes] == [1, 2, 3]


def test_een_lege_lijst_slaat_de_activiteit_over_en_is_geen_fout(db, monkeypatch):
    """Een lege lijst is vaak juist het goede nieuws ('geen fouten gevonden')."""
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Lijst", "prompt": "geef", "json_schema": "{}"},
        {"id": "b", "type": "agent", "naam": "Verwerk", "prompt": "verwerk {{ item }}",
         "herhaal_over": "stap.lijst.json.fouten"},
        {"id": "c", "type": "agent", "naam": "Afronden", "prompt": "rond af"},
    ], [{"van": "a", "naar": "b", "soort": "succes"},
        {"van": "b", "naar": "c", "soort": "succes"}])
    run, beurten = _draai(db, monkeypatch, wf, {"Lijst": '{"fouten": []}'})
    assert run.status == "completed"
    assert [b["node"] for b in beurten] == ["Lijst", "Afronden"]
    overgeslagen = [s for s in _stappen(db, run) if s.naam == "Verwerk"][0]
    assert overgeslagen.status == "overgeslagen"


def test_herhaal_tot_stopt_zodra_het_klopt(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Proberen", "prompt": "probeer",
         "json_schema": "{}",
         "herhaal_tot": {"links": "stap.proberen.json.klaar", "operator": "==", "rechts": True},
         "herhaal_max": 5},
    ], [])
    run, beurten = _draai(db, monkeypatch, wf, {
        "Proberen": lambda n: '{"klaar": true}' if n >= 3 else '{"klaar": false}'})
    assert len(beurten) == 3, "hij hoort te stoppen zodra de voorwaarde klopt"
    assert run.status == "completed"


def test_herhaal_tot_geeft_het_op_bij_de_bovengrens(db, monkeypatch):
    """Een model dat 'nog niet klaar' blijft zeggen moet ergens stoppen."""
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Proberen", "prompt": "probeer",
         "json_schema": "{}",
         "herhaal_tot": {"links": "stap.proberen.json.klaar", "operator": "==", "rechts": True},
         "herhaal_max": 4},
    ], [])
    _, beurten = _draai(db, monkeypatch, wf, {"Proberen": '{"klaar": false}'})
    assert len(beurten) == 4


def test_een_mislukte_activiteit_stopt_de_run_met_een_reden(db, monkeypatch):
    from db import database

    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Valt om", "prompt": "doe"},
        {"id": "b", "type": "agent", "naam": "Daarna", "prompt": "daarna"},
    ], [{"van": "a", "naar": "b", "soort": "succes"}])

    async def _valt_om(_db, run, node, opdracht):
        return "", [], {}, None, "de agent viel om"

    monkeypatch.setattr(engine, "_agent_beurt", _valt_om)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    run = engine.maak_run(db, wf, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    db.refresh(run)
    assert run.status == "failed"
    assert "Valt om" in (run.error or "")
    assert len(_stappen(db, run)) == 1, "de volgende activiteit hoort niet gedraaid te hebben"


def test_een_foutverbinding_laat_de_run_doorgaan(db, monkeypatch):
    """Precies waarvoor de fout-tak bestaat: opruimen of melden, en dan verder."""
    from db import database

    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Valt om", "prompt": "doe"},
        {"id": "b", "type": "agent", "naam": "Melden", "prompt": "meld"},
    ], [{"van": "a", "naar": "b", "soort": "fout"}])

    gedraaid = []

    async def _soms_fout(_db, run, node, opdracht):
        gedraaid.append(node["naam"])
        if node["naam"] == "Valt om":
            return "", [], {}, None, "boem"
        return "gemeld", [], {}, "s", None

    monkeypatch.setattr(engine, "_agent_beurt", _soms_fout)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    run = engine.maak_run(db, wf, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    db.refresh(run)
    assert gedraaid == ["Valt om", "Melden"]
    assert run.status == "completed"


def test_een_kringetje_loopt_niet_de_hele_nacht(db, monkeypatch):
    """Eén verkeerd getekende verbinding mag geen lab bezet houden tot morgen."""
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Rondje", "prompt": "doe"},
    ], [{"van": "a", "naar": "a", "soort": "succes"}])
    run, beurten = _draai(db, monkeypatch, wf, {})
    assert run.status == "failed"
    assert "kringetje" in (run.error or "")
    assert len(beurten) <= graph.MAX_ACTIVITEITEN_PER_RUN


# ── bubbels: activiteiten die tegelijk draaien ──────────────────────────────

def test_een_bubbel_draait_zijn_activiteiten_tegelijk(db, monkeypatch):
    """De takken mogen niet op elkaar wachten. Draaien ze toch achter elkaar,
    dan is de bubbel alleen een tekening en geen versnelling."""
    from db import database

    wf = _workflow(db, [
        {"id": "b", "type": "parallel", "naam": "Tegelijk", "max_gelijktijdig": 3},
        {"id": "x", "type": "agent", "naam": "Klant", "prompt": "laad klant", "groep": "b"},
        {"id": "y", "type": "agent", "naam": "Order", "prompt": "laad order", "groep": "b"},
        {"id": "z", "type": "agent", "naam": "Product", "prompt": "laad product", "groep": "b"},
        {"id": "na", "type": "agent", "naam": "Samenvatten", "prompt": "vat samen"},
    ], [{"van": "b", "naar": "na", "soort": "succes"}])

    tegelijk, hoogste = 0, 0

    async def _traag(_db, run, node, opdracht):
        nonlocal tegelijk, hoogste
        tegelijk += 1
        hoogste = max(hoogste, tegelijk)
        await asyncio.sleep(0.05)
        tegelijk -= 1
        return f"{node['naam']} klaar", [], {"input_tokens": 1, "output_tokens": 1}, "s", None

    monkeypatch.setattr(engine, "_agent_beurt", _traag)
    monkeypatch.setattr(engine, "_werkers_voor", lambda *a, **kw: [])
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    run = engine.maak_run(db, wf, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    db.refresh(run)
    assert run.status == "completed"
    assert hoogste >= 2, "de takken hoorden tegelijk te lopen"
    namen = [s.naam for s in _stappen(db, run)]
    assert namen[0] == "Tegelijk"
    assert set(namen[1:4]) == {"Klant", "Order", "Product"}
    assert namen[-1] == "Samenvatten"


def test_de_activiteiten_in_een_bubbel_draaien_niet_ook_nog_los(db, monkeypatch):
    """Ze horen bij hun bubbel. Zou de gewone wandeling ze ook oppakken, dan
    draait elke tak twee keer — en dat merk je pas aan een dubbel uitgevoerde
    laadstap."""
    wf = _workflow(db, [
        {"id": "b", "type": "parallel", "naam": "Tegelijk"},
        {"id": "x", "type": "agent", "naam": "Klant", "prompt": "laad", "groep": "b"},
    ], [])
    run, beurten = _draai(db, monkeypatch, wf, {})
    assert [b["node"] for b in beurten] == ["Klant"]


def test_een_tak_krijgt_altijd_een_eigen_sessie(db, monkeypatch):
    """Eén CLI-sessie kan geen twee beurten tegelijk hebben. Een tak die de
    gedeelde sessie zou hervatten, husselt de context van beide door elkaar."""
    from db import database

    wf = _workflow(db, [
        {"id": "b", "type": "parallel", "naam": "Tegelijk"},
        {"id": "x", "type": "agent", "naam": "Een", "prompt": "a", "groep": "b"},
        {"id": "y", "type": "agent", "naam": "Twee", "prompt": "b", "groep": "b"},
    ], [])
    gezien = []

    async def _kijk(_db, run, node, opdracht):
        gezien.append(node.get("verse_sessie"))
        return "ok", [], {}, "s", None

    monkeypatch.setattr(engine, "_agent_beurt", _kijk)
    monkeypatch.setattr(engine, "_werkers_voor", lambda *a, **kw: [])
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    run = engine.maak_run(db, wf, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    assert gezien == [True, True]


def test_de_uitvoer_van_elke_tak_is_daarna_bruikbaar(db, monkeypatch):
    from db import database

    wf = _workflow(db, [
        {"id": "b", "type": "parallel", "naam": "Tegelijk"},
        {"id": "x", "type": "agent", "naam": "Klant", "prompt": "laad", "groep": "b"},
        {"id": "y", "type": "agent", "naam": "Order", "prompt": "laad", "groep": "b"},
        {"id": "na", "type": "agent", "naam": "Samenvatten",
         "prompt": "klant: {{ stap.klant.uitvoer }} / order: {{ stap.order.uitvoer }}"},
    ], [{"van": "b", "naar": "na", "soort": "succes"}])

    async def _antwoord(_db, run, node, opdracht):
        return f"{node['naam'].lower()} ok", [], {}, "s", None

    laatste = {}

    async def _vang(_db, run, node, opdracht):
        laatste[node["naam"]] = opdracht
        return await _antwoord(_db, run, node, opdracht)

    monkeypatch.setattr(engine, "_agent_beurt", _vang)
    monkeypatch.setattr(engine, "_werkers_voor", lambda *a, **kw: [])
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    run = engine.maak_run(db, wf, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    assert laatste["Samenvatten"] == "klant: klant ok / order: order ok"


def test_een_omgevallen_tak_laat_de_bubbel_falen(db, monkeypatch):
    from db import database

    wf = _workflow(db, [
        {"id": "b", "type": "parallel", "naam": "Tegelijk"},
        {"id": "x", "type": "agent", "naam": "Goed", "prompt": "a", "groep": "b"},
        {"id": "y", "type": "agent", "naam": "Fout", "prompt": "b", "groep": "b"},
        {"id": "na", "type": "agent", "naam": "Daarna", "prompt": "c"},
    ], [{"van": "b", "naar": "na", "soort": "succes"}])

    async def _soms(_db, run, node, opdracht):
        if node["naam"] == "Fout":
            return "", [], {}, None, "boem"
        return "ok", [], {}, "s", None

    monkeypatch.setattr(engine, "_agent_beurt", _soms)
    monkeypatch.setattr(engine, "_werkers_voor", lambda *a, **kw: [])
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    run = engine.maak_run(db, wf, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    db.refresh(run)
    assert run.status == "failed"
    assert [s.naam for s in _stappen(db, run) if s.naam == "Daarna"] == []


def test_met_fout_gedrag_doorgaan_loopt_de_bubbel_gewoon_door(db, monkeypatch):
    """Voor een bubbel waar één mislukte tak geen ramp is — de rest is wel
    gedaan, en de volgende stap mag daarover beslissen."""
    from db import database

    wf = _workflow(db, [
        {"id": "b", "type": "parallel", "naam": "Tegelijk", "fout_gedrag": "doorgaan"},
        {"id": "x", "type": "agent", "naam": "Goed", "prompt": "a", "groep": "b"},
        {"id": "y", "type": "agent", "naam": "Fout", "prompt": "b", "groep": "b"},
        {"id": "na", "type": "agent", "naam": "Daarna", "prompt": "c"},
    ], [{"van": "b", "naar": "na", "soort": "succes"}])

    async def _soms(_db, run, node, opdracht):
        if node["naam"] == "Fout":
            return "", [], {}, None, "boem"
        return "ok", [], {}, "s", None

    monkeypatch.setattr(engine, "_agent_beurt", _soms)
    monkeypatch.setattr(engine, "_werkers_voor", lambda *a, **kw: [])
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    run = engine.maak_run(db, wf, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    db.refresh(run)
    assert run.status == "completed"
    assert [s.naam for s in _stappen(db, run) if s.naam == "Daarna"]


def test_validatie_meldt_een_lege_bubbel():
    nodes, edges = graph.normaliseer(
        [{"id": "b", "type": "parallel", "naam": "Leeg"}], [])
    assert any("leeg" in m for m in graph.valideer(nodes, edges))


# ── een slapend lab ─────────────────────────────────────────────────────────
#
# Een lab gaat vanzelf uit als er een tijd niet in gewerkt is, en dat hoort zo.
# Maar dan draait de workflow van vannacht precies niet op het moment waarvoor
# hij bestaat. Dus: de run zet het lab zelf aan.

def _lab(db, status="stopped"):
    """De toestand van het lab uit de fixture zetten."""
    from models.lab import Lab
    rij = db.get(Lab, "lab-1")
    rij.status = status
    db.commit()
    return rij


def _start_nep(monkeypatch, *, faalt=False):
    """LabService.ensure_running vervangen; hij praat anders met docker."""
    import services.lab.lab_service as ls
    aanroepen = []

    class NepService:
        def __init__(self, _db):
            pass

        async def ensure_running(self, lab_id):
            aanroepen.append(lab_id)
            if faalt:
                raise RuntimeError("container weg")
            return {"ok": True, "started": True, "status": "running"}

    monkeypatch.setattr(ls, "LabService", NepService)
    return aanroepen


def test_een_slapend_lab_wordt_gestart_en_dat_staat_in_het_verslag(db, monkeypatch):
    """Zonder die regel in het verslag lijkt de eerste activiteit
    onverklaarbaar lang te duren."""
    _lab(db, status="stopped")
    gestart = _start_nep(monkeypatch)
    wf = _workflow(db, [{"id": "a", "type": "agent", "naam": "Doen", "prompt": "doe"}], [])
    run, beurten = _draai(db, monkeypatch, wf, {})
    assert gestart == ["lab-1"]
    assert run.status == "completed"
    stappen = _stappen(db, run)
    assert stappen[0].naam.startswith("Lab 'Fabric' starten")
    assert stappen[0].status == "ok"
    assert [b["node"] for b in beurten] == ["Doen"]


def test_een_draaiend_lab_levert_geen_extra_regel_op(db, monkeypatch):
    _lab(db, status="running")
    _start_nep(monkeypatch)
    wf = _workflow(db, [{"id": "a", "type": "agent", "naam": "Doen", "prompt": "doe"}], [])
    run, _ = _draai(db, monkeypatch, wf, {})
    assert [s.naam for s in _stappen(db, run)] == ["Doen"]


def test_een_lab_dat_niet_wil_starten_stopt_de_run_met_een_reden(db, monkeypatch):
    _lab(db, status="error")
    _start_nep(monkeypatch, faalt=True)
    wf = _workflow(db, [{"id": "a", "type": "agent", "naam": "Doen", "prompt": "doe"}], [])
    run, beurten = _draai(db, monkeypatch, wf, {})
    assert run.status == "failed"
    assert "niet gestart" in (run.error or "")
    assert beurten == [], "er hoort niets gedraaid te hebben in een lab dat er niet is"


# ── lussen: alles in de lus, één keer per element ───────────────────────────

def test_een_lus_draait_zijn_activiteiten_per_element(db, monkeypatch):
    """Dit is wat `herhaal_over` op één activiteit niet kan: twee activiteiten
    die samen per element draaien."""
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Analyse", "prompt": "analyseer",
         "json_schema": "{}"},
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.analyse.json.incidentGroups"},
        {"id": "x", "type": "agent", "naam": "Bekijken",
         "prompt": "bekijk {{ item.title }}", "groep": "lus"},
        {"id": "y", "type": "agent", "naam": "Melden",
         "prompt": "meld {{ item.title }} (ronde {{ iteratie }})", "groep": "lus"},
        {"id": "na", "type": "agent", "naam": "Afronden", "prompt": "rond af"},
    ], [{"van": "a", "naar": "lus", "soort": "succes"},
        {"van": "x", "naar": "y", "soort": "succes"},
        {"van": "lus", "naar": "na", "soort": "succes"}])

    run, beurten = _draai(db, monkeypatch, wf, {
        "Analyse": '{"incidentGroups": [{"title": "printer"}, {"title": "vpn"}]}'})
    assert run.status == "completed"
    namen = [b["node"] for b in beurten]
    assert namen == ["Analyse", "Bekijken", "Melden", "Bekijken", "Melden", "Afronden"]
    opdrachten = [b["opdracht"] for b in beurten if b["node"] == "Melden"]
    assert opdrachten == ["meld printer (ronde 1)", "meld vpn (ronde 2)"]


def test_een_als_in_een_lus_beslist_per_element(db, monkeypatch):
    """Precies het geval waar dit voor gebouwd is: per incidentgroep kijken of
    hij complex is, en dan iets anders doen."""
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Analyse", "prompt": "x", "json_schema": "{}"},
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.analyse.json.incidentGroups"},
        {"id": "c", "type": "als", "naam": "Complex?", "groep": "lus",
         "conditie": {"links": "item.complexity", "operator": "==", "rechts": "hoog"}},
        {"id": "ja", "type": "agent", "naam": "Uitzoeken", "prompt": "zoek {{ item.title }} uit",
         "groep": "lus"},
        {"id": "nee", "type": "agent", "naam": "Afhandelen", "prompt": "handel {{ item.title }} af",
         "groep": "lus"},
    ], [{"van": "a", "naar": "lus", "soort": "succes"},
        {"van": "c", "naar": "ja", "soort": "ja"},
        {"van": "c", "naar": "nee", "soort": "nee"}])

    run, beurten = _draai(db, monkeypatch, wf, {
        "Analyse": '{"incidentGroups": [{"title": "printer", "complexity": "hoog"},'
                   ' {"title": "vpn", "complexity": "laag"}]}'})
    gedaan = [(b["node"], b["opdracht"]) for b in beurten if b["node"] != "Analyse"]
    assert gedaan == [("Uitzoeken", "zoek printer uit"), ("Afhandelen", "handel vpn af")]
    assert run.status == "completed"


def test_een_lege_lijst_slaat_de_lus_over_en_gaat_door(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Analyse", "prompt": "x", "json_schema": "{}"},
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.analyse.json.incidentGroups"},
        {"id": "x", "type": "agent", "naam": "Bekijken", "prompt": "b", "groep": "lus"},
        {"id": "na", "type": "agent", "naam": "Afronden", "prompt": "rond af"},
    ], [{"van": "a", "naar": "lus", "soort": "succes"},
        {"van": "lus", "naar": "na", "soort": "succes"}])
    run, beurten = _draai(db, monkeypatch, wf, {"Analyse": '{"incidentGroups": []}'})
    assert [b["node"] for b in beurten] == ["Analyse", "Afronden"]
    assert run.status == "completed"


def test_een_ronde_ziet_de_vorige_ronde_niet(db, monkeypatch):
    """Zonder schone context per ronde zou een verwijzing naar een stap in de
    lus stiekem die van de vorige ronde kunnen zijn."""
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Analyse", "prompt": "x", "json_schema": "{}"},
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.analyse.json.lijst"},
        {"id": "x", "type": "agent", "naam": "Eerste", "prompt": "eerste {{ item }}",
         "groep": "lus"},
        {"id": "y", "type": "agent", "naam": "Tweede",
         "prompt": "vorige zei: [{{ stap.eerste.uitvoer }}]", "groep": "lus"},
    ], [{"van": "a", "naar": "lus", "soort": "succes"},
        {"van": "x", "naar": "y", "soort": "succes"}])
    _, beurten = _draai(db, monkeypatch, wf,
                        {"Analyse": '{"lijst": ["een", "twee"]}',
                         "Eerste": "gedaan"})
    tweede = [b["opdracht"] for b in beurten if b["node"] == "Tweede"]
    assert tweede == ["vorige zei: [gedaan]", "vorige zei: [gedaan]"]


def test_de_lus_stopt_bij_een_fout_tenzij_je_doorgaan_kiest(db, monkeypatch):
    from db import database

    def _maak(fout_gedrag):
        wf = _workflow(db, [
            {"id": "a", "type": "agent", "naam": f"Analyse{fout_gedrag}", "prompt": "x",
             "json_schema": "{}"},
            {"id": "lus", "type": "voorelk", "naam": f"Lus{fout_gedrag}",
             "bron": f"stap.analyse{fout_gedrag}.json.lijst", "fout_gedrag": fout_gedrag},
            {"id": "x", "type": "agent", "naam": f"Valt om{fout_gedrag}", "prompt": "p",
             "groep": "lus"},
        ], [{"van": "a", "naar": "lus", "soort": "succes"}])
        return wf

    async def _soms(_db, run, node, opdracht):
        if node["naam"].startswith("Valt om"):
            return "", [], {}, None, "boem"
        return '{"lijst": ["een", "twee", "drie"]}', [], {}, "s", None

    monkeypatch.setattr(engine, "_agent_beurt", _soms)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    wf = _maak("stop")
    run = engine.maak_run(db, wf, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run.id))
    db.refresh(run)
    assert run.status == "failed"

    wf2 = _maak("doorgaan")
    run2 = engine.maak_run(db, wf2, lab_id="lab-1")
    asyncio.run(engine.voer_uit(run2.id))
    db.refresh(run2)
    assert run2.status == "completed"
    rondes = [s for s in _stappen(db, run2) if s.naam == "Valt omdoorgaan"]
    assert len(rondes) == 3, "alle drie de rondes horen geprobeerd te zijn"


# ── het spoor van een lus en een keuze ──────────────────────────────────────
#
# Een run waarin een lus dertien rondes draaide, was in het verslag niet van
# een rechte keten te onderscheiden: elke activiteit stond er los onder, zonder
# ronde en zonder element. En een `als` schreef alleen zijn voorwaarde en
# "nee" op — niet de waarde waarop hij besloot. Daardoor was aan een workflow
# die stil de verkeerde tak nam, niets te zien.

def test_een_activiteit_in_een_lus_weet_in_welke_ronde_hij_zat(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Analyse", "prompt": "x", "json_schema": "{}"},
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.analyse.json.groepen"},
        {"id": "x", "type": "agent", "naam": "Bekijken", "prompt": "b", "groep": "lus"},
    ], [{"van": "a", "naar": "lus", "soort": "succes"}])
    run, _ = _draai(db, monkeypatch, wf, {
        "Analyse": '{"groepen": [{"title": "printer"}, {"title": "vpn"}]}'})
    stappen = [s for s in _stappen(db, run) if s.naam == "Bekijken"]
    assert [s.iteratie for s in stappen] == [1, 2]
    assert [json.loads(s.item)["title"] for s in stappen] == ["printer", "vpn"]


def test_een_als_in_een_lus_legt_vast_waarop_hij_besloot(db, monkeypatch):
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Analyse", "prompt": "x", "json_schema": "{}"},
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.analyse.json.groepen"},
        {"id": "c", "type": "als", "naam": "Simpel?", "groep": "lus",
         "conditie": {"links": "item.complexity", "operator": "==", "rechts": "simpel"}},
    ], [{"van": "a", "naar": "lus", "soort": "succes"}])
    run, _ = _draai(db, monkeypatch, wf, {
        "Analyse": '{"groepen": [{"complexity": "complex"}, {"complexity": "simpel"}]}'})
    keuzes = [s for s in _stappen(db, run) if s.soort == "als"]
    assert [s.tak for s in keuzes] == ["nee", "ja"]
    assert [s.resultaat_json["links_waarde"] for s in keuzes] == ["complex", "simpel"]
    assert [s.iteratie for s in keuzes] == [1, 2]
    # en het staat ook leesbaar in het verslag, niet alleen als json
    assert 'item.complexity → "complex"' in keuzes[0].invoer


def test_de_lus_staat_pas_op_klaar_als_de_rondes_gedraaid_zijn(db, monkeypatch):
    """Stond hij meteen op "ok", dan las het verslag alsof de lus klaar was
    voordat er één ronde was gedraaid — alsof er niet gelust werd."""
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Analyse", "prompt": "x", "json_schema": "{}"},
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.analyse.json.groepen"},
        {"id": "x", "type": "agent", "naam": "Bekijken", "prompt": "b", "groep": "lus"},
    ], [{"van": "a", "naar": "lus", "soort": "succes"}])
    run, _ = _draai(db, monkeypatch, wf, {"Analyse": '{"groepen": [1, 2, 3]}'})
    lus = [s for s in _stappen(db, run) if s.soort == "voorelk"][0]
    assert lus.status == "ok"
    assert lus.uitvoer == "3 ronde(s) gedaan"
    assert lus.resultaat_json["aantal"] == 3


def test_een_lus_zonder_lijst_zegt_waarom_hij_niets_deed(db, monkeypatch):
    """Nul rondes omdat de lijst leeg was, en nul rondes omdat de verwijzing
    niet bestaat, zagen er precies hetzelfde uit."""
    wf = _workflow(db, [
        {"id": "a", "type": "agent", "naam": "Analyse", "prompt": "x", "json_schema": "{}"},
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.analyse.json.incidenten"},
        {"id": "x", "type": "agent", "naam": "Bekijken", "prompt": "b", "groep": "lus"},
    ], [{"van": "a", "naar": "lus", "soort": "succes"}])
    run, _ = _draai(db, monkeypatch, wf, {"Analyse": '{"groepen": [1]}'})
    lus = [s for s in _stappen(db, run) if s.soort == "voorelk"][0]
    assert "leverde niets op" in lus.uitvoer
    assert "groepen" in lus.uitvoer  # wat er wél in zat


# ── de aanhalingstekensval ──────────────────────────────────────────────────

def test_aanhalingstekens_om_een_waarde_worden_genegeerd():
    """`'simpel'` in een schermpje betekent simpel. Vergelijken met de
    letterlijke tekst levert een `nee` waar niets aan te zien is."""
    ctx = {"item": {"complexity": "simpel"}}
    for rechts in ("'simpel'", '"simpel"', "simpel"):
        assert expressies.evalueer(
            {"links": "item.complexity", "operator": "==", "rechts": rechts}, ctx) is True


def test_de_uitleg_noemt_de_velden_die_er_wel_zijn():
    """Een voorwaarde op `item.complexiteit` terwijl het veld `complexity`
    heet, was een stille nee-tak."""
    _, uitleg = expressies.evalueer_uitgelegd(
        {"links": "item.complexiteit", "operator": "==", "rechts": "simpel"},
        {"item": {"complexity": "simpel", "title": "printer"}})
    assert uitleg["links_bestaat"] is False
    assert uitleg["beschikbare_velden"] == ["complexity", "title"]
    assert "wel aanwezig" in expressies.beschrijf_uitkomst(uitleg)
