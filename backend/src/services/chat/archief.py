"""services/chat/archief.py — chats die stil zijn geworden opzij zetten.

**Archiveren is niet verwijderen.** De thread blijft staan, zijn berichten
blijven staan en zijn CLI-sessie blijft staan; het enige dat verandert is dat
hij niet meer in de chatlijst meeloopt. `GET /chat/threads/{id}` geeft hem
gewoon terug, dus een link vanaf een ticket of uit je geschiedenis blijft
werken, en terughalen is één klik. Precies daarom mag dit vanzelf gebeuren —
bij verwijderen zou automatisch nooit goed voelen.

**Waarom op `updated_at` en niet op "wanneer heb je hem opengehad".** Een chat
openen verandert er niets aan; pas een bericht doet dat. Dat is de bedoeling:
rondkijken houdt een gesprek niet kunstmatig levend, en een chat waarin je
vandaag nog iets gevraagd hebt gaat gegarandeerd niet weg. Haal je er een uit
het archief, dan zet dat `updated_at` op nu — anders zou dezelfde opruimronde
hem een uur later weer opzij zetten.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.thread import Thread

log = get_logger(__name__)

# Drie dagen, en het staat hier en niet in een env-var: dit is gedrag dat je in
# de interface wilt kunnen bijstellen, niet iets om een container voor te
# herstarten. De instelling overschrijft hem; 0 zet het uit.
STANDAARD_DAGEN = 3


def _dagen(db: Session) -> int:
    from services import settings_service

    waarde = getattr(settings_service.get_settings(db), "chat_archive_days", None)
    if waarde is None:
        return STANDAARD_DAGEN
    return max(0, int(waarde))


def archiveer_stille_chats(db: Session, dagen: Optional[int] = None) -> int:
    """Alles wat `dagen` niet is aangeraakt het archief in. Geeft het aantal terug."""
    if dagen is None:
        dagen = _dagen(db)
    if dagen <= 0:
        return 0

    grens = (datetime.now(timezone.utc) - timedelta(days=dagen)).isoformat()
    nu = datetime.now(timezone.utc).isoformat()
    rijen = (db.query(Thread)
             .filter(Thread.archived_at.is_(None), Thread.updated_at < grens)
             .all())
    if not rijen:
        return 0
    for t in rijen:
        t.archived_at = nu
    db.commit()
    log.infox("Stille chats gearchiveerd", aantal=len(rijen), na_dagen=dagen)
    return len(rijen)
