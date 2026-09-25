"""Welke verwijzingen er beschikbaar zijn — uit de schema's die er al zijn.

Aanleiding: in een echte workflow stond `stap.stap_2.json.rijen` terwijl het
veld `incidentGroups` heet. Zo'n typefout levert geen foutmelding op maar een
voorwaarde die altijd onwaar is; dat merk je pas als de verkeerde tak loopt.
LabX kent dat schema al, dus hoort het een keuzelijst te zijn.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.workflows import graph, verwijzingen as vw  # noqa: E402

SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "incidentGroups": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "title": {"type": "string"},
                "complexity": {"type": "string", "description": "hoog of laag"},
                "tickets": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "string"}}}},
            }},
        },
        "samenvatting": {"type": "string"},
    },
})


def _graaf(extra_nodes=None, extra_edges=None):
    nodes = [
        {"id": "a", "type": "agent", "naam": "Root Cause analyse", "prompt": "x",
         "json_schema": SCHEMA},
    ] + (extra_nodes or [])
    return graph.normaliseer(nodes, extra_edges or [])


def test_de_velden_uit_een_schema_worden_paden():
    nodes, edges = _graaf([{"id": "b", "type": "als", "naam": "Check"}])
    paden = {v["pad"] for v in vw.beschikbaar(nodes, edges, "b")}
    assert "stap.root_cause_analyse.json.incidentGroups" in paden
    assert "stap.root_cause_analyse.json.samenvatting" in paden
    assert "stap.root_cause_analyse.uitvoer" in paden
    assert "stap.root_cause_analyse.status" in paden


def test_velden_van_een_lijstelement_krijgen_haakjes():
    """Zo is te zien dat je er eerst met een lus langs moet."""
    nodes, edges = _graaf([{"id": "b", "type": "als", "naam": "Check"}])
    paden = {v["pad"] for v in vw.beschikbaar(nodes, edges, "b")}
    assert "stap.root_cause_analyse.json.incidentGroups[].title" in paden


def test_een_activiteit_verwijst_niet_naar_zichzelf():
    nodes, edges = _graaf()
    paden = {v["pad"] for v in vw.beschikbaar(nodes, edges, "a")}
    assert not any(p.startswith("stap.root_cause_analyse") for p in paden)


def test_in_een_lus_staan_item_en_zijn_velden_bovenaan():
    """Precies het geval: per incidentgroep kijken of hij complex is."""
    nodes, edges = _graaf([
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.root_cause_analyse.json.incidentGroups"},
        {"id": "c", "type": "als", "naam": "Complex?", "groep": "lus"},
    ])
    lijst = vw.beschikbaar(nodes, edges, "c")
    paden = [v["pad"] for v in lijst]
    assert paden[0] == "item"
    assert "iteratie" in paden
    assert "item.title" in paden
    assert "item.complexity" in paden
    uitleg = {v["pad"]: v["omschrijving"] for v in lijst}
    assert uitleg["item.complexity"] == "hoog of laag", "de uitleg uit het schema telt mee"


def test_buiten_een_lus_bestaat_item_niet():
    nodes, edges = _graaf([{"id": "b", "type": "als", "naam": "Check"}])
    assert "item" not in {v["pad"] for v in vw.beschikbaar(nodes, edges, "b")}


def test_de_lijsten_om_langs_te_lopen_zijn_de_arrays():
    nodes, _ = _graaf()
    paden = {v["pad"] for v in vw.lijsten(nodes)}
    assert paden == {"stap.root_cause_analyse.json.incidentGroups"}, \
        "een array BINNEN een element is er geen om in de hoofdstroom langs te lopen"


def test_zonder_schema_blijft_er_gewoon_uitvoer_over():
    nodes, edges = graph.normaliseer(
        [{"id": "a", "type": "agent", "naam": "Doen", "prompt": "x"},
         {"id": "b", "type": "als", "naam": "Check"}], [])
    paden = {v["pad"] for v in vw.beschikbaar(nodes, edges, "b")}
    assert paden == {"stap.doen.uitvoer", "stap.doen.status"}


# ── de graaf die NU in het scherm staat ─────────────────────────────────────

def test_de_live_graaf_levert_item_op_ook_als_er_nog_niets_is_opgeslagen():
    """Het geval waar dit voor bestaat: je sleept een `als` in een lus en de
    keuzelijst moet meteen `item` tonen. Rekende hij met de opgeslagen versie,
    dan stond daar nog niets van die lus in — en kreeg je een lijst zonder het
    element waar je juist mee verder wilde."""
    nodes, edges = _graaf([
        {"id": "lus", "type": "voorelk", "naam": "Per groep",
         "bron": "stap.root_cause_analyse.json.incidentGroups"},
        {"id": "c", "type": "als", "naam": "Complex?", "groep": "lus"},
    ])
    paden = [v["pad"] for v in vw.beschikbaar(nodes, edges, "c")]
    assert paden[0] == "item"
    assert "item.complexity" in paden


def test_een_lus_zonder_bron_geeft_in_elk_geval_item_en_iteratie():
    """Je zet de lus neer, sleept er iets in, en kiest de lijst pas daarna. Ook
    dan hoort `item` te bestaan — anders lijkt de lus niet te werken."""
    nodes, edges = _graaf([
        {"id": "lus", "type": "voorelk", "naam": "Per groep"},
        {"id": "c", "type": "agent", "naam": "Doen", "prompt": "x", "groep": "lus"},
    ])
    paden = [v["pad"] for v in vw.beschikbaar(nodes, edges, "c")]
    assert "item" in paden and "iteratie" in paden
