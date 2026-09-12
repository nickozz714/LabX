"""
services/lab/intent.py

Intentie: de agent zegt vooraf wát hij ophaalt, en de uitvoer wordt daaraan
getoetst.

**Het probleem dat dit oplost.** De guard beoordeelde een commando op vorm.
`SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS` bevat het woord
SELECT en het woord FROM, dus het leek op het ophalen van rijen — terwijl er
een kolommenlijst uit kwam. Andersom kan een commando er onschuldig uitzien en
toch veertig klantrijen opleveren. Vorm alleen is niet genoeg, en méér regels
op de vorm stapelen maakt het alleen brozer.

**Declare-and-verify.** De agent geeft mee wat de bedoeling is: metadata,
telling, code of klantdata. Dat VERRUIMT wat er op de opdracht mag — een
declaratie "metadata" laat een SELECT op information_schema door. Maar het is
geen vrijbrief, want de uitvoer wordt eraan getoetst: zeg je "metadata" en er
komen veertig klantrijen of een geldig BSN uit, dan gaat het alsnog dicht, en
in de audit staat het als MISMATCH. Liegen kost je dus de uitvoer én laat een
spoor achter — precies andersom dan een systeem waarin een declaratie de
controle uitschakelt.

Niets declareren blijft geldig en verandert niets: dan gelden de regels zoals
ze staan.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# per intentie:
#   label      — wat de gebruiker in de audit ziet
#   verruimt   — categorieën waarvan een blokkeerregel op de OPDRACHT tot een
#                waarschuwing zakt; de uitvoer beslist dan.
#   verbiedt   — wat er in de uitvoer niet mag voorkomen als je dit declareert.
#                Komt het er tóch uit, dan is dat een mismatch en gaat het dicht.
INTENTIES: Dict[str, Dict[str, Any]] = {
    "metadata": {
        "label": "Metadata (structuur, namen, definities)",
        "uitleg": ("Kolomnamen, datatypes, partities, pipeline- en "
                   "notebookdefinities, job-status. Geen waarden uit rijen."),
        "verruimt": ["klantgegevens"],
        "verbiedt": ["klantgegevens", "persoonsgegeven"],
    },
    "telling": {
        "label": "Telling (aantallen, nulls, duplicaten)",
        "uitleg": ("Hoeveel rijen, hoeveel lege waarden, hoeveel unieke. Het "
                   "antwoord is een getal, geen gegeven."),
        "verruimt": ["klantgegevens"],
        "verbiedt": ["klantgegevens", "persoonsgegeven"],
    },
    "code": {
        "label": "Code en configuratie",
        "uitleg": ("Een script schrijven of lezen, een definitie ophalen, een "
                   "log bekijken. Code is geen klantdata."),
        "verruimt": ["klantgegevens"],
        "verbiedt": ["klantgegevens", "persoonsgegeven"],
    },
    "klantdata": {
        "label": "Klantgegevens (bewust)",
        "uitleg": ("Eerlijk verklaard dat hier echte gegevens uit komen. Dit "
                   "verruimt niets — het zorgt ervoor dat de blokkade geen "
                   "verrassing is en herkenbaar in de audit staat."),
        "verruimt": [],
        "verbiedt": ["klantgegevens", "persoonsgegeven"],
    },
}

GELDIG = tuple(INTENTIES)


def normaliseer(waarde: Optional[str]) -> Optional[str]:
    sleutel = (waarde or "").strip().lower()
    return sleutel if sleutel in INTENTIES else None


def verruimt(intent: Optional[str]) -> List[str]:
    spec = INTENTIES.get(intent or "")
    return list(spec["verruimt"]) if spec else []


def toets(intent: Optional[str], findings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Is de uitvoer wat er beloofd was?

    Geeft None als het klopt (of als er niets verklaard is). Anders de
    mismatch, met `blokkeren` erbij: wat de guard al onleesbaar maakt hoeft
    niet óók de hele uitvoer te kosten — maskeren beschermt daar volledig, en
    een heel antwoord weggooien omdat er één e-mailadres in een logregel stond
    is precies de strengheid die het werk in de weg zat. Komen er records uit
    (een blokkeerregel), dan gaat het wél dicht. In beide gevallen staat de
    mismatch in de audit: dát is wat een verklaring controleerbaar maakt.
    """
    spec = INTENTIES.get(intent or "")
    if not spec:
        return None
    verboden = set(spec["verbiedt"])
    geraakt = [f for f in findings
               if f.get("categorie") in verboden
               and f.get("actie") in ("blokkeren", "maskeren")]
    if not geraakt:
        return None
    categorieen = sorted({str(f.get("categorie") or "") for f in geraakt})
    return {
        "intent": intent,
        "verklaard": spec["label"],
        "gevonden": categorieen,
        "regels": sorted({str(f.get("regel") or "") for f in geraakt}),
        "blokkeren": any(f.get("actie") == "blokkeren" for f in geraakt),
        "uitleg": (f"verklaard als '{spec['label']}', maar de uitvoer bevat "
                   + " en ".join(categorieen)),
    }


def streng_op_uitvoer(intent: Optional[str]) -> bool:
    """Is er iets verruimd op de opdracht? Dan kijken we scherper naar wat
    eruit komt. Een verklaring koopt ruimte vooraf en betaalt met controle
    achteraf; dat is de hele afspraak."""
    return bool(verruimt(intent))


def lijst() -> List[Dict[str, Any]]:
    return [{"key": k, "label": v["label"], "uitleg": v["uitleg"]}
            for k, v in INTENTIES.items()]
