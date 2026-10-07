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
from services.voice import stappen as stappenlaag

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
        # Alleen als de gekozen workflow invoer vraagt. Welke dat zijn, weten
        # we pas als "wat" ingevuld is -- vandaar dynamisch.
        {"naam": "parameterwaarden", "dynamisch": True, "vraag": ""},
    ],
    "workflow": [
        {"naam": "naam", "vraag": "Hoe moet de workflow heten?"},
        {"naam": "omschrijving",
         "vraag": "Waar is hij voor? Eén zin is genoeg.", "optioneel": True},
        {"naam": "invoer", "optioneel": True,
         "vraag": "Heeft deze workflow invoer nodig? Noem de velden, of zeg "
                  "'geen'."},
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


def _openstaande_parameter(velden: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """De eerste parameter van de gekozen workflow die nog geen waarde heeft."""
    gevuld = velden.get("parameterwaarden") or {}
    for p in (velden.get("_params") or []):
        if p["naam"] not in gevuld:
            return p
    return None


def openstaand_veld(concept) -> Optional[Dict[str, Any]]:
    """Welke vraag staat er nu open? None betekent: compleet."""
    velden = concept.velden or {}
    for veld in VELDEN[concept.soort]:
        naam = veld["naam"]
        if veld.get("dynamisch"):
            if _openstaande_parameter(velden) is None:
                continue
            return veld
        if veld.get("herhaalt"):
            if stappenlaag.compleet(velden):
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
    if veld.get("dynamisch"):
        p = _openstaande_parameter(dict(concept.velden or {}))
        if p is None:
            return None
        uitleg = f" ({p['omschrijving']})" if p.get("omschrijving") else ""
        return f"Welke waarde moet '{p['naam']}'{uitleg} krijgen?"
    if veld.get("herhaalt"):
        # De stappenlaag weet waar in de boom we zijn: in een tak, in een lus,
        # of gewoon op het hoofdniveau. Die vraag is hier niet na te maken.
        return stappenlaag.vraag(dict(concept.velden or {}))
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

    if veld.get("dynamisch"):
        p = _openstaande_parameter(velden)
        if p is None:
            return {"klaar": True, "concept": samenvatting(db, concept)}
        velden.setdefault("parameterwaarden", {})[p["naam"]] = tekst
    elif naam == "invoer":
        if plat in ("geen", "nee", "niets", "niks", "sla over", "overslaan"):
            velden["invoer_overgeslagen"] = True
        else:
            # "klantnaam en periode" → twee parameters.
            losse = [re.sub(r"[^a-z0-9_]+", "_", w.strip().lower()).strip("_")
                     for w in re.split(r",| en ", tekst) if w.strip()]
            velden["parameters"] = [{"naam": n, "soort": "tekst"}
                                    for n in losse if n]
            velden["invoer"] = ", ".join(n["naam"] for n in velden["parameters"])
    elif veld.get("herhaalt"):
        if plat in KLAAR_WOORDEN and not velden.get(naam) and not velden.get("pad"):
            return {"vraag": "Er is nog geen enkele stap. Wat moet de "
                             "eerste stap doen?"}
        velden = stappenlaag.verwerk(velden, tekst, KLAAR_WOORDEN)
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

    if naam == "wat" and concept.soort == "planning":
        velden["_params"] = _parameters_van_workflow(db, tekst)

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
    # Ook open takken sluiten: wie afrondt terwijl hij nog in een nee-tak zit,
    # bedoelt de hele workflow en niet alleen die tak.
    velden["pad"] = []
    velden["open"] = None
    velden["stappen_klaar"] = True
    concept.velden = velden
    concept.updated_at = _nu()
    flag_modified(concept, "velden")
    db.commit()
    db.refresh(concept)
    return True


def _parameters_van_workflow(db: Session, wat: str) -> List[Dict[str, Any]]:
    """Vraagt de gekozen workflow invoer? Dan moet de planning die meegeven.

    Zonder dit werd een planning aangemaakt voor een workflow met verplichte
    invoer, en viel die bij de eerste run om op een parameter die niemand had
    gezet.
    """
    from models.workflow import Workflow
    from services.voice import opzoeken
    from services.workflows import parameters as params

    try:
        wfs = db.query(Workflow).all()
        treffers = opzoeken.kies_op_naam(wat or "", [w.name for w in wfs])
        if len(treffers) != 1:
            return []
        wf = next(w for w in wfs if w.name == treffers[0])
        return [{"naam": p["naam"], "soort": p["soort"],
                 "omschrijving": p.get("omschrijving") or ""}
                for p in params.normaliseer(wf.parameters_json)]
    except Exception as exc:  # noqa: BLE001
        # Lukt het opzoeken niet, dan gaat de planning gewoon door zonder
        # invoer. Een gesprek laten omvallen op een opzoeking is erger dan
        # een parameter missen -- en dat laatste merk je bij de eerste run.
        log.warningx("Parameters van workflow niet op te halen", error=str(exc)[:200])
        return []


def samenvatting(db: Session, concept) -> Dict[str, Any]:
    """Wat er nu staat, in woorden die je kunt voorlezen."""
    v = dict(concept.velden or {})
    uit: Dict[str, Any] = {"soort": concept.soort, "naam": v.get("naam")}
    if concept.soort == "planning":
        uit.update({
            "lab": v.get("lab"),
            "wanneer": cron_in_woorden(v["wanneer"]) if v.get("wanneer") else None,
            "wat": v.get("wat"),
            "invoer": v.get("parameterwaarden") or None,
        })
    else:
        uit.update({
            "omschrijving": v.get("omschrijving"),
            "stappen": stappenlaag.in_woorden(v.get("stappen") or []),
            "invoer": [p["naam"] for p in (v.get("parameters") or [])] or None,
        })
    uit["nog_te_vragen"] = vraag_nu(concept)
    return uit


def stop(db: Session, concept) -> None:
    concept.status = "afgebroken"
    concept.updated_at = _nu()
    db.commit()
