"""De acties die de spraaklaag mag uitvoeren.

Dit is de hele machtiging van de assistent. Wat hier niet staat, kan hij niet —
niet omdat een controle het tegenhoudt, maar omdat de functie niet bestaat.
Daarom staan de vijf verboden acties (tickets verwijderen, naar de bron
synchroniseren, labs of borden verwijderen, de kluis, en instellingen of
guard-regels) hier nergens: een afwezigheid is steviger dan een check die je
kunt vergeten.

Twee soorten:

- **Lezen** gaat direct. Dat kan niets kapotmaken, en wachten op bevestiging
  zou de monitoring onbruikbaar traag maken.
- **Schrijven** levert nooit meteen een resultaat op, maar een `Bevestiging`:
  de opgeloste parameters plus de zin die de gebruiker te horen krijgt. Pas
  als die bevestigd is, voert `voer_uit` exact díé parameters uit.

Dat onderscheid zit in de code en niet in een instructie aan het model, want
een instructie kan een model negeren.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from services.voice import opzoeken

log = get_logger(__name__)


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Bevestiging:
    """Een schrijfactie die nog niet gebeurd is.

    `zin` is wat de gebruiker hoort. Die wordt HIER samengesteld, uit de
    parameters die straks ook echt uitgevoerd worden — niet door het model,
    want dan kan er licht zitten tussen waar je ja tegen zegt en wat er
    gebeurt.
    """

    tool: str
    parameters: Dict[str, Any]
    zin: str


@dataclass
class Antwoord:
    """Wat een leesactie teruggeeft.

    `feiten` is voor het model om een zin van te maken; `vraag` is er als er
    doorgevraagd moet worden (nul of meerdere treffers). Nooit allebei.
    """

    feiten: Optional[Dict[str, Any]] = None
    vraag: Optional[str] = None
    bevestiging: Optional[Bevestiging] = None
    keuzes: List[str] = field(default_factory=list)


# ── hulp: dingen opzoeken ───────────────────────────────────────────────────

class Context:
    """Alles wat een actie nodig heeft om zijn werk te doen."""

    def __init__(self, db: Session):
        self.db = db

    # -- borden en tickets --

    def borden(self):
        from models.board import Board
        return self.db.query(Board).all()

    def prefixen(self) -> List[str]:
        return [b.key_prefix for b in self.borden() if b.key_prefix]

    def bord_van_naam(self, naam: Optional[str]):
        """Het bord dat bij deze (gesproken) naam hoort, of None bij twijfel."""
        borden = self.borden()
        namen = [b.name for b in borden]
        treffers = opzoeken.kies_op_naam(naam, namen)
        if len(treffers) == 1:
            return next(b for b in borden if b.name == treffers[0])
        return None

    def zoek_ticket(self, verwijzing: str, bord_naam: Optional[str] = None):
        """Een gesproken verwijzing naar één ticket.

        Geeft terug: (ticket, alternatieven). Precies één treffer → ticket
        gevuld. Nul of meerdere → ticket is None en `alternatieven` vertelt
        waarom, zodat de assistent kan doorvragen in plaats van gokken.
        """
        from models.board import Ticket

        borden = self.borden()
        if bord_naam:
            gekozen = self.bord_van_naam(bord_naam)
            borden = [gekozen] if gekozen else []
            if not borden:
                return None, []

        bord_ids = [b.id for b in borden]
        if not bord_ids:
            return None, []

        # 1. Als er een sleutel in zit, is dat het sterkste signaal.
        sleutel = opzoeken.normaliseer_sleutel(verwijzing, self.prefixen())
        if sleutel:
            t = (self.db.query(Ticket)
                 .filter(Ticket.board_id.in_(bord_ids), Ticket.key == sleutel).first())
            if t is not None:
                return t, []

        # 2. Anders op titel, over de gekozen borden heen.
        rijen = (self.db.query(Ticket)
                 .filter(Ticket.board_id.in_(bord_ids),
                         Ticket.archived_at.is_(None)).all())
        kandidaten = [(t.key, t.title or "") for t in rijen]
        treffers = opzoeken.kies_op_titel(verwijzing, kandidaten)
        if len(treffers) == 1:
            key = treffers[0][0]
            return next((t for t in rijen if t.key == key), None), []
        return None, [f"{k} — {titel}" for k, titel in treffers[:5]]

    # -- labs --

    def lab_van_naam(self, naam: Optional[str]):
        from models.lab import Lab
        labs = self.db.query(Lab).all()
        treffers = opzoeken.kies_op_naam(naam, [l.name for l in labs])
        if len(treffers) == 1:
            return next(l for l in labs if l.name == treffers[0])
        return None


# ── lezen ───────────────────────────────────────────────────────────────────

def wat_loopt_er(ctx: Context, **_) -> Antwoord:
    """Alles wat nu daadwerkelijk draait, over de hele installatie."""
    from models.background_run import BackgroundRun
    from models.board import Board, Ticket
    from models.workflow import WorkflowRun

    borden = {b.id: b.name for b in ctx.db.query(Board).all()}
    tickets = (ctx.db.query(Ticket)
               .filter(Ticket.agent_state.in_(("running", "queued", "waiting"))).all())
    wf = (ctx.db.query(WorkflowRun)
          .filter(WorkflowRun.status.in_(("running", "queued"))).all())
    bg = (ctx.db.query(BackgroundRun)
          .filter(BackgroundRun.status.in_(("running", "queued")),
                  BackgroundRun.mode == "background").all())

    return Antwoord(feiten={
        "tickets_met_agent": [
            {"sleutel": t.key, "klant": borden.get(t.board_id, "?"),
             "stand": t.agent_state, "titel": (t.title or "")[:90]}
            for t in tickets],
        "workflow_runs": [{"id": r.id[:8], "status": r.status} for r in wf],
        "achtergrondtaken": len(bg),
        "totaal_actief": len(tickets) + len(wf) + len(bg),
    })


def ticket_status(ctx: Context, *, ticket: str, klant: Optional[str] = None, **_) -> Antwoord:
    """Hoe het met één ticket staat — de belangrijkste leesactie."""
    from services.voice.samenvatten import run_samenvatting

    t, alternatieven = ctx.zoek_ticket(ticket, klant)
    if t is None:
        if alternatieven:
            return Antwoord(
                vraag=f"Ik vind er meerdere die daarop lijken. Welke bedoel je?",
                keuzes=alternatieven)
        return Antwoord(vraag=f"Ik kan '{ticket}' nergens vinden.")

    from models.background_run import BackgroundRun
    from models.board import Board, TicketComment

    bord = ctx.db.get(Board, t.board_id)
    run = ctx.db.get(BackgroundRun, t.agent_run_id) if t.agent_run_id else None

    opmerkingen = (ctx.db.query(TicketComment)
                   .filter(TicketComment.ticket_id == t.id,
                           TicketComment.kind == "comment")
                   .order_by(TicketComment.created_at.desc()).limit(1).all())
    laatste = opmerkingen[0] if opmerkingen else None

    feiten: Dict[str, Any] = {
        "sleutel": t.key,
        "titel": t.title,
        "klant": bord.name if bord else "?",
        "kolom": t.status,
        "agent": t.agent_state,
        "project": t.project,
    }
    if run is not None:
        feiten["gestart"] = run.started_at
        feiten["minuten_bezig"] = _minuten_sinds(run.started_at)
        feiten["stappen"] = len(run.steps or [])
        if run.error:
            feiten["fout"] = str(run.error)[:300]
        # Waar hij volgens zijn eigen stappen mee bezig is. Gecachet per run,
        # want hier zit een modelaanroep onder.
        feiten["bezig_met"] = run_samenvatting(ctx.db, run)
    if t.agent_state == "waiting" and t.agent_resume_at:
        feiten["wacht_tot"] = t.agent_resume_at
        feiten["wacht_op"] = t.agent_wait_reason
    if laatste is not None:
        feiten["laatste_opmerking"] = {
            "van": laatste.author,
            "minuten_geleden": _minuten_sinds(laatste.created_at),
            # LETTERLIJK mee, niet samengevat: dan kan de assistent citeren in
            # plaats van parafraseren als je doorvraagt.
            "tekst": (laatste.body or "")[:1200],
        }
    return Antwoord(feiten=feiten)


def zoek_tickets(ctx: Context, *, klant: Optional[str] = None,
                 kolom: Optional[str] = None, zoek: Optional[str] = None, **_) -> Antwoord:
    from models.board import Board, Ticket

    bord = ctx.bord_van_naam(klant) if klant else None
    if klant and bord is None:
        return Antwoord(vraag=f"Ik weet niet welk bord je met '{klant}' bedoelt.",
                        keuzes=[b.name for b in ctx.borden()])
    q = ctx.db.query(Ticket).filter(Ticket.archived_at.is_(None))
    if bord:
        q = q.filter(Ticket.board_id == bord.id)
    if kolom:
        q = q.filter(Ticket.status == kolom)
    rijen = q.order_by(Ticket.updated_at.desc()).limit(200).all()
    if zoek:
        treffers = {k for k, _ in opzoeken.kies_op_titel(
            zoek, [(t.key, t.title or "") for t in rijen], drempel=0.3)}
        rijen = [t for t in rijen if t.key in treffers]

    borden = {b.id: b.name for b in ctx.db.query(Board).all()}
    return Antwoord(feiten={
        "aantal": len(rijen),
        "tickets": [{"sleutel": t.key, "titel": (t.title or "")[:90],
                     "kolom": t.status, "klant": borden.get(t.board_id, "?"),
                     "agent": t.agent_state}
                    for t in rijen[:15]],
    })


def chat_status(ctx: Context, *, lab: Optional[str] = None, **_) -> Antwoord:
    from models.background_run import BackgroundRun
    from models.thread import Thread

    q = ctx.db.query(Thread).filter(Thread.source == "chat",
                                    Thread.archived_at.is_(None))
    if lab:
        gekozen = ctx.lab_van_naam(lab)
        if gekozen is None:
            return Antwoord(vraag=f"Ik weet niet welk lab je met '{lab}' bedoelt.")
        q = q.filter(Thread.lab_id == gekozen.id)
    threads = q.order_by(Thread.updated_at.desc()).limit(10).all()

    uit = []
    for t in threads:
        run = (ctx.db.query(BackgroundRun)
               .filter(BackgroundRun.thread_id == t.id)
               .order_by(BackgroundRun.created_at.desc()).first())
        uit.append({
            "titel": t.title,
            "bijgewerkt": t.updated_at,
            "laatste_run": {"status": run.status,
                            "minuten": _minuten_sinds(run.started_at)} if run else None,
        })
    return Antwoord(feiten={"chats": uit})


def workflow_status(ctx: Context, *, workflow: Optional[str] = None, **_) -> Antwoord:
    from models.workflow import Workflow, WorkflowRun

    wfs = ctx.db.query(Workflow).all()
    if workflow:
        treffers = opzoeken.kies_op_naam(workflow, [w.name for w in wfs])
        if len(treffers) != 1:
            return Antwoord(vraag="Welke workflow bedoel je?",
                            keuzes=[w.name for w in wfs][:10])
        wfs = [w for w in wfs if w.name == treffers[0]]

    uit = []
    for w in wfs[:10]:
        run = (ctx.db.query(WorkflowRun)
               .filter(WorkflowRun.workflow_id == w.id)
               .order_by(WorkflowRun.created_at.desc()).first())
        uit.append({
            "naam": w.name,
            "laatste_run": {
                "status": run.status,
                "gestart": run.started_at,
                "minuten": _minuten_sinds(run.started_at),
            } if run else None,
        })
    return Antwoord(feiten={"workflows": uit})


def schedules_overzicht(ctx: Context, **_) -> Antwoord:
    from models.schedule import Schedule

    rijen = ctx.db.query(Schedule).all()
    return Antwoord(feiten={"schedules": [
        {"naam": s.name, "aan": bool(getattr(s, "is_enabled", True)),
         "volgende": getattr(s, "next_run_at", None)}
        for s in rijen[:20]]})


def labs_status(ctx: Context, **_) -> Antwoord:
    from models.lab import Lab
    from services.lab.lab_service import LabService

    svc = LabService(ctx.db)
    uit = []
    for lab in ctx.db.query(Lab).all():
        try:
            bezet = svc.bezette_werkers(lab.id)
        except Exception:  # noqa: BLE001 — een labstatus mag nooit de vraag slopen
            bezet = None
        uit.append({"naam": lab.name, "status": lab.status,
                    "bezette_werkers": len(bezet) if bezet is not None else None})
    return Antwoord(feiten={"labs": uit})


def uren_overzicht(ctx: Context, *, van: Optional[str] = None,
                   tot: Optional[str] = None, **_) -> Antwoord:
    from services.time.time_service import TimeService

    uit = TimeService(ctx.db).overzicht(van=van, tot=tot)
    return Antwoord(feiten={
        "periode": {"van": van, "tot": tot},
        "totaal_uren": round(uit["totaal"]["verdeeld"] / 60, 1),
        "overlap_uren": round(uit["totaal"]["overlap"] / 60, 1),
        "per_project": [
            {"project": p["project"], "uren": round(p["verdeeld"] / 60, 1),
             "eigen_uren": round(p["eigen_minuten"] / 60, 1)}
            for p in uit["projecten"][:10]],
    })


def _minuten_sinds(tijdstip: Optional[str]) -> Optional[int]:
    if not tijdstip:
        return None
    try:
        d = datetime.fromisoformat(str(tijdstip).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - d).total_seconds() // 60)


LEESACTIES: Dict[str, Callable[..., Antwoord]] = {
    "wat_loopt_er": wat_loopt_er,
    "ticket_status": ticket_status,
    "zoek_tickets": zoek_tickets,
    "chat_status": chat_status,
    "workflow_status": workflow_status,
    "schedules_overzicht": schedules_overzicht,
    "labs_status": labs_status,
    "uren_overzicht": uren_overzicht,
}
