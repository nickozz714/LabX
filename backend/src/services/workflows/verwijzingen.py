"""
services/workflows/verwijzingen.py

Welke `{{ ... }}`-verwijzingen er op een plek beschikbaar zijn.

**Waarom dit bestaat.** In een echte workflow stond:

    conditie: stap.stap_2.json.rijen  bevat  ''

Het veld heette `incidentGroups`. De verwijzing moest uit het hoofd getypt
worden, terwijl LabX het JSON-schema van die stap gewoon kent — daar staan de
veldnamen letterlijk in. Zoiets hoort een keuzelijst te zijn, geen
geheugenspel: een typefout levert namelijk geen foutmelding op maar een
voorwaarde die altijd onwaar is, en dat merk je pas als de verkeerde tak loopt.

Wat hier uitkomt is een vlakke lijst paden met een omschrijving, klaar om in
een dropdown te zetten. Voor een activiteit BINNEN een lus komen `item` en
`iteratie` erbij, met de velden van het element — afgeleid uit het schema van
de stap waar de lus zijn lijst vandaan haalt.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services.workflows import graph

# Dieper dan dit wordt een keuzelijst onleesbaar, en zulke paden typt niemand
# meer over: dan kun je beter de hele tak pakken.
MAX_DIEPTE = 4


def _laad_schema(ruw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(ruw, dict):
        return ruw
    if isinstance(ruw, str) and ruw.strip():
        try:
            geladen = json.loads(ruw)
            return geladen if isinstance(geladen, dict) else None
        except ValueError:
            return None
    return None


def paden_uit_schema(schema: Optional[Dict[str, Any]], prefix: str,
                     diepte: int = 0) -> List[Dict[str, Any]]:
    """Elke veldnaam uit een JSON-schema als pad.

    Een lijst krijgt zijn eigen regel (daar loop je met een lus langs) én, als
    de elementen objecten zijn, de velden daarvan met `[]` ertussen — zodat te
    zien is dat je er eerst langs moet lopen."""
    if not schema or diepte > MAX_DIEPTE:
        return []
    uit: List[Dict[str, Any]] = []
    for naam, veld in (schema.get("properties") or {}).items():
        if not isinstance(veld, dict):
            continue
        soort = veld.get("type")
        pad = f"{prefix}.{naam}"
        omschrijving = (veld.get("description") or "").strip()
        uit.append({"pad": pad, "soort": soort or "onbekend",
                    "omschrijving": omschrijving})
        if soort == "object":
            uit += paden_uit_schema(veld, pad, diepte + 1)
        elif soort == "array":
            items = veld.get("items")
            if isinstance(items, dict) and items.get("type") == "object":
                uit += paden_uit_schema(items, f"{pad}[]", diepte + 1)
    return uit


def _element_schema(nodes: List[Dict[str, Any]], bron: str) -> Optional[Dict[str, Any]]:
    """Het schema van ÉÉN element van de lijst waar een lus langs loopt.

    `bron` ziet eruit als `stap.analyse.json.incidentGroups`; we zoeken die
    stap op, lopen het pad in zijn schema af en pakken `items`."""
    delen = [d for d in str(bron or "").split(".") if d]
    if len(delen) < 3 or delen[0] != "stap" or delen[2] != "json":
        return None
    sleutel, pad = delen[1], delen[3:]
    node = next((n for n in nodes if n.get("sleutel") == sleutel), None)
    if node is None:
        return None
    schema = _laad_schema(node.get("json_schema"))
    for stuk in pad:
        if not schema:
            return None
        veld = (schema.get("properties") or {}).get(stuk)
        if not isinstance(veld, dict):
            return None
        schema = veld if veld.get("type") != "array" else veld.get("items")
    if isinstance(schema, dict) and schema.get("type") == "object":
        return schema
    return None


def beschikbaar(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
                node_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Alles waar je vanaf deze activiteit naar kunt verwijzen.

    Bewust ALLE andere activiteiten en niet alleen de activiteiten die er
    aantoonbaar vóór liggen: in een graaf met vertakkingen is "ervoor" geen
    harde uitspraak, en een keuzelijst die te weinig toont is erger dan een die
    iets te veel toont. Wat er niet gedraaid heeft, levert gewoon een lege
    waarde op (zie expressies.los_op)."""
    uit: List[Dict[str, Any]] = []
    huidig = next((n for n in nodes if n["id"] == node_id), None) if node_id else None

    for n in nodes:
        if node_id and n["id"] == node_id:
            continue
        if n["type"] in ("parallel", "voorelk"):
            continue        # een groep levert zelf geen uitvoer op
        sleutel = n.get("sleutel") or ""
        if not sleutel:
            continue
        uit.append({"pad": f"stap.{sleutel}.uitvoer", "soort": "tekst",
                    "omschrijving": f"wat '{n['naam']}' opleverde"})
        uit.append({"pad": f"stap.{sleutel}.status", "soort": "ok | fout",
                    "omschrijving": f"of '{n['naam']}' lukte"})
        if n["type"] == "shell":
            uit.append({"pad": f"stap.{sleutel}.exit_code", "soort": "getal",
                        "omschrijving": f"afloop van '{n['naam']}'"})
        for veld in paden_uit_schema(_laad_schema(n.get("json_schema")),
                                     f"stap.{sleutel}.json"):
            uit.append({**veld, "omschrijving": (veld["omschrijving"]
                                                 or f"uit '{n['naam']}'")})

    # Zit deze activiteit in een lus? Dan is het element zelf het belangrijkst.
    groep_id = (huidig or {}).get("groep")
    lus = next((n for n in nodes if n["id"] == groep_id and n["type"] == "voorelk"), None)
    if lus is not None:
        item_uit = [{"pad": "item", "soort": "element",
                     "omschrijving": f"het huidige element uit '{lus['naam']}'"},
                    {"pad": "iteratie", "soort": "getal",
                     "omschrijving": "de hoeveelste ronde (1, 2, 3 …)"}]
        element = _element_schema(nodes, lus.get("bron") or "")
        item_uit += paden_uit_schema(element, "item")
        uit = item_uit + uit
    return uit


def lijsten(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Alles waar een lus langs zou kunnen lopen: de array-velden uit de
    schema's van de activiteiten."""
    uit: List[Dict[str, Any]] = []
    for n in nodes:
        if n["type"] in ("parallel", "voorelk"):
            continue
        sleutel = n.get("sleutel") or ""
        for veld in paden_uit_schema(_laad_schema(n.get("json_schema")),
                                     f"stap.{sleutel}.json"):
            if veld["soort"] == "array" and "[]" not in veld["pad"]:
                uit.append({**veld, "omschrijving": (veld["omschrijving"]
                                                     or f"lijst uit '{n['naam']}'")})
    return uit
