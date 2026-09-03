"""
services/boards/agent_work.py

"Werk dat de AI kan oppakken en het ticket kan aanvullen."

Een agent-run op een ticket is een gewone LabX-achtergrondtaak — zelfde
runtime, zelfde lab, zelfde guard, zelfde live-eventstroom — met drie dingen
eromheen:

1. **Een eigen thread per ticket**, zodat het gesprek over LAB-12 herleesbaar
   blijft en een tweede run op hetzelfde ticket de context van de eerste ziet.
2. **Een prompt die het ticket beschrijft** én de agent vertelt WAAR hij wat
   neerzet. Die scheiding is hard: de omschrijving is de OPDRACHT, opmerkingen
   zijn het WERKLOGBOEK, en acceptatiecriteria zijn het MEETLINT. Zonder die
   regel schrijft een agent zijn bevindingen zowel in een opmerking als onder
   de omschrijving, en is na twee runs niet meer te zien wat er oorspronkelijk
   gevraagd werd.
3. **Een afloop-hook** die het eindantwoord als opmerking op het ticket zet en
   de kolom verschuift. De hook is de vangnet-kant: ook een agent die vergeet
   `board__comment_ticket` aan te roepen laat zo een leesbaar spoor achter.

De hook overleeft een backend-herstart niet (het proces is weg); daarvoor is
`reconcile_on_start` — tickets die "running" claimen zonder levende run worden
teruggezet, net als bij achtergrondtaken zelf.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.board import Board, Ticket
from models.thread import Thread
from services.boards.board_service import BoardService

log = get_logger(__name__)

_MAX_ANSWER_IN_COMMENT = 8000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ticket_prompt(board: Board, ticket: Ticket, comments: List[Any],
                   extra_instruction: Optional[str] = None) -> str:
    lines = [
        f"Je pakt ticket {ticket.key} op van het board '{board.name}'.",
        "",
        f"## {ticket.key} — {ticket.title}",
        f"Status: {ticket.status} | Prioriteit: {ticket.priority}"
        + (f" | Toegewezen aan: {ticket.assignee}" if ticket.assignee else ""),
    ]
    if ticket.labels:
        lines.append(f"Labels: {', '.join(str(x) for x in ticket.labels)}")
    if ticket.external_key:
        lines.append(f"Externe bron: {ticket.external_provider} {ticket.external_key}"
                     + (f" ({ticket.external_url})" if ticket.external_url else ""))
    lines.append("")
    lines.append("### Omschrijving (de opdracht)")
    lines.append((ticket.description or "").strip() or "(geen omschrijving)")

    criteria = (ticket.acceptance_criteria or "").strip()
    lines.append("")
    lines.append("### Acceptatiecriteria")
    lines.append(criteria or "(nog niet ingevuld)")

    visible = [c for c in comments if c.kind == "comment"]
    if visible:
        lines.append("")
        lines.append("### Eerdere opmerkingen (het werklogboek)")
        for c in visible[-15:]:
            lines.append(f"- [{c.author}] {(c.body or '').strip()[:1500]}")

    if (board.agent_instruction or "").strip():
        lines.append("")
        lines.append("### Vaste werkafspraken op dit board")
        lines.append(board.agent_instruction.strip())

    if (extra_instruction or "").strip():
        lines.append("")
        lines.append("### Extra instructie voor deze run")
        lines.append(extra_instruction.strip())

    lines += [
        "",
        "### Wat er van je verwacht wordt",
        "Voer het werk daadwerkelijk uit in het lab (shell, bestanden, MCP-tools) —"
        " niet alleen beschrijven wat je zou doen.",
        "",
        "**Waar je wat neerzet — houd dit strikt gescheiden:**",
        f"- **Je bevindingen, voortgang, resultaten en vragen** gaan als OPMERKING op"
        f" {ticket.key}, met `board__comment_ticket`. Dat is het werklogboek en de"
        " enige plek waar verslag hoort.",
        "- **De omschrijving is de OPDRACHT, geen verslag.** Laat hem met rust, tenzij"
        " de opdracht zelf onduidelijk of onvolledig blijkt; dan scherp je hem aan met"
        " `board__update_ticket(description=...)`. Plak er NOOIT je bevindingen,"
        " statusupdates of een 'Bevindingen'-kopje onder — die staan al in je"
        " opmerking en horen daar niet dubbel.",
        "- **Acceptatiecriteria** zijn het meetlint: toets je werk eraan voor je"
        " afrondt en benoem per criterium of eraan voldaan is. Staan ze er niet, stel"
        " ze dan zelf op en zet ze met `board__update_ticket(acceptance_criteria=...)`"
        " als korte, toetsbare lijst (Markdown) — dat is een aanscherping van de"
        " opdracht, geen verslag.",
        "",
        "Kom je er niet uit of ontbreekt informatie? Zet dat als opmerking op het"
        " ticket en zeg het expliciet in je eindantwoord — een half afgemaakt ticket"
        " zonder uitleg is het slechtste resultaat.",
        "Sluit af met een kort, concreet eindantwoord: wat je hebt gedaan, of de"
        " acceptatiecriteria gehaald zijn, en wat de volgende stap is. Dat antwoord"
        " wordt automatisch als opmerking op het ticket gezet, dus schrijf het voor"
        " iemand die alleen het ticket leest.",
    ]
    return "\n".join(lines)


def _thread_for_ticket(db: Session, board: Board, ticket: Ticket) -> Thread:
    if ticket.agent_thread_id:
        existing = db.get(Thread, ticket.agent_thread_id)
        if existing is not None:
            return existing
    now = _now_iso()
    t = Thread(id=str(uuid4()), title=f"{ticket.key} — {ticket.title}"[:255],
               lab_id=board.lab_id, source="board", created_at=now, updated_at=now)
    db.add(t)
    db.commit()
    ticket.agent_thread_id = t.id
    db.commit()
    return t


async def start_ticket_run(db: Session, ticket_id: int, *,
                     extra_instruction: Optional[str] = None,
                     trigger: str = "handmatig") -> Dict[str, Any]:
    """Zet de agent op één ticket. Geeft de run-info terug; de run zelf loopt
    non-blocking door (zelfde patroon als een achtergrondtaak in de chat)."""
    from models.lab import Lab
    from services.agent import background_runs

    svc = BoardService(db)
    ticket = svc.get_ticket(ticket_id)
    board = svc.get_board(ticket.board_id)

    if not board.lab_id:
        raise HTTPException(status_code=409,
                            detail="Dit board heeft geen gekoppeld lab — de agent heeft een lab nodig om in te werken")
    lab = db.get(Lab, board.lab_id)
    if not lab:
        raise HTTPException(status_code=409, detail="Het lab van dit board bestaat niet meer")
    if lab.status != "running":
        # Een lab dat uit staat is geen reden om te weigeren: aanzetten is
        # precies wat degene die op "agent" drukt bedoelde. Lukt dát niet, dan
        # is er pas echt iets aan de hand.
        from services.lab.lab_service import LabService
        await LabService(db).ensure_running(lab.id)
    if ticket.agent_state == "running" and ticket.agent_run_id and \
            background_runs.is_active(ticket.agent_run_id):
        raise HTTPException(status_code=409, detail="De agent werkt al aan dit ticket")

    thread = _thread_for_ticket(db, board, ticket)
    prompt = _ticket_prompt(board, ticket, svc.list_comments(ticket.id), extra_instruction)

    run = background_runs.start(
        db, thread_id=thread.id, lab_id=board.lab_id,
        history=[{"role": "user", "content": prompt}], prompt=prompt,
        mode="background",
    )

    ticket.agent_state = "running"
    ticket.agent_run_id = run.id
    ticket.agent_last_error = None
    ticket.updated_at = _now_iso()
    db.commit()

    svc.add_comment(ticket.id, kind="activity", author="agent",
                    body=f"Agent gestart ({trigger}) — run {run.id[:8]}.")

    background_runs.on_finish(run.id, _make_finish_hook(ticket.id, started_at=_now_iso()))
    log.infox("Agent-run op ticket gestart", ticket=ticket.key, board=board.name,
              run_id=run.id, trigger=trigger)
    return {"run_id": run.id, "thread_id": thread.id, "ticket_id": ticket.id,
            "ticket_key": ticket.key, "status": "running"}


def _agent_commented_since(db: Session, ticket_id: int, since: str) -> bool:
    """Heeft de agent tijdens deze run zélf al verslag gedaan?"""
    from models.board import TicketComment
    return (db.query(TicketComment)
            .filter(TicketComment.ticket_id == ticket_id,
                    TicketComment.kind == "comment",
                    TicketComment.author == "agent",
                    TicketComment.created_at >= since)
            .first()) is not None


def _make_finish_hook(ticket_id: int, *, started_at: str):
    def _hook(db: Session, run) -> None:
        svc = BoardService(db)
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return
        board = db.get(Board, ticket.board_id)
        status = getattr(run, "status", None) or "failed"

        if status == "completed":
            answer = (getattr(run, "answer", None) or "").strip()
            ticket.agent_state = "done"
            ticket.agent_last_error = None
            # Het eindantwoord is een VANGNET, geen tweede verslag: heeft de
            # agent tijdens de run zelf al een opmerking geplaatst, dan staat
            # het er al en levert dit alleen dubbele tekst op.
            if answer and not _agent_commented_since(db, ticket.id, started_at):
                svc.add_comment(ticket.id, kind="comment", author="agent",
                                body=answer[:_MAX_ANSWER_IN_COMMENT])
            # Verplaats alleen vanuit de oppak-kolom: een agent die het ticket
            # zelf al ergens anders heeft neergezet weet beter dan deze hook.
            done_col = (board.agent_done_column if board else None)
            if done_col and board and ticket.status == (board.agent_column or ""):
                try:
                    svc.move_ticket(ticket.id, done_col, author="agent")
                except HTTPException as exc:
                    log.warningx("Ticket verplaatsen na agent-run mislukt",
                                 ticket=ticket.key, error=str(exc.detail))
        else:
            ticket.agent_state = "failed"
            ticket.agent_last_error = (getattr(run, "error", None)
                                       or f"Run eindigde als '{status}'")[:2000]
            svc.add_comment(ticket.id, kind="activity", author="agent",
                            body=f"Agent-run {status}: {ticket.agent_last_error[:500]}")
        ticket.updated_at = _now_iso()
        db.commit()
    return _hook


def reconcile_on_start(db: Session) -> int:
    """Na een herstart zijn alle in-flight runs weg (background_runs zet ze op
    'interrupted'), maar het ticket claimt nog 'running'. Zonder deze opruiming
    kan zo'n ticket nooit meer opgepakt worden."""
    rows = db.query(Ticket).filter(Ticket.agent_state == "running").all()
    for t in rows:
        t.agent_state = "failed"
        t.agent_last_error = "Backend herstart tijdens de agent-run"
        t.updated_at = _now_iso()
    if rows:
        db.commit()
    return len(rows)


async def pick_up_column(db: Session, board_id: int, *, column: Optional[str] = None,
                         max_tickets: int = 1, trigger: str = "schedule") -> List[Dict[str, Any]]:
    """Pak de bovenste N tickets uit een kolom op. Dit is wat een board-
    schedule doet: werk dat klaarstaat wordt vanzelf door de AI opgepakt.
    Tickets waar de agent al aan werkt worden overgeslagen."""
    svc = BoardService(db)
    board = svc.get_board(board_id)
    col = column or board.agent_column
    if not col:
        raise HTTPException(status_code=400, detail="Dit board heeft geen agent-kolom ingesteld")

    started: List[Dict[str, Any]] = []
    for ticket in svc.list_tickets(board_id, status=col):
        if len(started) >= max(1, int(max_tickets or 1)):
            break
        if ticket.agent_state == "running":
            continue
        try:
            started.append(await start_ticket_run(db, ticket.id, trigger=trigger))
        except HTTPException as exc:
            log.warningx("Ticket oppakken mislukt", ticket=ticket.key, error=str(exc.detail))
            started.append({"ticket_key": ticket.key, "status": "failed", "error": str(exc.detail)})
    return started
