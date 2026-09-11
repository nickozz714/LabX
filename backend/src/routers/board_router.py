"""routers/board_router.py — het agent board: boards, tickets, opmerkingen,
agent-runs op een ticket en synchronisatie met Azure DevOps / Jira.

De endpoints zijn dun; alle logica zit in services/boards/. Tickets hangen
onder /boards/{board_id}/tickets zodat een bord altijd de context is — er
bestaat geen ticket zonder bord."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.boards.board_service import BoardService

router = APIRouter(prefix="/boards", tags=["boards"], dependencies=[Depends(require_user)])


# Metadata voor het instellingenscherm: welke velden een provider nodig heeft.
# Staat hier (niet in de frontend) zodat een nieuwe provider maar op één plek
# beschreven hoeft te worden.
PROVIDER_SPECS = [
    {
        "key": "local",
        "name": "Alleen LabX",
        "description": "Een board dat alleen in LabX bestaat. Geen externe koppeling.",
        "fields": [],
        "secret_label": None,
    },
    {
        "key": "azure_devops",
        "name": "Azure DevOps Boards",
        "description": "Synchroniseert work items uit een Azure DevOps-project.",
        "fields": [
            {"key": "organization", "label": "Organisatie", "required": True,
             "placeholder": "mijn-org"},
            {"key": "project", "label": "Project", "required": True,
             "placeholder": "MijnProject"},
            {"key": "area_path", "label": "Area path (optioneel)", "required": False,
             "placeholder": "MijnProject\\Team"},
            {"key": "work_item_type", "label": "Type voor nieuwe items", "required": False,
             "placeholder": "Task"},
            {"key": "wiql", "label": "Eigen WIQL-query (optioneel)", "required": False,
             "multiline": True,
             "placeholder": "SELECT [System.Id] FROM WorkItems WHERE [System.State] <> 'Closed'"},
        ],
        "secret_label": "Personal Access Token (scope: Work Items read/write)",
        "state_hint": ("Bijvoorbeeld New, Active, Resolved, Closed. Meerdere statussen per "
                       "kolom mag; de eerste is degene waar LabX het work item naartoe zet."),
        "write_note": ("Bij two-way sync schrijft LabX titel, omschrijving, prioriteit, tags "
                       "en status (System.State) terug naar het work item, en plaatst het "
                       "opmerkingen — ook die van de agent. Zet het op 'alleen lezen' voor een "
                       "bord dat de bron niet mag aanraken."),
    },
    {
        "key": "jira",
        "name": "Jira",
        "description": "Synchroniseert issues uit een Jira Cloud-project.",
        "fields": [
            {"key": "base_url", "label": "Jira-URL", "required": True,
             "placeholder": "https://mijnbedrijf.atlassian.net"},
            {"key": "email", "label": "E-mailadres bij het API-token", "required": True,
             "placeholder": "naam@bedrijf.nl"},
            {"key": "project_key", "label": "Projectsleutel", "required": True,
             "placeholder": "BICC"},
            {"key": "board_name", "label": "Jira-bordnaam (optioneel — anders het eerste bord "
                                          "van het project)", "required": False,
             "placeholder": "BICC Sprint board"},
            {"key": "issue_type", "label": "Type voor nieuwe issues", "required": False,
             "placeholder": "Task"},
            {"key": "acceptance_field", "label": "Veld-id voor acceptatiecriteria (optioneel)",
             "required": False, "placeholder": "customfield_10035"},
            {"key": "jql", "label": "Eigen JQL (optioneel)", "required": False,
             "multiline": True, "placeholder": "project = BICC AND statusCategory != Done"},
        ],
        "secret_label": "Atlassian API-token",
        "state_hint": ("Een Jira-bordkolom is een groepje statussen — koppel dus gerust "
                       "meerdere statussen aan één LabX-kolom. De eerste status van een kolom "
                       "is degene waar LabX het issue naartoe zet als je het ticket verplaatst."),
        "write_note": ("Bij two-way sync schrijft LabX summary, description, prioriteit en "
                       "labels terug, plaatst het opmerkingen (ook die van de agent) en zet "
                       "het de status om via een Jira-transitie — die moet dus in de workflow "
                       "van het project bestaan. Zet het op 'alleen lezen' voor een bord dat "
                       "de bron niet mag aanraken."),
    },
]


def _svc(db: Session) -> BoardService:
    return BoardService(db)


@router.get("/providers")
def list_providers():
    return PROVIDER_SPECS


# ── boards ──────────────────────────────────────────────────────────────────

@router.get("")
def list_boards(db: Session = Depends(get_db)):
    svc = _svc(db)
    return [svc.board_to_dict(b, with_counts=True) for b in svc.list_boards()]


@router.post("")
def create_board(payload: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.board_to_dict(svc.create_board(payload), with_counts=True)



@router.get("/overview")
def overview(runs: int = Query(default=30, le=200), db: Session = Depends(get_db)):
    """Eén beeld over álle borden: wat draait er nu, wat wacht, en hoe liepen
    de laatste dingen af.

    Dit werd eerst volledig uit PLANNINGEN opgebouwd, en dat was een
    denkfout: verreweg het meeste werk begint met "Agent starten" op een
    ticket, en dat maakt geen planning. Het overzicht stond dus leeg terwijl er
    een agent aan het werk was. De bron is nu wat er feitelijk draait — de
    agent-runs zelf — met planningen als extra laag eromheen.

    Bewust één antwoord en niet vijf losse endpoints: "hoe staat het ervoor" is
    één vraag, en het antwoord moet in één oogopslag kloppen.

    LET OP: deze route staat vóór /{board_id}, anders leest FastAPI "overview"
    als board-id."""
    from models.background_run import BackgroundRun
    from models.board import Ticket
    from models.lab import Lab
    from models.plan import TicketPlan, TicketPlanItem
    from services.boards.plan_service import PlanService

    svc = _svc(db)
    plan_svc = PlanService(db)
    boards = svc.list_boards()
    board_namen = {b.id: b.name for b in boards}
    labs = {l.id: l for l in db.query(Lab).all()}

    # Runs koppelen aan tickets via de thread van het ticket: dat is de enige
    # verbinding die er is, en hij geldt voor élke run — handmatig gestart of
    # uit een planning.
    def _runs(*statussen: str, limiet: int) -> List[Any]:
        q = (db.query(BackgroundRun, Ticket)
             .join(Ticket, Ticket.agent_thread_id == BackgroundRun.thread_id))
        if statussen:
            q = q.filter(BackgroundRun.status.in_(statussen))
        return q.order_by(BackgroundRun.created_at.desc()).limit(limiet).all()

    # Bij welke planning hoort een lopende run (als hij ergens bij hoort)?
    plan_per_run = {}
    for item, plan in (db.query(TicketPlanItem, TicketPlan)
                       .join(TicketPlan, TicketPlan.id == TicketPlanItem.plan_id)
                       .filter(TicketPlanItem.run_id.isnot(None)).all()):
        plan_per_run[item.run_id] = {"plan_id": plan.id, "plan_name": plan.name,
                                     "worker_id": item.worker_id}

    def _regel(run, ticket) -> Dict[str, Any]:
        board = next((b for b in boards if b.id == ticket.board_id), None)
        lab = labs.get(board.lab_id) if board and board.lab_id else None
        extra = plan_per_run.get(run.id) or {}
        return {
            "run_id": run.id, "status": run.status,
            "board_id": ticket.board_id, "board_name": board_namen.get(ticket.board_id),
            "ticket_key": ticket.key, "ticket_title": ticket.title,
            "thread_id": run.thread_id,
            "lab_name": lab.name if lab else None,
            "lab_status": lab.status if lab else None,
            "started_at": run.started_at or run.created_at,
            "finished_at": run.finished_at,
            "error": (run.error or None),
            "steps": len(run.steps or []),
            "plan_id": extra.get("plan_id"), "plan_name": extra.get("plan_name"),
        }

    lopend = [_regel(r, t) for r, t in _runs("running", limiet=50)]
    verloop = [_regel(r, t) for r, t in _runs(limiet=runs)]

    borden = []
    for b in boards:
        tickets = db.query(Ticket).filter(Ticket.board_id == b.id).all()
        per_kolom: Dict[str, int] = {}
        for t in tickets:
            per_kolom[t.status] = per_kolom.get(t.status, 0) + 1
        lab = labs.get(b.lab_id) if b.lab_id else None
        werkers_totaal = werkers_bezet = 0
        if lab is not None:
            from services.lab.lab_service import LabService
            lab_svc = LabService(db)
            werkers_totaal = len(lab_svc.ensure_workers(lab))
            werkers_bezet = len(lab_svc._bezette_werkers(lab.id))
        actief = db.query(TicketPlan).filter(
            TicketPlan.board_id == b.id,
            TicketPlan.state.in_(("running", "paused", "scheduled"))).all()
        borden.append({
            "id": b.id, "name": b.name, "key_prefix": b.key_prefix,
            "lab_id": b.lab_id,
            "lab_name": lab.name if lab else None,
            "lab_status": lab.status if lab else None,
            "agent_column": b.agent_column,
            "workers_total": werkers_totaal,
            "workers_busy": werkers_bezet,
            "columns": b.columns or [],
            "ticket_counts": per_kolom,
            "ticket_total": len(tickets),
            "running_tickets": len([r for r in lopend if r["board_id"] == b.id]),
            "wachtend_in_agentkolom": per_kolom.get(b.agent_column or "", 0),
            "provider": b.provider,
            "last_sync_at": b.last_sync_at,
            "last_sync_error": b.last_sync_error,
            "plans": [plan_svc.to_dict(p, with_items=False) for p in actief],
        })

    return {"boards": borden, "running": lopend, "recent": verloop}

@router.get("/{board_id}")
def get_board(board_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.board_to_dict(svc.get_board(board_id), with_counts=True)


@router.patch("/{board_id}")
def update_board(board_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.board_to_dict(svc.update_board(board_id, payload), with_counts=True)


@router.delete("/{board_id}")
def delete_board(board_id: int, db: Session = Depends(get_db)):
    _svc(db).delete_board(board_id)
    return {"ok": True}


# ── tickets ─────────────────────────────────────────────────────────────────

@router.get("/{board_id}/tickets")
def list_tickets(board_id: int, status: Optional[str] = None, assignee: Optional[str] = None,
                 db: Session = Depends(get_db)):
    svc = _svc(db)
    svc.get_board(board_id)
    return svc.tickets_to_dicts(svc.list_tickets(board_id, status=status, assignee=assignee))


@router.post("/{board_id}/tickets")
def create_ticket(board_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.ticket_to_dict(svc.create_ticket(board_id, payload))


@router.get("/{board_id}/tickets/{ticket_id}")
def get_ticket(board_id: int, ticket_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    ticket = _ticket_of_board(svc, board_id, ticket_id)
    return {
        **svc.ticket_to_dict(ticket),
        "comments": [svc.comment_to_dict(c) for c in svc.list_comments(ticket.id)],
    }


@router.patch("/{board_id}/tickets/{ticket_id}")
def update_ticket(board_id: int, ticket_id: int, payload: Dict[str, Any],
                  db: Session = Depends(get_db)):
    svc = _svc(db)
    _ticket_of_board(svc, board_id, ticket_id)
    return svc.ticket_to_dict(svc.update_ticket(ticket_id, payload))


@router.delete("/{board_id}/tickets/{ticket_id}")
def delete_ticket(board_id: int, ticket_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    _ticket_of_board(svc, board_id, ticket_id)
    svc.delete_ticket(ticket_id)
    return {"ok": True}


@router.post("/{board_id}/tickets/{ticket_id}/move")
def move_ticket(board_id: int, ticket_id: int, payload: Dict[str, Any],
                db: Session = Depends(get_db)):
    svc = _svc(db)
    _ticket_of_board(svc, board_id, ticket_id)
    status = (payload.get("status") or "").strip()
    if not status:
        raise HTTPException(status_code=400, detail="status is verplicht")
    position = payload.get("position")
    return svc.ticket_to_dict(
        svc.move_ticket(ticket_id, status,
                        float(position) if position is not None else None))


@router.get("/{board_id}/tickets/{ticket_id}/comments")
def list_comments(board_id: int, ticket_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    _ticket_of_board(svc, board_id, ticket_id)
    return [svc.comment_to_dict(c) for c in svc.list_comments(ticket_id)]


@router.post("/{board_id}/tickets/{ticket_id}/comments")
def add_comment(board_id: int, ticket_id: int, payload: Dict[str, Any],
                db: Session = Depends(get_db)):
    svc = _svc(db)
    _ticket_of_board(svc, board_id, ticket_id)
    body = (payload.get("body") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body is verplicht")
    # `internal` weglaten laat de service kiezen: agent = intern, mens = extern.
    internal = payload.get("internal")
    return svc.comment_to_dict(
        svc.add_comment(ticket_id, body=body, author=payload.get("author") or "user",
                        internal=None if internal is None else bool(internal)))


@router.post("/{board_id}/tickets/{ticket_id}/comments/{comment_id}/promote")
async def promote_comment(board_id: int, ticket_id: int, comment_id: int,
                          db: Session = Depends(get_db)):
    """Een interne opmerking alsnog naar de bron. Bij een two-way board gaat hij
    er meteen heen — wachten op de volgende sync maakt van "nu doorzetten" iets
    dat er straks misschien staat, en dan weet je niet of het gelukt is."""
    svc = _svc(db)
    _ticket_of_board(svc, board_id, ticket_id)
    comment = svc.promote_comment(comment_id)
    board = svc.get_board(board_id)
    pushed: Any = None
    if board.provider != "local" and board.sync_direction == "two_way":
        from services.boards.sync_service import BoardSyncService
        try:
            pushed = await BoardSyncService(db).push_comment(comment_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — de promotie zelf staat al vast
            pushed = {"ok": False, "error": str(exc)[:300]}
    return {"comment": svc.comment_to_dict(svc.get_comment(comment_id)), "pushed": pushed}


def _ticket_of_board(svc: BoardService, board_id: int, ticket_id: int):
    ticket = svc.get_ticket(ticket_id)
    if ticket.board_id != board_id:
        raise HTTPException(status_code=404, detail="Ticket hoort niet bij dit board")
    return ticket


# ── agent ───────────────────────────────────────────────────────────────────

# async def, geen gewone def: FastAPI draait een sync endpoint in een
# threadpool, en daar is geen draaiende event loop — terwijl het starten van
# een achtergrondrun er juist een nodig heeft (asyncio.create_task).
@router.post("/{board_id}/tickets/{ticket_id}/agent-run")
async def start_agent_run(board_id: int, ticket_id: int, payload: Optional[Dict[str, Any]] = None,
                    db: Session = Depends(get_db)):
    """Laat de agent dit ticket oppakken. Draait non-blocking: het antwoord
    bevat het run-id waarop de UI kan meelezen (/chat/background-runs/{id})."""
    from services.boards.agent_work import start_ticket_run
    from services.lab.uploads import beschrijf
    svc = _svc(db)
    _ticket_of_board(svc, board_id, ticket_id)
    body = payload or {}
    instructie = str(body.get("instruction") or "").strip()
    # Bijlagen staan al in het lab; wat hier bijkomt is de verwijzing. Ze horen
    # bij de instructie en niet bij de omschrijving van het ticket: die laatste
    # is de opdracht zoals de bron hem kent en gaat terug naar Jira.
    blok = beschrijf(body.get("attachments"))
    if blok:
        instructie = f"{instructie}\n{blok}".strip()
    return await start_ticket_run(db, ticket_id,
                                  extra_instruction=instructie or None,
                                  trigger="handmatig")


@router.post("/{board_id}/pick-up")
async def pick_up(board_id: int, payload: Optional[Dict[str, Any]] = None,
            db: Session = Depends(get_db)):
    """Pak de bovenste N tickets uit de agent-kolom op — dezelfde actie die een
    board-schedule automatisch uitvoert, hier met de hand."""
    from services.boards.agent_work import pick_up_column
    body = payload or {}
    started = await pick_up_column(db, board_id, column=body.get("column"),
                                   max_tickets=int(body.get("max_tickets") or 1),
                                   trigger="handmatig")
    return {"started": started, "count": len(started)}



# ── planningen (geordende werkrijen) ────────────────────────────────────────

def _plan_svc(db: Session):
    from services.boards.plan_service import PlanService
    return PlanService(db)


@router.get("/{board_id}/plans")
def list_plans(board_id: int, limit: int = Query(default=25, le=200),
               db: Session = Depends(get_db)):
    from models.plan import TicketPlan
    svc = _plan_svc(db)
    rows = (db.query(TicketPlan).filter(TicketPlan.board_id == board_id)
            .order_by(TicketPlan.id.desc()).limit(limit).all())
    return [svc.to_dict(p) for p in rows]


@router.post("/{board_id}/plans")
async def create_plan(board_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Een eigen selectie inplannen. `ticket_ids` is de VOLGORDE waarin ze
    gedaan worden. Zonder `start_at` begint hij meteen; met `start_at` (ISO)
    wacht hij op dat moment. `start_now=false` zet hem klaar zonder te starten."""
    from services.lab.uploads import beschrijf
    svc = _plan_svc(db)
    # Een planning-instructie geldt voor élke run erin, dus een bijlage hier is
    # een bijlage voor alle tickets van deze planning. Dat is precies wat je
    # wilt bij bijvoorbeeld één specificatie die voor de hele reeks geldt.
    instructie = str(payload.get("instruction") or "").strip()
    blok = beschrijf(payload.get("attachments"))
    if blok:
        instructie = f"{instructie}\n{blok}".strip()
    plan = svc.create(
        board_id,
        name=str(payload.get("name") or ""),
        ticket_ids=payload.get("ticket_ids") or [],
        start_at=(payload.get("start_at") or None),
        instruction=instructie or None,
        max_parallel=payload.get("max_parallel"),
        workspace_mode=payload.get("workspace_mode"),
        start_now=bool(payload.get("start_now", True)),
    )
    if plan.state == "running":
        await svc.advance(plan.id)
    return svc.to_dict(svc.get(plan.id))


@router.post("/{board_id}/plans/from-column")
async def create_plan_from_column(board_id: int, payload: Optional[Dict[str, Any]] = None,
                                  db: Session = Depends(get_db)):
    """De hele kolom als planning — "pak alles op", maar dan te volgen en te
    sturen. `limit` leeg = alles."""
    body = payload or {}
    svc = _plan_svc(db)
    plan = svc.create_from_column(board_id, column=body.get("column"),
                                  name=body.get("name"),
                                  limit=body.get("limit"),
                                  instruction=body.get("instruction"),
                                  start_at=body.get("start_at") or None)
    if plan.state == "running":
        await svc.advance(plan.id)
    return svc.to_dict(svc.get(plan.id))


@router.get("/{board_id}/plans/{plan_id}")
def get_plan(board_id: int, plan_id: int, db: Session = Depends(get_db)):
    svc = _plan_svc(db)
    return svc.to_dict(svc.get(plan_id))


@router.post("/{board_id}/plans/{plan_id}/pause")
def pause_plan(board_id: int, plan_id: int, db: Session = Depends(get_db)):
    svc = _plan_svc(db)
    return svc.to_dict(svc.pause(plan_id))


@router.post("/{board_id}/plans/{plan_id}/resume")
async def resume_plan(board_id: int, plan_id: int, db: Session = Depends(get_db)):
    svc = _plan_svc(db)
    await svc.resume(plan_id)
    return svc.to_dict(svc.get(plan_id))


@router.post("/{board_id}/plans/{plan_id}/cancel")
def cancel_plan(board_id: int, plan_id: int, db: Session = Depends(get_db)):
    svc = _plan_svc(db)
    return svc.to_dict(svc.cancel(plan_id))


@router.post("/{board_id}/plans/{plan_id}/reorder")
def reorder_plan(board_id: int, plan_id: int, payload: Dict[str, Any],
                 db: Session = Depends(get_db)):
    svc = _plan_svc(db)
    svc.reorder(plan_id, [int(x) for x in (payload.get("item_ids") or [])])
    return svc.to_dict(svc.get(plan_id))


@router.delete("/{board_id}/plans/{plan_id}/items/{item_id}")
def remove_plan_item(board_id: int, plan_id: int, item_id: int, db: Session = Depends(get_db)):
    svc = _plan_svc(db)
    svc.remove_item(plan_id, item_id)
    return svc.to_dict(svc.get(plan_id))


# ── synchronisatie ──────────────────────────────────────────────────────────

@router.post("/{board_id}/sync")
async def sync_board(board_id: int, db: Session = Depends(get_db)):
    from services.boards.sync_service import BoardSyncService
    try:
        return await BoardSyncService(db).sync_board(board_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — een configuratiefout is een 400, geen 500
        raise HTTPException(status_code=400, detail=str(exc)[:1000])


@router.post("/{board_id}/sync/test")
async def test_connection(board_id: int, db: Session = Depends(get_db)):
    """Haalt één pagina op zonder iets weg te schrijven — de knop 'verbinding
    testen' in het instellingenscherm."""
    from services.boards.sync_service import BoardSyncService
    svc = BoardSyncService(db)
    board = svc.boards.get_board(board_id)
    try:
        adapter = svc._adapter(board)
        items = await adapter.fetch_items(with_comments=False)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:1000]}

    # Statussen uit de bron zelf (alle, ook lege) aangevuld met wat er in de
    # opgehaalde items staat — een status waar nu geen issue in zit hoort ook
    # in de mapping te kunnen.
    try:
        declared = await adapter.discover_states()
    except Exception:  # noqa: BLE001
        declared = []
    states = sorted(set(declared) | {i.state for i in items if i.state})

    # De kolommen zoals ze in de bron op het bord staan, met de statussen
    # eronder: hiermee kan de frontend de mapping in één klik overnemen.
    try:
        columns = [{"name": c.name, "states": c.states}
                   for c in await adapter.discover_columns()]
    except Exception:  # noqa: BLE001
        columns = []

    unmapped = [s for s in states
                if svc._column_for_state(board, s) is None]
    return {
        "ok": True, "found": len(items),
        # De echte statusnamen uit de bron: precies wat de gebruiker nodig
        # heeft om de statusmapping in te vullen zonder te gokken.
        "states": states,
        "columns": columns,
        # Statussen die nu op geen kolom uitkomen — die tickets belanden bij een
        # sync in de eerste kolom. Beter hier gezegd dan achteraf ontdekt.
        "unmapped_states": unmapped,
        "sample": [{"key": i.external_key, "title": i.title, "state": i.state}
                   for i in items[:5]],
    }
