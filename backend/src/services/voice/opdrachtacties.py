"""De tools waarmee het brein een begeleide opdracht voert.

Vier om het gesprek te voeren (beginnen, aanvullen, tonen, stoppen) en twee om
het af te ronden -- en die laatste twee zijn gewone schrijfacties, dus ze
lopen langs dezelfde bevestigingslus als al het andere. Het verschil is dat de
bevestigingszin hier het HELE concept voorleest, want dat is het moment waarop
je het nog kunt tegenhouden.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from component_logging import get_logger
from services.voice import opdracht
from services.voice.opdracht import _nu
from services.voice.acties import Antwoord, Bevestiging, Context

log = get_logger(__name__)


# ── Het gesprek voeren ───────────────────────────────────────────────────────

def begin_opdracht(ctx: Context, *, soort: str, **_) -> Antwoord:
    """Een planning of workflow opbouwen in meerdere beurten."""
    try:
        concept = opdracht.begin(ctx.db, ctx.session_id, soort)
    except ValueError:
        return Antwoord(vraag="Ik kan een planning of een workflow opbouwen. "
                              "Welke van de twee bedoel je?")
    return Antwoord(feiten={
        "gestart": concept.soort,
        "vraag": opdracht.vraag_nu(concept),
    })


def vul_aan(ctx: Context, *, waarde: str, **_) -> Antwoord:
    """Het antwoord op de vraag die nu openstaat."""
    concept = opdracht.lopend(ctx.db, ctx.session_id)
    if concept is None:
        return Antwoord(vraag="Er loopt geen opdracht. Wil je een planning of "
                              "een workflow opbouwen?")
    uit = opdracht.vul_aan(ctx.db, concept, waarde)
    if uit.get("klaar"):
        return Antwoord(feiten={
            "compleet": True,
            "concept": uit["concept"],
            "aanwijzing": "Alles is ingevuld. Rond af met rond_opdracht_af.",
        })
    return Antwoord(feiten={"vraag": uit.get("vraag"),
                            "concept": uit.get("concept")})


def toon_concept(ctx: Context, **_) -> Antwoord:
    """Wat er tot nu toe staat -- "wat heb ik nu?"."""
    concept = opdracht.lopend(ctx.db, ctx.session_id)
    if concept is None:
        return Antwoord(feiten={"melding": "Er loopt geen opdracht."})
    return Antwoord(feiten=opdracht.samenvatting(ctx.db, concept))


def stop_opdracht(ctx: Context, **_) -> Antwoord:
    """Weggooien wat er is opgebouwd."""
    concept = opdracht.lopend(ctx.db, ctx.session_id)
    if concept is None:
        return Antwoord(feiten={"melding": "Er liep al niets."})
    opdracht.stop(ctx.db, concept)
    return Antwoord(feiten={"melding": "Weggegooid. Er staat niets klaar."})


OPDRACHT_LEESACTIES = {
    "begin_opdracht": begin_opdracht,
    "vul_aan": vul_aan,
    "toon_concept": toon_concept,
    "stop_opdracht": stop_opdracht,
}


# ── Afronden: één bevestiging over het geheel ────────────────────────────────

def bereid_rond_opdracht_af(ctx: Context, **_) -> Antwoord:
    concept = opdracht.lopend(ctx.db, ctx.session_id)
    if concept is None:
        return Antwoord(vraag="Er loopt geen opdracht om af te ronden.")
    # Staat er nog een stappenlijst open met minstens één stap erin, dan is
    # afronden het sein om die te sluiten -- niet om nog een keer te vragen.
    opdracht.sluit_open_lijst(ctx.db, concept)

    open_vraag = opdracht.vraag_nu(concept)
    if open_vraag:
        return Antwoord(vraag=f"Nog niet compleet. {open_vraag}")

    v = dict(concept.velden or {})
    if concept.soort == "planning":
        lab = ctx.lab_van_naam(v.get("lab"))
        if lab is None:
            from models.lab import Lab
            return Antwoord(
                vraag=f"Ik ken geen lab dat '{v.get('lab')}' heet.",
                keuzes=[l.name for l in ctx.db.query(Lab).all() if l.name])
        zin = (f"Je wilt de planning '{v['naam']}' aanmaken: "
               f"{opdracht.cron_in_woorden(v['wanneer'])}, in lab {lab.name}, "
               f"en dan {v['wat']}. Zal ik hem aanzetten?")
        return Antwoord(bevestiging=Bevestiging(
            tool="maak_planning",
            parameters={"concept_id": concept.id, "lab_id": lab.id},
            zin=zin))

    stappen = list(v.get("stappen") or [])
    opsomming = "; ".join(f"{i}. {s}" for i, s in enumerate(stappen, start=1))
    zin = (f"Je wilt de workflow '{v['naam']}' aanmaken met "
           f"{len(stappen)} stap{'pen' if len(stappen) != 1 else ''}: "
           f"{opsomming}. Zal ik hem opslaan?")
    return Antwoord(bevestiging=Bevestiging(
        tool="maak_workflow",
        parameters={"concept_id": concept.id},
        zin=zin))


def _concept(db, concept_id: str):
    from models.voice import VoiceConcept
    return db.get(VoiceConcept, concept_id)


async def voer_maak_planning(db, p: Dict[str, Any]) -> str:
    from models.schedule import Schedule
    from models.workflow import Workflow
    from services.voice import opzoeken

    concept = _concept(db, p["concept_id"])
    if concept is None or concept.status != "bezig":
        return "Dat concept bestaat niet meer."
    v = dict(concept.velden or {})

    # "wat" kan de naam van een workflow zijn of een opdracht in woorden.
    # Eerst kijken of het een workflow is; zo niet, dan is het een prompt.
    wfs = db.query(Workflow).all()
    treffers = opzoeken.kies_op_naam(v.get("wat", ""), [w.name for w in wfs])
    is_workflow = len(treffers) == 1

    rij = Schedule(
        name=v["naam"], cron_expression=v["wanneer"], lab_id=p["lab_id"],
        kind="workflow" if is_workflow else "prompt",
        prompt=None if is_workflow else v["wat"],
        workflow_id=(next(w.id for w in wfs if w.name == treffers[0])
                     if is_workflow else None),
        is_enabled=True, created_at=_nu(), updated_at=_nu())
    db.add(rij)
    concept.status = "afgerond"
    db.commit()
    return (f"De planning {rij.name} staat klaar en is aangezet: "
            f"{opdracht.cron_in_woorden(rij.cron_expression)}.")


async def voer_maak_workflow(db, p: Dict[str, Any]) -> str:
    from models.workflow import Workflow
    from services.workflows import migratie

    concept = _concept(db, p["concept_id"])
    if concept is None or concept.status != "bezig":
        return "Dat concept bestaat niet meer."
    v = dict(concept.velden or {})

    stappen = [{"index": i, "title": _titel(s), "instruction": s}
               for i, s in enumerate(v.get("stappen") or [], start=1)]
    rij = Workflow(name=v["naam"], description=v.get("omschrijving"),
                   markdown="", steps_json=stappen,
                   nodes_json=[], edges_json=[], parameters_json=[],
                   is_enabled=True, created_at=_nu(), updated_at=_nu())
    db.add(rij)
    db.flush()

    # De tekenaar heeft nodes en edges nodig; die laten we opbouwen door
    # dezelfde omzetting die bestaande workflows gebruikt, zodat een workflow
    # die je uitspreekt er precies zo uitziet als een die je tekende.
    nodes, edges = migratie.zet_om(rij)
    if nodes:
        rij.nodes_json, rij.edges_json = nodes, edges

    concept.status = "afgerond"
    db.commit()
    return (f"De workflow {rij.name} staat klaar met {len(stappen)} stappen. "
            f"Je kunt hem openen bij Workbench om hem bij te werken.")


def _titel(instructie: str) -> str:
    """Een korte titel uit de eerste woorden van de stap."""
    woorden = (instructie or "").split()
    kort = " ".join(woorden[:6])
    return (kort[:60] or "Stap").rstrip(".,;:")


OPDRACHT_SCHRIJFACTIES = {"rond_opdracht_af": bereid_rond_opdracht_af}
OPDRACHT_UITVOERDERS = {
    "maak_planning": voer_maak_planning,
    "maak_workflow": voer_maak_workflow,
}
