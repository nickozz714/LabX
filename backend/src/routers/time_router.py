"""routers/time_router.py — urenregistratie.

De vraag die dit beantwoordt is niet "wat heeft LabX gedaan" (dat is het
auditspoor) maar "wat kan ik schrijven". Dat is een andere vraag, met een
ander antwoord: niet de machinetijd maar jouw tijd, en niet bruto opgeteld
maar verdeeld over wat tegelijk liep.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.time_entry import TimeEntry
from services.time.time_service import TimeService

router = APIRouter(prefix="/time", tags=["time"], dependencies=[Depends(require_user)])


def _dto(r: TimeEntry) -> Dict[str, Any]:
    return {
        "id": r.id, "board_id": r.board_id, "ticket_id": r.ticket_id,
        "project": r.project, "category": r.category, "kind": r.kind,
        "minutes": r.minutes, "day": r.day,
        "started_at": r.started_at, "ended_at": r.ended_at,
        "note": r.note, "approved": bool(r.approved),
        "source_run_id": r.source_run_id,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


@router.get("/overzicht")
def overzicht(van: Optional[str] = None, tot: Optional[str] = None,
              board_id: Optional[int] = None,
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Uren per project over een periode, met de verdeling over parallel werk."""
    return TimeService(db).overzicht(van=van, tot=tot, board_id=board_id)


@router.get("/entries")
def entries(van: Optional[str] = None, tot: Optional[str] = None,
            board_id: Optional[int] = None, ticket_id: Optional[int] = None,
            kind: Optional[str] = None, limit: int = 500,
            db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    q = db.query(TimeEntry)
    if van:
        q = q.filter(TimeEntry.day >= van)
    if tot:
        q = q.filter(TimeEntry.day <= tot)
    if board_id:
        q = q.filter(TimeEntry.board_id == board_id)
    if ticket_id:
        q = q.filter(TimeEntry.ticket_id == ticket_id)
    if kind:
        q = q.filter(TimeEntry.kind == kind)
    rijen = q.order_by(TimeEntry.day.desc(), TimeEntry.id.desc()).limit(max(1, min(limit, 2000))).all()
    return [_dto(r) for r in rijen]


@router.post("/entries")
def maak(payload: Dict[str, Any], db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not payload.get("board_id"):
        raise HTTPException(status_code=400, detail="board_id is verplicht")
    regel = TimeService(db).log(
        ticket_id=payload.get("ticket_id"), board_id=int(payload["board_id"]),
        minuten=float(payload.get("minutes") or 0), kind="handmatig",
        project=payload.get("project"), category=payload.get("category"),
        note=payload.get("note"), dag=payload.get("day"))
    return _dto(regel)


@router.patch("/entries/{entry_id}")
def bewerk(entry_id: int, payload: Dict[str, Any],
           db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        return _dto(TimeService(db).bewerk(entry_id, payload))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/entries/{entry_id}")
def verwijder(entry_id: int, db: Session = Depends(get_db)) -> Dict[str, bool]:
    if not TimeService(db).verwijder(entry_id):
        raise HTTPException(status_code=404, detail="Deze tijdregel bestaat niet")
    return {"ok": True}


@router.post("/verzamel")
def verzamel(payload: Optional[Dict[str, Any]] = None,
             db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Agent-runs opnieuw uitlezen en de schattingen bijwerken.

    Bedoeld voor de knop "opnieuw berekenen" en voor het eenmalig vullen van
    de geschiedenis. Veilig om vaker te draaien: gemeten regels zijn idempotent
    op het run-id, en een schatting die je hebt afgetekend blijft staan.
    """
    svc = TimeService(db)
    sinds = (payload or {}).get("sinds")
    gemeten = svc.verzamel_gemeten(sinds=sinds)
    geschat = svc.schat_alles(board_id=(payload or {}).get("board_id"))
    return {"gemeten_nieuw": gemeten, "schattingen": geschat}


@router.get("/suggesties")
def suggesties(db: Session = Depends(get_db)) -> Dict[str, List[str]]:
    """Voor de autoaanvulling: wat je eerder gebruikte."""
    svc = TimeService(db)
    return {"projecten": svc.projecten(), "categorieen": svc.categorieen()}
