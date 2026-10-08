"""Schrijfacties van de spraaklaag: eerst voorstellen, dan pas doen.

Elke functie hier bestaat in twee helften.

`bereid_*` lost de gesproken verwijzing op en levert een `Bevestiging`: de
parameters die straks uitgevoerd worden, plus de zin die de gebruiker hoort.
Die zin wordt HIER opgesteld — uit de opgeloste parameters — en niet door het
model. Anders kan het model iets anders voorlezen dan het uitvoert, en zeg je
ja tegen de goede zin met de verkeerde actie als gevolg.

`voer_*` draait pas na een bevestiging, op precies die parameters. Hij zoekt
niets opnieuw op: wat bevestigd is, wordt uitgevoerd.

De zin noemt ook wat de gebruiker niet uitsprak maar wel krijgt — dat het lab
nog gestart moet worden, bijvoorbeeld. Dat zijn de gevolgen waar je ja tegen
zegt zonder ze te noemen.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from services.voice.acties import Antwoord, Bevestiging, Context

log = get_logger(__name__)


def _niet_gevonden(wat: str, alternatieven) -> Antwoord:
    if alternatieven:
        return Antwoord(vraag="Ik vind er meerdere die daarop lijken. Welke bedoel je?",
                        keuzes=alternatieven)
    return Antwoord(vraag=f"Ik kan '{wat}' nergens vinden.")


def _lab_staart(ctx: Context, lab_id: Optional[str]) -> str:
    """"…het lab staat uit, die start ik dan eerst." — het gevolg dat de
    gebruiker niet noemde maar wel krijgt."""
    if not lab_id:
        return ""
    from models.lab import Lab
    lab = ctx.db.get(Lab, lab_id)
    if lab is None:
        return ""
    if lab.status != "running":
        return f" Het lab {lab.name} staat uit, dus die start ik eerst."
    return ""


# ── agent starten op een ticket ─────────────────────────────────────────────

def bereid_start_agent(ctx: Context, *, ticket: str, instructie: Optional[str] = None,
                       klant: Optional[str] = None, **_) -> Antwoord:
    t, alt = ctx.zoek_ticket(ticket, klant)
    if t is None:
        return _niet_gevonden(ticket, alt)

    from models.board import Board
    bord = ctx.db.get(Board, t.board_id)

    if t.agent_state == "running":
        return Antwoord(vraag=f"Op {t.key} draait al een agent. Zal ik die eerst stoppen?")

    zin = (f"Je wilt dat ik een agent start voor {t.key}, "
           f"{(t.title or '')[:70]}, bij {bord.name if bord else 'onbekend'}")
    if instructie:
        zin += f", met de instructie: {instructie}"
    zin += "?" + _lab_staart(ctx, bord.lab_id if bord else None)
    return Antwoord(bevestiging=Bevestiging(
        tool="start_agent",
        parameters={"board_id": t.board_id, "ticket_id": t.id,
                    "sleutel": t.key, "instructie": instructie},
        zin=zin))


async def voer_start_agent(db: Session, p: Dict[str, Any]) -> str:
    from services.boards.agent_work import start_ticket_run
    uit = await start_ticket_run(db, int(p["ticket_id"]),
                                 extra_instruction=p.get("instructie"),
                                 trigger="spraak")
    return f"Agent gestart op {p['sleutel']} (run {str(uit.get('run_id', ''))[:8]})."


# ── ticket aanmaken ─────────────────────────────────────────────────────────

def bereid_maak_ticket(ctx: Context, *, klant: str, titel: str,
                       omschrijving: Optional[str] = None, **_) -> Antwoord:
    bord = ctx.bord_van_naam(klant)
    if bord is None:
        return Antwoord(vraag=f"Ik weet niet welk bord je met '{klant}' bedoelt.",
                        keuzes=[b.name for b in ctx.borden()])
    zin = f"Je wilt een nieuw ticket op {bord.name} met de titel: {titel}"
    if omschrijving:
        zin += f", en als omschrijving: {omschrijving[:200]}"
    zin += "?"
    return Antwoord(bevestiging=Bevestiging(
        tool="maak_ticket",
        parameters={"board_id": bord.id, "titel": titel,
                    "omschrijving": omschrijving, "klant": bord.name},
        zin=zin))


async def voer_maak_ticket(db: Session, p: Dict[str, Any]) -> str:
    from services.boards.board_service import BoardService
    t = BoardService(db).create_ticket(int(p["board_id"]), {
        "title": p["titel"], "description": p.get("omschrijving")}, author="spraak")
    return f"Ticket {t.key} aangemaakt op {p['klant']}."


# ── ticket verplaatsen ──────────────────────────────────────────────────────

def bereid_verplaats_ticket(ctx: Context, *, ticket: str, kolom: str,
                            klant: Optional[str] = None, **_) -> Antwoord:
    t, alt = ctx.zoek_ticket(ticket, klant)
    if t is None:
        return _niet_gevonden(ticket, alt)
    from models.board import Board
    bord = ctx.db.get(Board, t.board_id)
    kolommen = {c.get("key"): c.get("name") for c in (bord.columns or [])} if bord else {}
    doel = kolom
    if kolom not in kolommen:
        from services.voice import opzoeken
        treffers = opzoeken.kies_op_naam(kolom, list(kolommen.values()))
        if len(treffers) != 1:
            return Antwoord(vraag=f"Welke kolom bedoel je met '{kolom}'?",
                            keuzes=list(kolommen.values()))
        doel = next(k for k, v in kolommen.items() if v == treffers[0])
    return Antwoord(bevestiging=Bevestiging(
        tool="verplaats_ticket",
        parameters={"board_id": t.board_id, "ticket_id": t.id,
                    "sleutel": t.key, "kolom": doel},
        zin=f"Je wilt {t.key} verplaatsen naar {kolommen.get(doel, doel)}?"))


async def voer_verplaats_ticket(db: Session, p: Dict[str, Any]) -> str:
    from services.boards.board_service import BoardService
    BoardService(db).move_ticket(int(p["ticket_id"]), p["kolom"], author="spraak")
    return f"{p['sleutel']} staat nu op {p['kolom']}."


# ── opmerking plaatsen ──────────────────────────────────────────────────────

def bereid_plaats_opmerking(ctx: Context, *, ticket: str, tekst: str,
                            klant: Optional[str] = None, **_) -> Antwoord:
    t, alt = ctx.zoek_ticket(ticket, klant)
    if t is None:
        return _niet_gevonden(ticket, alt)
    return Antwoord(bevestiging=Bevestiging(
        tool="plaats_opmerking",
        parameters={"ticket_id": t.id, "sleutel": t.key, "tekst": tekst},
        # De tekst LETTERLIJK in de zin: je moet horen wat er komt te staan,
        # zeker als een transcriptie er iets van gemaakt heeft.
        zin=f"Je wilt bij {t.key} de opmerking plaatsen: {tekst}?"))


async def voer_plaats_opmerking(db: Session, p: Dict[str, Any]) -> str:
    from services.boards.board_service import BoardService
    BoardService(db).add_comment(int(p["ticket_id"]), body=p["tekst"],
                                 author="user", kind="comment")
    return f"Opmerking geplaatst bij {p['sleutel']}."


# ── agent stoppen ───────────────────────────────────────────────────────────

def bereid_stop_agent(ctx: Context, *, ticket: str,
                      klant: Optional[str] = None, **_) -> Antwoord:
    t, alt = ctx.zoek_ticket(ticket, klant)
    if t is None:
        return _niet_gevonden(ticket, alt)
    if t.agent_state != "running":
        return Antwoord(vraag=f"Op {t.key} draait op dit moment geen agent.")
    return Antwoord(bevestiging=Bevestiging(
        tool="stop_agent",
        parameters={"board_id": t.board_id, "ticket_id": t.id, "sleutel": t.key},
        # Stoppen is veilig voor de klantsystemen maar je gooit werk weg —
        # daarom staat dat in de zin.
        zin=f"Je wilt de agent op {t.key} stoppen? Het werk van deze run gaat dan verloren."))


async def voer_stop_agent(db: Session, p: Dict[str, Any]) -> str:
    from services.boards.agent_work import cancel_ticket_run
    await cancel_ticket_run(db, int(p["ticket_id"]))
    return f"Agent op {p['sleutel']} gestopt."


# ── chat starten ────────────────────────────────────────────────────────────

def bereid_start_chat(ctx: Context, *, lab: str, vraag: str, **_) -> Antwoord:
    gekozen = ctx.lab_van_naam(lab)
    if gekozen is None:
        from models.lab import Lab
        return Antwoord(vraag=f"Ik weet niet welk lab je met '{lab}' bedoelt.",
                        keuzes=[l.name for l in ctx.db.query(Lab).all()])
    zin = (f"Je wilt een chat starten op het lab {gekozen.name} met de vraag: {vraag}?"
           + _lab_staart(ctx, gekozen.id))
    return Antwoord(bevestiging=Bevestiging(
        tool="start_chat",
        parameters={"lab_id": gekozen.id, "lab": gekozen.name, "vraag": vraag},
        zin=zin))


async def voer_start_chat(db: Session, p: Dict[str, Any]) -> str:
    from services.agent import background_runs
    from services.chat import threads as thread_service  # noqa: F401
    from models.thread import Thread
    from datetime import datetime, timezone
    from uuid import uuid4

    nu = datetime.now(timezone.utc).isoformat()
    thread = Thread(id=str(uuid4()), lab_id=p["lab_id"], source="chat",
                    title=(p["vraag"][:60] or "Gesproken vraag"),
                    created_at=nu, updated_at=nu)
    db.add(thread)
    db.commit()
    run = background_runs.start(db, thread_id=thread.id, lab_id=p["lab_id"],
                                history=[{"role": "user", "content": p["vraag"]}],
                                prompt=p["vraag"], mode="background")
    return f"Chat gestart op {p['lab']} (taak {run.id[:8]})."


# ── workflow starten ────────────────────────────────────────────────────────

def bereid_start_workflow(ctx: Context, *, workflow: str,
                          lab: Optional[str] = None, **_) -> Antwoord:
    from models.workflow import Workflow
    from services.voice import opzoeken

    wfs = ctx.db.query(Workflow).all()
    treffers = opzoeken.kies_op_naam(workflow, [w.name for w in wfs])
    if len(treffers) != 1:
        return Antwoord(vraag=f"Welke workflow bedoel je met '{workflow}'?",
                        keuzes=[w.name for w in wfs][:10])
    wf = next(w for w in wfs if w.name == treffers[0])

    gekozen = ctx.lab_van_naam(lab) if lab else None
    if lab and gekozen is None:
        from models.lab import Lab
        return Antwoord(vraag=f"Ik weet niet welk lab je met '{lab}' bedoelt.",
                        keuzes=[l.name for l in ctx.db.query(Lab).all()])
    if gekozen is None:
        return Antwoord(vraag=f"Op welk lab moet '{wf.name}' draaien?")

    return Antwoord(bevestiging=Bevestiging(
        tool="start_workflow",
        parameters={"workflow_id": wf.id, "workflow": wf.name,
                    "lab_id": gekozen.id, "lab": gekozen.name},
        zin=(f"Je wilt de workflow {wf.name} uitvoeren op het lab {gekozen.name}?"
             + _lab_staart(ctx, gekozen.id))))


async def voer_start_workflow(db: Session, p: Dict[str, Any]) -> str:
    from models.workflow import Workflow
    from services.workflows.engine import maak_run
    wf = db.get(Workflow, int(p["workflow_id"]))
    if wf is None:
        return f"De workflow {p['workflow']} bestaat niet meer."
    run = maak_run(db, wf, lab_id=p["lab_id"], trigger_type="spraak")
    return f"Workflow {p['workflow']} gestart op {p['lab']} (run {str(run.id)[:8]})."


# ── schedule nu uitvoeren ───────────────────────────────────────────────────

def bereid_voer_schedule_uit(ctx: Context, *, schedule: str, **_) -> Antwoord:
    from models.schedule import Schedule
    from services.voice import opzoeken

    rijen = ctx.db.query(Schedule).all()
    treffers = opzoeken.kies_op_naam(schedule, [s.name for s in rijen])
    if len(treffers) != 1:
        return Antwoord(vraag=f"Welke planning bedoel je met '{schedule}'?",
                        keuzes=[s.name for s in rijen][:10])
    s = next(x for x in rijen if x.name == treffers[0])
    return Antwoord(bevestiging=Bevestiging(
        tool="voer_schedule_uit",
        parameters={"schedule_id": s.id, "naam": s.name},
        zin=f"Je wilt de planning {s.name} nu meteen uitvoeren?"))


async def voer_voer_schedule_uit(db: Session, p: Dict[str, Any]) -> str:
    from services.scheduling.cron import run_now
    # Synchroon en zonder db: hij zet de run zelf weg en geeft een stempel terug.
    run_now(int(p["schedule_id"]))
    return f"Planning {p['naam']} is gestart."


def bereid_wijzig_werkers(ctx: Context, *, lab: str,
                          maximaal: Optional[int] = None,
                          minimaal: Optional[int] = None, **_) -> Antwoord:
    """Meer of minder werkers in een lab.

    Dit kost geheugen en processortijd op de machine waar het lab draait, dus
    het is een schrijfactie met bevestiging -- ook al verandert er niets aan
    klantgegevens. Een lab dat je per ongeluk op twintig werkers zet, legt de
    rest plat.
    """
    from models.lab import Lab

    rij = ctx.lab_van_naam(lab)
    if rij is None:
        return _niet_gevonden(f"een lab dat '{lab}' heet",
                              [l.name for l in ctx.db.query(Lab).all() if l.name])
    if maximaal is None and minimaal is None:
        return Antwoord(vraag="Hoeveel werkers moeten het er worden?")

    nieuw_max = int(maximaal) if maximaal is not None else rij.max_workers
    nieuw_min = int(minimaal) if minimaal is not None else rij.min_workers
    if not (1 <= nieuw_min <= nieuw_max <= 20):
        return Antwoord(vraag=(
            "Dat kan niet: het minimum moet minstens 1 zijn, het maximum niet "
            "boven de 20, en het minimum niet boven het maximum."))

    deel = []
    if minimaal is not None and nieuw_min != rij.min_workers:
        deel.append(f"minimaal {nieuw_min}")
    if maximaal is not None and nieuw_max != rij.max_workers:
        deel.append(f"maximaal {nieuw_max}")
    if not deel:
        return Antwoord(feiten={"melding": f"{rij.name} staat al zo ingesteld."})

    return Antwoord(bevestiging=Bevestiging(
        tool="wijzig_werkers",
        parameters={"lab_id": rij.id, "min": nieuw_min, "max": nieuw_max},
        zin=(f"Je wilt lab {rij.name} op {' en '.join(deel)} werkers zetten? "
             f"Nu is dat minimaal {rij.min_workers} en maximaal {rij.max_workers}.")))


def bereid_wijzig_sessies(ctx: Context, *, lab: str, aantal: int, **_) -> Antwoord:
    """Hoeveel sessies er tegelijk in één werker mogen draaien."""
    from models.lab import Lab

    rij = ctx.lab_van_naam(lab)
    if rij is None:
        return _niet_gevonden(f"een lab dat '{lab}' heet",
                              [l.name for l in ctx.db.query(Lab).all() if l.name])
    getal = int(aantal)
    if not (1 <= getal <= 10):
        return Antwoord(vraag="Dat moet tussen 1 en 10 liggen.")
    if getal == rij.sessies_per_werker:
        return Antwoord(feiten={"melding": f"{rij.name} staat al op {getal}."})

    return Antwoord(bevestiging=Bevestiging(
        tool="wijzig_sessies",
        parameters={"lab_id": rij.id, "aantal": getal},
        zin=(f"Je wilt in lab {rij.name} {getal} sessies tegelijk per werker "
             f"toestaan? Nu staat dat op {rij.sessies_per_werker}.")))


def bereid_zet_schedule(ctx: Context, *, schedule: str, aan: bool, **_) -> Antwoord:
    """Een planning aan- of uitzetten."""
    from models.schedule import Schedule
    from services.voice import opzoeken

    rijen = ctx.db.query(Schedule).all()
    treffers = opzoeken.kies_op_naam(schedule, [s.name for s in rijen])
    if len(treffers) != 1:
        return _niet_gevonden(f"een planning die '{schedule}' heet",
                              [s.name for s in rijen])
    s = next(s for s in rijen if s.name == treffers[0])
    if bool(s.is_enabled) == bool(aan):
        return Antwoord(feiten={
            "melding": f"{s.name} staat al {'aan' if aan else 'uit'}."})

    return Antwoord(bevestiging=Bevestiging(
        tool="zet_schedule",
        parameters={"schedule_id": s.id, "aan": bool(aan)},
        zin=(f"Je wilt de planning {s.name} {'aan' if aan else 'uit'}zetten? "
             f"Die draait op {s.cron_expression}.")))


SCHRIJFACTIES: Dict[str, Callable[..., Antwoord]] = {
    "start_agent": bereid_start_agent,
    "wijzig_werkers": bereid_wijzig_werkers,
    "wijzig_sessies": bereid_wijzig_sessies,
    "zet_schedule": bereid_zet_schedule,
    "maak_ticket": bereid_maak_ticket,
    "verplaats_ticket": bereid_verplaats_ticket,
    "plaats_opmerking": bereid_plaats_opmerking,
    "stop_agent": bereid_stop_agent,
    "start_chat": bereid_start_chat,
    "start_workflow": bereid_start_workflow,
    "voer_schedule_uit": bereid_voer_schedule_uit,
}

async def voer_wijzig_werkers(db: Session, p: Dict[str, Any]) -> str:
    from models.lab import Lab

    rij = db.get(Lab, p["lab_id"])
    if rij is None:
        return "Dat lab bestaat niet meer."
    rij.min_workers, rij.max_workers = int(p["min"]), int(p["max"])
    # Alleen de GRENZEN verzetten, niet het actuele aantal: de autoscaler
    # brengt dat zelf binnen de nieuwe marges, en daar moet je niet mee vechten.
    db.commit()
    return (f"{rij.name} staat nu op minimaal {rij.min_workers} en maximaal "
            f"{rij.max_workers} werkers.")


async def voer_wijzig_sessies(db: Session, p: Dict[str, Any]) -> str:
    from models.lab import Lab

    rij = db.get(Lab, p["lab_id"])
    if rij is None:
        return "Dat lab bestaat niet meer."
    rij.sessies_per_werker = int(p["aantal"])
    db.commit()
    return (f"In {rij.name} mogen nu {rij.sessies_per_werker} sessies tegelijk "
            f"per werker.")


async def voer_zet_schedule(db: Session, p: Dict[str, Any]) -> str:
    from models.schedule import Schedule

    rij = db.get(Schedule, int(p["schedule_id"]))
    if rij is None:
        return "Die planning bestaat niet meer."
    rij.is_enabled = bool(p["aan"])
    db.commit()
    return f"{rij.name} staat nu {'aan' if rij.is_enabled else 'uit'}."


UITVOERDERS: Dict[str, Callable[..., Any]] = {
    "start_agent": voer_start_agent,
    "wijzig_werkers": voer_wijzig_werkers,
    "wijzig_sessies": voer_wijzig_sessies,
    "zet_schedule": voer_zet_schedule,
    "maak_ticket": voer_maak_ticket,
    "verplaats_ticket": voer_verplaats_ticket,
    "plaats_opmerking": voer_plaats_opmerking,
    "stop_agent": voer_stop_agent,
    "start_chat": voer_start_chat,
    "start_workflow": voer_start_workflow,
    "voer_schedule_uit": voer_voer_schedule_uit,
}
