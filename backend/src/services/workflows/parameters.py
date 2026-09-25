"""
services/workflows/parameters.py

De invoer van een workflow: wat je bij het starten meegeeft, en wat elke
activiteit met `{{ invoer.<naam> }}` kan gebruiken.

**Waarom dit er moest komen.** Een workflow die "de incidenten van Swinkels"
ophaalt, staat met die klantnaam in de opdracht van elke activiteit. Voor de
volgende klant kopieer je hem, en daarna heb je twee workflows die uit elkaar
gaan lopen. De motor kende `invoer.x` al — er was alleen geen manier om te
ZEGGEN welke invoer een workflow verwacht, dus ook niets om in te vullen bij
het starten en niets om in een keuzelijst te zetten.

Een parameter is bewust iets kleins: een naam, wat het is, en een
standaardwaarde. Geen expressies, geen afleidingen — dat is precies het soort
veld waar je later niet meer uitkomt.

**Wat er met een ontbrekende waarde gebeurt.** Is hij niet verplicht en niet
ingevuld, dan geldt de standaardwaarde; is hij verplicht en leeg, dan start de
run niet. Halverwege ontdekken dat een opdracht "haal de incidenten op van "
zei, kost een agent-beurt en levert niets op.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

SOORTEN = ("tekst", "getal", "waar/onwaar", "keuze")

# Dezelfde vorm als een sleutel in een verwijzing: `invoer.klant` moet te
# schrijven zijn zonder aanhalingstekens of haakjes.
_NAAM = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def normaliseer(ruw: Any) -> List[Dict[str, Any]]:
    """De parameterlijst zoals hij opgeslagen wordt.

    Een rij zonder bruikbare naam valt weg — niet met een foutmelding, want je
    bent hem aan het typen; hij hoort alleen niet in de opslag terecht te
    komen als een parameter waar nooit naar te verwijzen valt."""
    uit: List[Dict[str, Any]] = []
    gezien = set()
    for rij in (ruw or []):
        if not isinstance(rij, dict):
            continue
        naam = str(rij.get("naam") or "").strip().lower()
        if not _NAAM.match(naam) or naam in gezien:
            continue
        gezien.add(naam)
        soort = str(rij.get("soort") or "tekst")
        if soort not in SOORTEN:
            soort = "tekst"
        opties = [str(o).strip() for o in (rij.get("opties") or []) if str(o).strip()]
        if soort == "keuze" and not opties:
            soort = "tekst"
        uit.append({
            "naam": naam,
            "soort": soort,
            "omschrijving": str(rij.get("omschrijving") or "").strip(),
            "standaard": _als_soort(rij.get("standaard"), soort),
            "verplicht": bool(rij.get("verplicht")),
            "opties": opties if soort == "keuze" else [],
        })
    return uit


def _als_soort(waarde: Any, soort: str) -> Any:
    """Een waarde in de vorm die erbij hoort.

    Een getal dat als tekst binnenkomt is de normaalste zaak (een invoerveld
    geeft tekst), maar een `>`-vergelijking erop werkt dan niet zoals je
    verwacht. Beter hier één keer goedzetten dan verderop overal raden."""
    if waarde is None or waarde == "":
        return None
    if soort == "getal":
        try:
            getal = float(str(waarde).strip().replace(",", "."))
        except (TypeError, ValueError):
            return None
        return int(getal) if getal.is_integer() else getal
    if soort == "waar/onwaar":
        return str(waarde).strip().lower() in ("true", "waar", "ja", "1", "aan")
    return str(waarde)


def waarden(parameters: List[Dict[str, Any]],
            opgegeven: Dict[str, Any] | None) -> Tuple[Dict[str, Any], List[str]]:
    """De invoer van deze run: wat er is opgegeven, aangevuld met de
    standaardwaarden — plus wat er ontbreekt.

    Wat niet als parameter is opgegeven blijft gewoon staan: een run die van
    buiten iets extra's meekrijgt (een ticket, een klant-id uit een koppeling)
    hoort daar niet op te stuiten."""
    gegeven = dict(opgegeven or {})
    uit: Dict[str, Any] = {k: v for k, v in gegeven.items()
                           if k not in {p["naam"] for p in parameters}}
    ontbreekt: List[str] = []
    for p in parameters:
        rauw = gegeven.get(p["naam"])
        leeg = rauw is None or (isinstance(rauw, str) and not rauw.strip())
        waarde = p["standaard"] if leeg else _als_soort(rauw, p["soort"])
        if p["verplicht"] and (waarde is None or waarde == ""):
            ontbreekt.append(p["naam"])
        if waarde is not None:
            uit[p["naam"]] = waarde
    return uit, ontbreekt


def verwijzingen(parameters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """De parameters als keuzelijst, in dezelfde vorm als de andere
    verwijzingen — zodat `invoer.klant` te kiezen is en niet te typen."""
    uit = []
    for p in parameters:
        omschrijving = p["omschrijving"] or "invoer van deze workflow"
        if p["soort"] == "keuze" and p["opties"]:
            omschrijving += f" ({', '.join(p['opties'][:6])})"
        elif p["standaard"] not in (None, ""):
            omschrijving += f" (standaard: {p['standaard']})"
        uit.append({"pad": f"invoer.{p['naam']}", "soort": p["soort"],
                    "omschrijving": omschrijving})
    return uit
