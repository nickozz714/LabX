"""
services/boards/plan_service.py

Het afwerken van een planning: één ticket tegelijk, in jouw volgorde, tot de
lijst op is of er iets tegenhoudt.

Drie regels bepalen het gedrag, en die zijn allemaal een keuze geweest:

- **Eén run tegelijk per LAB, niet per planning.** Alle tickets van een bord
  werken in dezelfde container. Twee agents die daar tegelijk in graaien
  vechten om dezelfde bestanden, processen en `az`-sessie — zo liep de
  proces-tabel van een lab een keer vol en lag alles plat. Planningen op
  vérschillende borden (en dus verschillende labs) lopen wél gewoon naast
  elkaar. Zodra een lab meerdere werkers kan hebben, is `_lab_bezet` de enige
  plek die daarvoor open hoeft.
- **Een geblokkeerd ticket pauzeert de planning**, hij slaat het niet over.
  Doorgaan met de rest zou de volgorde die jij bedoelde stilzwijgend
  omgooien; nu staat er expliciet "wacht op SWI-3" en beslis jij.
- **Doorgaan gebeurt op het aflopen van een run**, niet op een klok. De
  afloop-hook van de achtergrondrun zet het volgende ticket in gang, zodat er
  geen seconde tussen zit. De scheduler-tick is er alleen voor planningen die
  op hun tijdstip wachten, en als vangnet na een herstart.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.board import Board, Ticket
from models.plan import TicketPlan, TicketPlanItem

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── samenstellen ────────────────────────────────────────────────────────

    def get(self, plan_id: int) -> TicketPlan:
        plan = self.db.get(TicketPlan, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Planning niet gevonden")
        return plan

    def items(self, plan_id: int) -> List[TicketPlanItem]:
        return (self.db.query(TicketPlanItem)
                .filter(TicketPlanItem.plan_id == plan_id)
                .order_by(TicketPlanItem.position.asc(), TicketPlanItem.id.asc()).all())

    def create(self, board_id: int, *, name: str, ticket_ids: List[int],
               start_at: Optional[str] = None, instruction: Optional[str] = None,
               source: str = "handmatig", start_now: bool = True) -> TicketPlan:
        board = self.db.get(Board, board_id)
        if board is None:
            raise HTTPException(status_code=404, detail="Board niet gevonden")
        ids = [int(x) for x in ticket_ids]
        if not ids:
            raise HTTPException(status_code=400, detail="Een planning zonder tickets doet niets")
        geldig = {t.id: t for t in self.db.query(Ticket)
                  .filter(Ticket.board_id == board_id, Ticket.id.in_(ids)).all()}
        onbekend = [i for i in ids if i not in geldig]
        if onbekend:
            raise HTTPException(status_code=400,
                                detail=f"Deze tickets horen niet bij dit board: {onbekend}")
        # Een ticket dat al in een lopende planning staat, gaat er niet nog
        # eens in. Twee planningen die hetzelfde ticket oppakken laten de agent
        # hetzelfde werk twee keer doen — en dat werk verandert echte systemen,
        # dus dat is geen onschuldige dubbelloop.
        al_gepland = self._al_gepland(board_id, ids)
        overgeslagen = [geldig[i].key for i in ids if i in al_gepland]
        ids = [i for i in ids if i not in al_gepland]
        if not ids:
            raise HTTPException(
                status_code=409,
                detail=(f"Deze tickets staan al in een lopende planning: "
                        f"{', '.join(overgeslagen)}"))
        now = _now_iso()
        plan = TicketPlan(
            board_id=board_id, name=(name or "").strip() or f"Planning {now[:16]}",
            state="scheduled" if start_at else ("running" if start_now else "draft"),
            start_at=start_at, source=source,
            instruction=(instruction or "").strip() or None,
            created_at=now, updated_at=now)
        self.db.add(plan)
        self.db.flush()
        # De VOLGORDE van de meegegeven lijst is de bedoeling — niet de
        # bordvolgorde, niet de id-volgorde. Wie een selectie handmatig
        # ordent, verwacht precies die volgorde terug.
        for i, tid in enumerate(ids):
            self.db.add(TicketPlanItem(plan_id=plan.id, ticket_id=tid,
                                       position=float((i + 1) * 100), state="waiting"))
        if overgeslagen:
            plan.note = (f"Overgeslagen omdat ze al in een lopende planning staan: "
                         f"{', '.join(overgeslagen)}")
        self.db.commit()
        log.infox("Planning aangemaakt", plan=plan.id, board=board_id,
                  tickets=len(ids), overgeslagen=len(overgeslagen), state=plan.state)
        return plan

    def _al_gepland(self, board_id: int, ticket_ids: List[int]) -> set:
        """Ticket-id's die al in een planning zitten die nog wat kan gaan doen."""
        rijen = (self.db.query(TicketPlanItem.ticket_id)
                 .join(TicketPlan, TicketPlan.id == TicketPlanItem.plan_id)
                 .filter(TicketPlan.board_id == board_id,
                         TicketPlan.state.in_(("draft", "scheduled", "running", "paused")),
                         TicketPlanItem.state.in_(("waiting", "blocked", "running")),
                         TicketPlanItem.ticket_id.in_(ticket_ids)).all())
        return {r[0] for r in rijen}

    def create_from_column(self, board_id: int, *, column: Optional[str] = None,
                           name: Optional[str] = None, limit: Optional[int] = None,
                           instruction: Optional[str] = None,
                           start_at: Optional[str] = None) -> TicketPlan:
        """"Pak de hele kolom op" is geen apart mechanisme maar dezelfde
        planning, gevuld met de kolom in bordvolgorde — zodat je hem daarna
        net zo goed kunt volgen, pauzeren en herschikken."""
        from services.boards.board_service import BoardService
        board = self.db.get(Board, board_id)
        if board is None:
            raise HTTPException(status_code=404, detail="Board niet gevonden")
        col = column or board.agent_column
        if not col:
            raise HTTPException(status_code=400, detail="Dit board heeft geen agent-kolom ingesteld")
        tickets = BoardService(self.db).list_tickets(board_id, status=col)
        ids = [t.id for t in tickets]
        if limit:
            ids = ids[:max(1, int(limit))]
        if not ids:
            raise HTTPException(status_code=400, detail=f"Er staan geen tickets in kolom '{col}'")
        vrij = [i for i in ids if i not in self._al_gepland(board_id, ids)]
        if not vrij:
            raise HTTPException(
                status_code=409,
                detail=f"Alle tickets in kolom '{col}' staan al in een lopende planning")
        ids = vrij
        kolomnaam = next((c.get("name") for c in (board.columns or []) if c.get("key") == col), col)
        return self.create(board_id, name=name or f"Kolom '{kolomnaam}'", ticket_ids=ids,
                           instruction=instruction, source="kolom", start_at=start_at)

    # ── afhankelijkheden ────────────────────────────────────────────────────

    def _done_columns(self, board: Board) -> set:
        keys = {str(c.get("key")) for c in (board.columns or []) if c.get("is_done")}
        if board.agent_done_column:
            keys.add(str(board.agent_done_column))
        return keys

    def blokkades(self, ticket: Ticket) -> List[str]:
        """Welke afhankelijkheden van dit ticket nog niet klaar zijn.

        "Klaar" is wat het BORD daaronder verstaat: het ticket staat in een
        kolom die als klaar gemarkeerd is. Niet "de agent zei done" — een
        mens die het ticket terugsleept naar Review bedoelt dat het nog niet
        af is, en dat hoort te tellen."""
        keys = [str(k).strip() for k in (getattr(ticket, "depends_on", None) or []) if str(k).strip()]
        if not keys:
            return []
        board = self.db.get(Board, ticket.board_id)
        klaar_kolommen = self._done_columns(board) if board else set()
        open_keys: List[str] = []
        for key in keys:
            ander = (self.db.query(Ticket)
                     .filter(Ticket.board_id == ticket.board_id, Ticket.key == key).one_or_none())
            if ander is None:
                continue  # verwijderd ticket houdt niets meer tegen
            if ander.status not in klaar_kolommen:
                open_keys.append(key)
        return open_keys

    # ── uitvoeren ───────────────────────────────────────────────────────────

    def _lab_bezet(self, plan: TicketPlan) -> Optional[int]:
        """Draait er al een planning in hetzelfde lab? Geeft dan díe planning
        terug. Dit is de enige plek die weet dat een lab één werker heeft —
        krijgt een lab er meer, dan telt hier het aantal vrije werkers."""
        board = self.db.get(Board, plan.board_id)
        if board is None or not board.lab_id:
            return None
        borden = [b.id for b in self.db.query(Board).filter(Board.lab_id == board.lab_id).all()]
        rij = (self.db.query(TicketPlanItem, TicketPlan)
               .join(TicketPlan, TicketPlan.id == TicketPlanItem.plan_id)
               .filter(TicketPlan.board_id.in_(borden),
                       TicketPlan.id != plan.id,
                       TicketPlanItem.state == "running").first())
        return rij[1].id if rij else None

    async def advance(self, plan_id: int) -> Dict[str, Any]:
        """Zet het volgende ticket in gang, als dat kan."""
        from services.agent import background_runs
        from services.boards.agent_work import start_ticket_run

        plan = self.get(plan_id)
        if plan.state not in ("running", "scheduled"):
            return {"plan": plan.id, "state": plan.state, "gestart": None}
        items = self.items(plan.id)

        # Loopt er nog iets van deze planning? Dan wachten we daarop. Een item
        # dat "running" heet terwijl de run allang weg is (herstart) telt niet
        # mee — anders staat de planning voor eeuwig te wachten op een geest.
        for it in items:
            if it.state != "running":
                continue
            if it.run_id and background_runs.is_active(it.run_id):
                return {"plan": plan.id, "state": plan.state, "gestart": None, "wacht_op": it.ticket_id}
            it.state = "failed"
            it.error = "De run is verdwenen (herstart van de backend?)"
            it.finished_at = _now_iso()
            self.db.commit()

        volgende = next((i for i in items if i.state in ("waiting", "blocked")), None)
        if volgende is None:
            plan.state = "done"
            plan.finished_at = plan.updated_at = _now_iso()
            plan.note = None
            self.db.commit()
            log.infox("Planning klaar", plan=plan.id)
            return {"plan": plan.id, "state": "done", "gestart": None}

        ticket = self.db.get(Ticket, volgende.ticket_id)
        if ticket is None:
            volgende.state = "skipped"
            volgende.error = "Ticket bestaat niet meer"
            volgende.finished_at = _now_iso()
            self.db.commit()
            return await self.advance(plan.id)

        open_keys = self.blokkades(ticket)
        if open_keys:
            volgende.state = "blocked"
            plan.state = "paused"
            plan.note = (f"{ticket.key} wacht op {', '.join(open_keys)}. "
                         f"Zet die eerst klaar, of haal het ticket uit de planning.")
            plan.updated_at = _now_iso()
            self.db.commit()
            log.infox("Planning gepauzeerd door een afhankelijkheid",
                      plan=plan.id, ticket=ticket.key, wacht_op=open_keys)
            return {"plan": plan.id, "state": "paused", "gestart": None,
                    "geblokkeerd": ticket.key, "wacht_op": open_keys}

        bezet_door = self._lab_bezet(plan)
        if bezet_door is not None:
            # Niet pauzeren: dit is geen probleem maar een wachtrij. Zodra de
            # andere planning een ticket afrondt, komt deze vanzelf aan bod.
            return {"plan": plan.id, "state": plan.state, "gestart": None,
                    "wacht_op_planning": bezet_door}

        if plan.state == "scheduled":
            plan.state = "running"
        if not plan.started_at:
            plan.started_at = _now_iso()
        volgende.state = "running"
        volgende.started_at = _now_iso()
        plan.note = None
        plan.updated_at = _now_iso()
        self.db.commit()

        try:
            res = await start_ticket_run(self.db, ticket.id,
                                         extra_instruction=plan.instruction,
                                         trigger=f"planning '{plan.name}'")
        except HTTPException as exc:
            volgende.state = "failed"
            volgende.error = str(exc.detail)[:2000]
            volgende.finished_at = _now_iso()
            plan.state = "paused"
            plan.note = f"{ticket.key} kon niet starten: {str(exc.detail)[:300]}"
            plan.updated_at = _now_iso()
            self.db.commit()
            log.warningx("Ticket uit planning kon niet starten", plan=plan.id,
                         ticket=ticket.key, error=str(exc.detail)[:200])
            return {"plan": plan.id, "state": "paused", "gestart": None,
                    "fout": str(exc.detail)}

        volgende.run_id = res.get("run_id")
        self.db.commit()
        background_runs.on_finish(res["run_id"], _maak_afloop_hook(plan.id, volgende.id))
        return {"plan": plan.id, "state": plan.state, "gestart": ticket.key,
                "run_id": res.get("run_id")}

    # ── sturen ──────────────────────────────────────────────────────────────

    def pause(self, plan_id: int, *, note: str = "Handmatig gepauzeerd") -> TicketPlan:
        plan = self.get(plan_id)
        if plan.state in ("done", "cancelled"):
            raise HTTPException(status_code=409, detail="Deze planning is al afgelopen")
        plan.state = "paused"
        plan.note = note
        plan.updated_at = _now_iso()
        self.db.commit()
        return plan

    async def resume(self, plan_id: int) -> Dict[str, Any]:
        plan = self.get(plan_id)
        if plan.state in ("done", "cancelled"):
            raise HTTPException(status_code=409, detail="Deze planning is al afgelopen")
        # Een geblokkeerd item mag het opnieuw proberen: misschien is dat
        # andere ticket inmiddels wél klaar — dat is meestal juist de reden
        # dat iemand op hervatten drukt.
        for it in self.items(plan.id):
            if it.state == "blocked":
                it.state = "waiting"
        plan.state = "running"
        plan.note = None
        plan.updated_at = _now_iso()
        self.db.commit()
        return await self.advance(plan.id)

    def cancel(self, plan_id: int) -> TicketPlan:
        """Afbreken laat lopend werk met rust: een run halverwege afkappen
        levert een half ticket en een verwarrend verslag. Wat nog wacht wordt
        overgeslagen."""
        plan = self.get(plan_id)
        for it in self.items(plan.id):
            if it.state in ("waiting", "blocked"):
                it.state = "skipped"
                it.finished_at = _now_iso()
        plan.state = "cancelled"
        plan.finished_at = plan.updated_at = _now_iso()
        self.db.commit()
        return plan

    def reorder(self, plan_id: int, item_ids: List[int]) -> List[TicketPlanItem]:
        items = {i.id: i for i in self.items(plan_id)}
        for i, item_id in enumerate(item_ids):
            it = items.get(int(item_id))
            if it is not None:
                it.position = float((i + 1) * 100)
        self.db.commit()
        return self.items(plan_id)

    def remove_item(self, plan_id: int, item_id: int) -> None:
        it = self.db.get(TicketPlanItem, item_id)
        if it is None or it.plan_id != plan_id:
            raise HTTPException(status_code=404, detail="Regel niet gevonden")
        if it.state == "running":
            raise HTTPException(status_code=409,
                                detail="Dit ticket draait nu — pauzeer de planning of wacht het af")
        self.db.delete(it)
        self.db.commit()

    # ── serialiseren ────────────────────────────────────────────────────────

    def to_dict(self, plan: TicketPlan, *, with_items: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": plan.id, "board_id": plan.board_id, "name": plan.name,
            "state": plan.state, "start_at": plan.start_at, "source": plan.source,
            "instruction": plan.instruction, "note": plan.note,
            "created_at": plan.created_at, "updated_at": plan.updated_at,
            "started_at": plan.started_at, "finished_at": plan.finished_at,
        }
        items = self.items(plan.id)
        telling: Dict[str, int] = {}
        for it in items:
            telling[it.state] = telling.get(it.state, 0) + 1
        out["counts"] = telling
        out["total"] = len(items)
        if with_items:
            tickets = {t.id: t for t in self.db.query(Ticket)
                       .filter(Ticket.id.in_([i.ticket_id for i in items] or [0])).all()}
            out["items"] = []
            for it in items:
                t = tickets.get(it.ticket_id)
                out["items"].append({
                    "id": it.id, "ticket_id": it.ticket_id,
                    "ticket_key": t.key if t else None,
                    "ticket_title": t.title if t else "(verwijderd)",
                    "depends_on": list(getattr(t, "depends_on", None) or []) if t else [],
                    "position": it.position, "state": it.state, "run_id": it.run_id,
                    "error": it.error, "started_at": it.started_at,
                    "finished_at": it.finished_at,
                })
        return out


def _maak_afloop_hook(plan_id: int, item_id: int):
    """Wanneer de run van dit item klaar is: de uitslag vastleggen en meteen
    door naar het volgende. Het doorpakken gebeurt in een eigen taak — de hook
    draait in de sessie van de aflopende run, en die wil je niet openhouden
    voor de hele rest van de planning."""
    def _hook(db: Session, run) -> None:
        item = db.get(TicketPlanItem, item_id)
        if item is None:
            return
        status = getattr(run, "status", None) or "failed"
        item.state = "done" if status == "completed" else "failed"
        if status != "completed":
            item.error = (getattr(run, "error", None) or f"Run eindigde als '{status}'")[:2000]
        item.finished_at = _now_iso()
        plan = db.get(TicketPlan, plan_id)
        if plan is not None:
            plan.updated_at = _now_iso()
        db.commit()
        _plan_verder(plan_id)
    return _hook


def _plan_verder(plan_id: int) -> None:
    """Volgende ticket in gang zetten, in een eigen taak met een eigen sessie."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warningx("Planning niet voortgezet: geen event loop", plan=plan_id)
        return
    loop.create_task(_advance_worker(plan_id))


async def _advance_worker(plan_id: int) -> None:
    from db.database import SessionLocal
    db = SessionLocal()
    try:
        await PlanService(db).advance(plan_id)
    except Exception as exc:  # noqa: BLE001 — een achtergrondtaak heeft geen aanroeper
        log.warningx("Planning voortzetten mislukt", plan=plan_id, error=str(exc)[:300])
    finally:
        db.close()


async def tick() -> int:
    """Scheduler-tick: planningen die aan de beurt zijn losmaken, en lopende
    planningen die stilvielen weer aanschoppen.

    Dat tweede is het vangnet: normaal duwt de afloop-hook de planning door,
    maar na een herstart van de backend is die hook weg, en een planning die
    op een bezet lab stond te wachten heeft niemand die hem wekt."""
    from db.database import SessionLocal

    db = SessionLocal()
    aantal = 0
    try:
        svc = PlanService(db)
        nu = _now_iso()
        due = (db.query(TicketPlan)
               .filter(TicketPlan.state == "scheduled",
                       TicketPlan.start_at.isnot(None),
                       TicketPlan.start_at <= nu).all())
        for plan in due:
            log.infox("Geplande planning gestart", plan=plan.id, gepland_voor=plan.start_at)
            await svc.advance(plan.id)
            aantal += 1
        for plan in db.query(TicketPlan).filter(TicketPlan.state == "running").all():
            res = await svc.advance(plan.id)
            if res.get("gestart"):
                aantal += 1
    except Exception as exc:  # noqa: BLE001
        log.warningx("Planning-tick mislukt", error=str(exc)[:300])
    finally:
        db.close()
    return aantal
