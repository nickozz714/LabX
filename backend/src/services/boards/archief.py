"""Tickets die lang genoeg klaar staan uit het bord halen.

Archiveren, niet verwijderen: het ticket blijft bestaan en blijft te vinden
onder Archief. Dat verschil is het hele punt -- een bord dat volloopt met werk
van vorige maand is onbruikbaar, maar dat werk weggooien is dat ook.

Per bord instelbaar: in welke kolom (meestal "Klaar") en na hoeveel dagen.
Nul dagen is uit, en dat is de standaard: niemand hoort verrast te worden
doordat zijn tickets vanzelf verdwijnen.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


def archiveer_afgeronde_tickets(db: Session, *, board_id: Optional[int] = None) -> int:
    """Alles wat lang genoeg in de ingestelde kolom staat. Geeft het aantal terug."""
    from models.board import Board, Ticket

    borden = db.query(Board).filter(Board.archive_days > 0)
    if board_id is not None:
        borden = borden.filter(Board.id == board_id)

    totaal = 0
    for bord in borden.all():
        kolom = (bord.archive_column or "").strip()
        if not kolom:
            continue
        grens = (datetime.now(timezone.utc)
                 - timedelta(days=int(bord.archive_days))).isoformat()
        rijen = (db.query(Ticket)
                 .filter(Ticket.board_id == bord.id,
                         Ticket.archived_at.is_(None),
                         Ticket.status == kolom,
                         # Op updated_at en niet op created_at: een ticket dat
                         # gisteren klaar werd gezet is niet oud, ook al is het
                         # een maand geleden aangemaakt.
                         Ticket.updated_at < grens)
                 .all())
        if not rijen:
            continue
        nu = _nu()
        for t in rijen:
            t.archived_at = nu
        db.commit()
        totaal += len(rijen)
        log.infox("Tickets gearchiveerd", bord=bord.name, kolom=kolom,
                  aantal=len(rijen), na_dagen=bord.archive_days)
    return totaal


def haal_ten_onrechte_gearchiveerde_terug(db: Session) -> int:
    """Een gearchiveerd ticket dat daarna tóch is aangeraakt hoort terug.

    Zelfde redenering als bij chats: iemand heeft het weer opgepakt, dus de
    aanname "hier gebeurt niets meer" klopte niet.
    """
    from models.board import Ticket

    rijen = (db.query(Ticket)
             .filter(Ticket.archived_at.isnot(None),
                     Ticket.updated_at > Ticket.archived_at).all())
    for t in rijen:
        t.archived_at = None
    if rijen:
        db.commit()
        log.infox("Tickets teruggehaald die na het archiveren nog gebruikt zijn",
                  aantal=len(rijen))
    return len(rijen)
