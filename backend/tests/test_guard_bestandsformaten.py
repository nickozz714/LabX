"""Klantrijen in JSON, XML en CSV -- en het verschil met metadata.

Aanleiding: er zijn klantgegevens langs de guard gekomen. Nameting liet zien
dat de regellaag twee vormen helemaal niet herkende:

1. XML. Daar keek `record_rows` niet naar. Een `<rows><row>...</row></rows>`
   ging er ongehinderd doorheen en werd alleen tegengehouden door het lokale
   model -- dus niet op een machine waar dat model niet draait, en dat is
   precies de situatie waarin het misging.
2. JSON dieper dan een niveau. `{"data":{"items":[ ...rijen... ]}}` is de
   gewone vorm van een API-antwoord, en die werd niet gezien.

Het lastige aan deze formaten is dat dezelfde syntaxis ook metadata draagt:
een schema, een kopregel, een lijst bestandsnamen. Die moeten er juist WEL
door, anders kan niemand meer werken. Het onderscheid dat we aanhouden is
hetzelfde als bij CSV: minstens twee velden die in elke rij terugkomen, en
minstens een veld waarvan de waarden getallen, bedragen of datums zijn. Een
schema noemt typen, geen waarden, en haalt die drempel dus niet.

Deze tests leggen beide kanten vast: wat tegengehouden moet worden, en wat
er doorheen moet blijven gaan.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.data_guard import record_rows  # noqa: E402


# ── Moet herkend worden als klantrijen ───────────────────────────────────────

def test_xml_met_klantrijen_wordt_herkend():
    xml = (
        "<rows>\n"
        "  <row><klant>Jansen Transport BV</klant><omzet>128400.50</omzet>"
        "<regio>Noord</regio></row>\n"
        "  <row><klant>De Groot Logistiek</klant><omzet>98210.00</omzet>"
        "<regio>West</regio></row>\n"
        "  <row><klant>Van Dijk Agro</klant><omzet>45120.75</omzet>"
        "<regio>Oost</regio></row>\n"
        "</rows>\n")
    assert record_rows(xml) >= 3


def test_xml_met_rijen_als_attributen_wordt_herkend():
    xml = (
        '<data>'
        '<row klant="Jansen Transport BV" omzet="128400.50"/>'
        '<row klant="De Groot Logistiek" omzet="98210.00"/>'
        '<row klant="Van Dijk Agro" omzet="45120.75"/>'
        '</data>')
    assert record_rows(xml) >= 3


def test_json_dieper_dan_een_niveau_wordt_herkend():
    """De gewone vorm van een API-antwoord, en precies wat eerder doorglipte."""
    payload = json.dumps({
        "status": "ok",
        "data": {"items": [
            {"naam": "Jansen Transport BV", "omzet": 128400.50},
            {"naam": "De Groot Logistiek", "omzet": 98210.00},
            {"naam": "Van Dijk Agro", "omzet": 45120.75},
        ]},
    })
    assert record_rows(payload) >= 3


def test_csv_met_klantrijen_blijft_herkend():
    csv = ("klant;contact;omzet;regio\n"
           "Jansen Transport BV;m.jansen@x.nl;128400,50;Noord\n"
           "De Groot Logistiek;info@y.nl;98210,00;West\n"
           "Van Dijk Agro;administratie@z.nl;45120,75;Oost\n")
    assert record_rows(csv) >= 3


# ── Moet er juist DOORHEEN: metadata in dezelfde formaten ────────────────────

@pytest.mark.parametrize("naam,tekst", [
    ("xml-schema",
     "<xs:schema><xs:element name='klant' type='xs:string'/>"
     "<xs:element name='omzet' type='xs:decimal'/>"
     "<xs:element name='regio' type='xs:string'/></xs:schema>"),
    ("json-schema", json.dumps({
        "table": "dim_klant",
        "columns": [{"name": "klant_id", "type": "int"},
                    {"name": "klant_naam", "type": "string"},
                    {"name": "omzet", "type": "decimal(18,2)"}],
        "rowCount": 1284})),
    ("csv-kopregel", "klant;contact;omzet;regio\n"),
    ("bestandslijst",
     "part-00000-abc.parquet (10769 bytes)\n"
     "part-00001-def.parquet (10233 bytes)\n"
     "part-00002-ghi.parquet (9981 bytes)\n"),
    ("xml-config",
     "<config><server>bronze-01</server><port>443</port>"
     "<timeout>30</timeout></config>"),
])
def test_metadata_wordt_niet_als_klantdata_gezien(naam, tekst):
    """Zonder dit kan niemand meer werken.

    Een guard die schema's en bestandslijsten tegenhoudt is net zo onbruikbaar
    als een guard die klantrijen doorlaat -- dan gaat hij uit, en dan beschermt
    hij niets meer.
    """
    assert record_rows(tekst) == 0, f"{naam} werd ten onrechte als rijen gezien"


def test_vrije_tekst_is_geen_rijenset():
    tekst = ("De pipeline draaide om 09:12 en is na 4 minuten klaar. "
             "Er zijn geen fouten opgetreden.")
    assert record_rows(tekst) == 0


# ── De korte-aggregaat-uitzondering op de modeldrempel ───────────────────────

def test_een_korte_verdeling_gaat_wel_langs_het_model():
    """De blinde vlek waarvoor het lokale model bestaat, paste niet in de
    ondergrens van 200 tekens en werd daardoor nooit voorgelegd."""
    from services.lab.data_guard_llm import _lijkt_op_aggregaat

    assert _lijkt_op_aggregaat("Noord 128400\nWest 98210\nOost 45120\n")
    assert _lijkt_op_aggregaat("PostNL: 11\nDHL: 7\nDPD: 5")


def test_korte_technische_uitvoer_gaat_niet_langs_het_model():
    """Anders komt het overblokkeren terug dat die ondergrens oploste."""
    from services.lab.data_guard_llm import _lijkt_op_aggregaat

    assert not _lijkt_op_aggregaat("AZ_YES")
    assert not _lijkt_op_aggregaat("check1")
    assert not _lijkt_op_aggregaat("exit 0")


# ── Wat er gebeurt als het lokale model wegvalt ──────────────────────────────

def test_een_uitgevallen_model_is_herkenbaar_als_uitgevallen():
    """Eerder gaf dit None terug, net als "niet gevraagd".

    Daardoor stond er in het auditspoor niets over een controle die helemaal
    niet had plaatsgevonden: je zag "doorgelaten" en nam aan dat het model had
    meegekeken. Dit is hoe dat lek onzichtbaar kon blijven.
    """
    import asyncio

    from services.lab import data_guard_llm as dgl

    tekst = "regel een met wat tekst erin\n" * 20   # ruim boven de ondergrens
    oud = dgl._url
    dgl._url = lambda: "http://localhost:1"          # gegarandeerd onbereikbaar
    try:
        uit = asyncio.run(dgl.llm_second_opinion(tekst))
    finally:
        dgl._url = oud

    assert uit is not None, "een storing mag niet op 'niet gevraagd' lijken"
    assert uit["uitgevoerd"] is False
    assert uit["allowed"] is True, "de regels blijven de vloer"
    assert uit.get("storing")


def test_data_commandos_zijn_herkenbaar_voor_de_terugval():
    """De fail-closed hangt hierop: alleen data-plane gaat dicht."""
    from services.lab.data_guard import classify_command

    assert classify_command("python -c \"import pandas; pandas.read_csv('k.csv')\"") == "data"
    assert classify_command("az group list") == "control"
