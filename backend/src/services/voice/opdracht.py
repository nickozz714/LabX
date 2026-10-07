"""Begeleide opdrachten: een planning of een workflow in meerdere beurten.

Het verschil met de gewone acties is dat een planning of een workflow te veel
velden heeft om in één zin te zeggen. Je wilt er doorheen gelopen worden.

Twee dingen bepalen de opzet, en ze hangen samen:

1. **Het concept staat op de SERVER**, in `voice_concepten`. Een model dat
   zelf onthoudt wat je gezegd hebt, vult op den duur velden in die nooit
   gevallen zijn -- en bij iets dat straks vanzelf gaat draaien, merk je dat
   pas als het draait.
2. **De server bepaalt welke vraag openstaat**, niet het model. `vul_aan`
   neemt daarom alleen een WAARDE aan en geen veldnaam: wat die waarde
   betekent, weet de kant die de vraag stelde. Zo kan het gesprek niet
   verdwalen en kan het model geen veld overslaan.

Aan het eind is er ÉÉN schrijfactie met één bevestigingszin waarin het hele
concept wordt voorgelezen. Niet per veld bevestigen -- dan klik je na de derde
keer blind ja, en dat is geen bevestiging meer.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from component_logging import get_logger

log = get_logger(__name__)

SOORTEN = ("planning", "workflow")


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Welke vragen er gesteld worden ───────────────────────────────────────────
# De volgorde is de volgorde van het gesprek. `herhaalt` betekent: blijf
# doorvragen tot de gebruiker zegt dat het klaar is.

VELDEN: Dict[str, List[Dict[str, Any]]] = {
    "planning": [
        {"naam": "naam", "vraag": "Hoe moet de planning heten?"},
        {"naam": "lab", "vraag": "In welk lab moet hij draaien?"},
        {"naam": "wanneer",
         "vraag": "Wanneer moet hij draaien? Bijvoorbeeld elke werkdag om "
                  "negen uur, of elk uur."},
        {"naam": "wat",
         "vraag": "Wat moet hij doen? Zeg de opdracht in woorden, of de naam "
                  "van een workflow."},
    ],
    "workflow": [
        {"naam": "naam", "vraag": "Hoe moet de workflow heten?"},
        {"naam": "omschrijving",
         "vraag": "Waar is hij voor? Eén zin is genoeg.", "optioneel": True},
        {"naam": "stappen", "herhaalt": True,
         "vraag": "Wat is de eerste stap? Zeg wat de agent moet doen.",
         "vervolgvraag": "En de volgende stap? Zeg 'klaar' als je er bent."},
    ],
}

# Woorden waarmee je een herhalend veld afsluit.
KLAAR_WOORDEN = {"klaar", "klar", "klaar is kees", "dat was het", "dat is het",
                 "genoeg", "stop", "niets meer", "niks meer", "afronden",
                 "dat was m", "verder niets", "verder niks", "ready", "done"}


# ── Nederlandse tijdsaanduiding naar cron ────────────────────────────────────
_DAGEN = {
    "maandag": "1", "dinsdag": "2", "woensdag": "3", "donderdag": "4",
    "vrijdag": "5", "zaterdag": "6", "zondag": "0",
}
_GETALLEN = {
    "een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6,
    "zeven": 7, "acht": 8, "negen": 9, "tien": 10, "elf": 11, "twaalf": 12,
    "dertien": 13, "veertien": 14, "vijftien": 15, "zestien": 16,
    "zeventien": 17, "achttien": 18, "negentien": 19, "twintig": 20,
    "eenentwintig": 21, "tweeentwintig": 22, "drieentwintig": 23,
}


def _getal(woord: str) -> Optional[int]:
    woord = woord.strip().lower()
    if woord.isdigit():
        return int(woord)
    return _GETALLEN.get(woord)


def naar_cron(tekst: str) -> Optional[str]:
    """"elke werkdag om half negen" → "30 8 * * 1-5".

    Bewust geen volledige taalparser: dit dekt wat je werkelijk zegt tegen een
    planner. Wat er niet in past, levert None op -- en dan vraagt de opdracht
    het gewoon nog een keer met een voorbeeld erbij. Dat is beter dan iets
    aannemelijks verzinnen voor iets dat straks vanzelf gaat draaien.
    """
    t = " " + (tekst or "").lower().strip() + " "

    # Elke N minuten / elk uur.
    m = re.search(r"elke?\s+(\d+)\s*minut", t)
    if m:
        n = int(m.group(1))
        return f"*/{n} * * * *" if 1 <= n <= 59 else None
    if re.search(r"\b(elk uur|ieder uur|elk heel uur)\b", t):
        return "0 * * * *"

    # Het tijdstip.
    uur: Optional[int] = None
    minuut = 0
    m = re.search(r"(\d{1,2})[:.](\d{2})", t)
    if m:
        uur, minuut = int(m.group(1)), int(m.group(2))
    else:
        half = bool(re.search(r"\bhalf\b", t))
        m = re.search(r"\b(?:om|rond)\s+(?:half\s+)?([a-z]+|\d{1,2})\s*(?:uur)?", t)
        if m:
            g = _getal(m.group(1))
            if g is not None:
                # "half negen" is half NEGEN, dus 8:30 -- niet 9:30. Dit is de
                # klassieke valkuil bij Nederlandse tijden.
                uur, minuut = (g - 1, 30) if half else (g, 0)
        if uur is None and re.search(r"\bmiddernacht\b", t):
            uur = 0
        if uur is None and re.search(r"\bmiddag\b", t):
            uur = 12

    if uur is None:
        return None
    # 's avonds/'s middags: een gesproken "acht uur 's avonds" is 20:00.
    if re.search(r"'s avonds|savonds|vanavond", t) and uur < 12:
        uur += 12
    if re.search(r"'s middags|smiddags", t) and uur < 12:
        uur += 12
    if not (0 <= uur <= 23 and 0 <= minuut <= 59):
        return None

    # De dagen.
    if re.search(r"\b(werkdag|werkdagen|doordeweeks)\b", t):
        return f"{minuut} {uur} * * 1-5"
    for dag, nummer in _DAGEN.items():
        if re.search(rf"\b{dag}\b", t):
            return f"{minuut} {uur} * * {nummer}"
    if re.search(r"\b(elke dag|iedere dag|dagelijks|elke ochtend|elke avond)\b", t):
        return f"{minuut} {uur} * * *"
    # Een tijd zonder dag betekent in de praktijk: elke dag.
    return f"{minuut} {uur} * * *"


def cron_in_woorden(cron: str) -> str:
    """Terug naar spreektaal, zodat de bevestigingszin voorleesbaar is."""
    delen = (cron or "").split()
    if len(delen) != 5:
        return cron
    minuut, uur, _dag, _maand, weekdag = delen
    if minuut.startswith("*/"):
        return f"elke {minuut[2:]} minuten"
    if uur == "*":
        return f"elk uur op {minuut} over"
    tijd = f"{int(uur):02d}:{int(minuut):02d}"
    if weekdag == "1-5":
        return f"elke werkdag om {tijd}"
    if weekdag == "*":
        return f"elke dag om {tijd}"
    namen = {v: k for k, v in _DAGEN.items()}
    return f"elke {namen.get(weekdag, 'dag')} om {tijd}"


# ── Het concept ──────────────────────────────────────────────────────────────

def lopend(db: Session, session_id: str):
    from models.voice import VoiceConcept

    return (db.query(VoiceConcept)
            .filter(VoiceConcept.session_id == session_id,
                    VoiceConcept.status == "bezig")
            .order_by(VoiceConcept.created_at.desc()).first())


def begin(db: Session, session_id: str, soort: str):
    from models.voice import VoiceConcept

    soort = (soort or "").strip().lower()
    if soort not in SOORTEN:
        raise ValueError("Dat kan ik niet opbouwen.")

    # Eén tegelijk: twee lopende concepten maken "vul aan" dubbelzinnig, en
    # dan weet niemand meer waar een antwoord bij hoort.
    bestaand = lopend(db, session_id)
    if bestaand is not None:
        bestaand.status = "afgebroken"
        bestaand.updated_at = _nu()

    rij = VoiceConcept(id=str(uuid4()), session_id=session_id, soort=soort,
                       velden={}, status="bezig",
                       created_at=_nu(), updated_at=_nu())
    db.add(rij)
    db.commit()
    db.refresh(rij)
    return rij


def openstaand_veld(concept) -> Optional[Dict[str, Any]]:
    """Welke vraag staat er nu open? None betekent: compleet."""
    velden = concept.velden or {}
    for veld in VELDEN[concept.soort]:
        naam = veld["naam"]
        if veld.get("herhaalt"):
            if velden.get(f"{naam}_klaar"):
                continue
            return veld
        if naam in velden:
            continue
        if veld.get("optioneel") and velden.get(f"{naam}_overgeslagen"):
            continue
        return veld
    return None


def vraag_nu(concept) -> Optional[str]:
    veld = openstaand_veld(concept)
    if veld is None:
        return None
    if veld.get("herhaalt") and (concept.velden or {}).get(veld["naam"]):
        return veld.get("vervolgvraag") or veld["vraag"]
    return veld["vraag"]


def vul_aan(db: Session, concept, waarde: str) -> Dict[str, Any]:
    """Het antwoord op de vraag die NU openstaat.

    Geen veldnaam van buiten: wat deze waarde betekent, weet de kant die de
    vraag stelde. Anders kan het model een veld overslaan of er een invullen
    waar nooit naar gevraagd is.
    """
    veld = openstaand_veld(concept)
    if veld is None:
        return {"klaar": True, "concept": samenvatting(db, concept)}

    tekst = (waarde or "").strip()
    # Een DIEPE kopie, en niet dict(): bij een ondiepe kopie is de stappenlijst
    # hetzelfde object als dat in het concept. Je wijzigt hem dan ter plekke,
    # de nieuwe waarde is gelijk aan de oude, SQLAlchemy ziet geen verandering
    # en schrijft niets weg -- de tweede stap verdween daardoor spoorloos.
    velden = copy.deepcopy(dict(concept.velden or {}))
    naam = veld["naam"]
    plat = tekst.lower().strip(" .!?")

    if veld.get("herhaalt"):
        if plat in KLAAR_WOORDEN:
            if not velden.get(naam):
                return {"vraag": "Er is nog geen enkele stap. Wat moet de "
                                 "eerste stap doen?"}
            velden[f"{naam}_klaar"] = True
        else:
            velden.setdefault(naam, []).append(tekst)
    elif veld.get("optioneel") and plat in ("geen", "sla over", "overslaan", "nee"):
        velden[f"{naam}_overgeslagen"] = True
    elif naam == "wanneer":
        cron = naar_cron(tekst)
        if cron is None:
            return {"vraag": "Dat kreeg ik er niet uit. Zeg het bijvoorbeeld "
                             "als 'elke werkdag om negen uur', 'elke dag om "
                             "half acht' of 'elke 15 minuten'."}
        velden[naam] = cron
        velden["wanneer_gezegd"] = tekst
    else:
        if not tekst:
            return {"vraag": veld["vraag"]}
        velden[naam] = tekst

    concept.velden = velden
    concept.updated_at = _nu()
    # Expliciet melden dat het JSON-veld veranderd is. Zonder dit hangt het
    # wegschrijven af van of SQLAlchemy het verschil toevallig ziet, en bij
    # geneste lijsten ziet hij het niet.
    flag_modified(concept, "velden")
    db.commit()
    db.refresh(concept)

    volgende = vraag_nu(concept)
    if volgende is None:
        return {"klaar": True, "concept": samenvatting(db, concept)}
    return {"vraag": volgende, "concept": samenvatting(db, concept)}


def sluit_open_lijst(db: Session, concept) -> bool:
    """Sluit een herhalend veld af als er al iets in staat.

    Zonder dit liep het gesprek dood: je zegt "klaar", de transcriptie maakt
    er "Klar" van of het model geeft het niet door, en afronden antwoordt dan
    "Nog niet compleet. En de volgende stap?" -- waarna het model het opgaf en
    bééérde dat het klaar was. Wie afrondt, bedoelt afronden.
    """
    veld = openstaand_veld(concept)
    if veld is None or not veld.get("herhaalt"):
        return False
    velden = copy.deepcopy(dict(concept.velden or {}))
    if not velden.get(veld["naam"]):
        return False
    velden[f"{veld['naam']}_klaar"] = True
    concept.velden = velden
    concept.updated_at = _nu()
    flag_modified(concept, "velden")
    db.commit()
    db.refresh(concept)
    return True


def samenvatting(db: Session, concept) -> Dict[str, Any]:
    """Wat er nu staat, in woorden die je kunt voorlezen."""
    v = dict(concept.velden or {})
    uit: Dict[str, Any] = {"soort": concept.soort, "naam": v.get("naam")}
    if concept.soort == "planning":
        uit.update({
            "lab": v.get("lab"),
            "wanneer": cron_in_woorden(v["wanneer"]) if v.get("wanneer") else None,
            "wat": v.get("wat"),
        })
    else:
        uit.update({
            "omschrijving": v.get("omschrijving"),
            "stappen": list(v.get("stappen") or []),
        })
    uit["nog_te_vragen"] = vraag_nu(concept)
    return uit


def stop(db: Session, concept) -> None:
    concept.status = "afgebroken"
    concept.updated_at = _nu()
    db.commit()
