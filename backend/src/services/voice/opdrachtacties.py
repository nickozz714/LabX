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
from services.voice import opdracht, stappen as stappenlaag
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
        invoer = v.get("parameterwaarden") or {}
        met = (" met " + ", ".join(f"{k} is {w}" for k, w in invoer.items())
               if invoer else "")
        zin = (f"Je wilt de planning '{v['naam']}' aanmaken: "
               f"{opdracht.cron_in_woorden(v['wanneer'])}, in lab {lab.name}, "
               f"en dan {v['wat']}{met}. Zal ik hem aanzetten?")
        return Antwoord(bevestiging=Bevestiging(
            tool="maak_planning",
            parameters={"concept_id": concept.id, "lab_id": lab.id},
            zin=zin))

    boom = v.get("stappen") or []
    nodes, _edges = stappenlaag.naar_graaf(boom)
    regels = stappenlaag.in_woorden(boom)
    invoer = [p["naam"] for p in (v.get("parameters") or [])]
    met = f" Hij vraagt om invoer: {', '.join(invoer)}." if invoer else ""
    zin = (f"Je wilt de workflow '{v['naam']}' aanmaken met "
           f"{len(nodes)} activiteit{'en' if len(nodes) != 1 else ''}: "
           f"{' '.join(regels)}.{met} Zal ik hem opslaan?")
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
        # De waarden voor de invoer van die workflow. Zonder dit viel een
        # planning bij de eerste run om op een parameter die niemand zette.
        parameters_json=(v.get("parameterwaarden") or None) if is_workflow else None,
        is_enabled=True, created_at=_nu(), updated_at=_nu())
    db.add(rij)
    concept.status = "afgerond"
    db.commit()
    return (f"De planning {rij.name} staat klaar en is aangezet: "
            f"{opdracht.cron_in_woorden(rij.cron_expression)}.")


async def voer_maak_workflow(db, p: Dict[str, Any]) -> str:
    from models.workflow import Workflow

    concept = _concept(db, p["concept_id"])
    if concept is None or concept.status != "bezig":
        return "Dat concept bestaat niet meer."
    v = dict(concept.velden or {})

    from services.workflows import graph

    boom = v.get("stappen") or []
    rauwe_nodes, rauwe_edges = stappenlaag.naar_graaf(boom)

    # Door de normalisatie van de tekenaar heen halen: die vult de velden aan
    # die wij niet noemen (posities, sleutels, herhaalgrenzen), gooit
    # verbindingen weg die nergens heen gaan, en bewaakt de vorm. Zelf de
    # uiteindelijke structuur schrijven zou betekenen dat een uitgesproken
    # workflow bij elke wijziging aan de tekenaar kan breken.
    nodes, edges = graph.normaliseer(rauwe_nodes, rauwe_edges)

    # De platte stappenlijst blijft ook bestaan: die wordt elders gebruikt om
    # een workflow samen te vatten.
    stappen = [{"index": i, "title": n["naam"],
                "instruction": n.get("prompt") or n.get("commando") or n["naam"]}
               for i, n in enumerate(nodes, start=1)]

    # Parameters komen uit de vraag naar invoer, maar ook uit een keuze die
    # naar `invoer.X` verwijst zonder dat die X al bestond.
    from services.workflows import parameters as params
    invoer = params.normaliseer(v.get("parameters") or [])

    rij = Workflow(name=v["naam"], description=v.get("omschrijving"),
                   markdown="", steps_json=stappen,
                   nodes_json=nodes, edges_json=edges, parameters_json=invoer,
                   is_enabled=True, created_at=_nu(), updated_at=_nu())
    db.add(rij)
    db.flush()

    # Dezelfde controle die de tekenaar toont. Een workflow die je uitspreekt
    # kan best nog een gat hebben -- dat hoor je liever nu dan als hij draait.
    waarschuwingen = graph.valideer(nodes, edges)
    concept.status = "afgerond"
    db.commit()

    staart = (" Let op: " + " ".join(waarschuwingen[:2])) if waarschuwingen else ""
    return (f"De workflow {rij.name} staat klaar met {len(nodes)} activiteiten. "
            f"Je kunt hem openen bij Workbench.{staart}")


OPDRACHT_SCHRIJFACTIES = {"rond_opdracht_af": bereid_rond_opdracht_af}
OPDRACHT_UITVOERDERS = {
    "maak_planning": voer_maak_planning,
    "maak_workflow": voer_maak_workflow,
}
