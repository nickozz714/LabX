"""routers/notify_router.py — meldkanalen beheren.

Het geheim (SMTP-wachtwoord of bot-token) is SCHRIJF-only: hij gaat erin via
`secret` en komt nooit terug, net als een Azure-profielgeheim of een
board-token. De GET zegt alleen `has_secret`.

Twee endpoints die geen CRUD zijn maar wel het verschil maken tussen "ingesteld"
en "werkend":

- `POST /{id}/test` probeert echt te verbinden (SMTP-login, IMAP-select, of
  getMe bij Telegram) en zegt per onderdeel wat eruit kwam. Zonder dit merk je
  een verkeerd wachtwoord pas als er een melding had moeten komen en die
  uitbleef — het slechtste moment.
- `POST /telegram/chats` haalt op wie de bot heeft aangeschreven. Dat is de
  enige manier om aan een chat-id te komen: een bot mag niemand als eerste
  benaderen, dus jij stuurt de bot 'hoi' en hier verschijnt hij.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.notification import CHANNEL_KINDS, EVENTS, NotificationChannel, NotificationLog

router = APIRouter(prefix="/notify", tags=["notify"], dependencies=[Depends(require_user)])

# Velden die per soort in `config` mogen. Een allowlist en geen "alles wat
# binnenkomt": config gaat ongewijzigd de database in en wordt later als
# verbindingsgegevens gebruikt.
_CONFIG_VELDEN = {
    "email": ("smtp_host", "smtp_port", "smtp_user", "smtp_tls", "from", "to",
              "imap_host", "imap_port", "imap_user", "imap_folder", "imap_ssl"),
    "telegram": ("chat_id",),
}
# Door LabX zelf bijgehouden posities in de inbox. Die mogen niet uit een
# verzoek komen — anders kan een verkeerde waarde ervoor zorgen dat berichten
# worden overgeslagen of eindeloos opnieuw verwerkt.
_INTERN = ("offset", "last_uid")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(c: NotificationChannel) -> Dict[str, Any]:
    config = {k: v for k, v in (c.config or {}).items() if k not in _INTERN}
    return {
        "id": c.id, "name": c.name, "kind": c.kind, "enabled": c.enabled,
        "config": config, "has_secret": bool(c.secret_encrypted),
        "events": list(c.events or []), "allow_reply": c.allow_reply,
        "last_error": c.last_error, "last_sent_at": c.last_sent_at,
        "last_poll_at": c.last_poll_at,
    }


def _schoon_config(kind: str, binnen: Any, bestaand: Dict[str, Any]) -> Dict[str, Any]:
    toegestaan = _CONFIG_VELDEN.get(kind, ())
    uit = {k: v for k, v in (bestaand or {}).items() if k in _INTERN}
    for veld in toegestaan:
        if isinstance(binnen, dict) and veld in binnen:
            uit[veld] = binnen[veld]
        elif veld in (bestaand or {}):
            uit[veld] = bestaand[veld]
    return uit


def _get(db: Session, channel_id: int) -> NotificationChannel:
    c = db.get(NotificationChannel, channel_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Meldkanaal niet gevonden")
    return c


@router.get("/events")
def list_events():
    """De gebeurtenissen met uitleg, zodat de UI ze niet hoeft te hardcoderen."""
    uitleg = {
        "run_klaar": "Een agent-run is klaar (met de samenvatting)",
        "run_mislukt": "Een agent-run is stukgelopen",
        "aandacht_nodig": "Er wacht iets op jou: ticket geblokkeerd, planning stil, werk niet afgemaakt",
        "planning_klaar": "Een hele planning is afgewerkt",
        "storing": "Iets is structureel mis: lab weg, sync-fout",
    }
    return [{"key": e, "label": uitleg.get(e, e)} for e in EVENTS]


@router.get("/channels")
def list_channels(db: Session = Depends(get_db)):
    rows = db.query(NotificationChannel).order_by(NotificationChannel.id).all()
    return [_dict(c) for c in rows]


@router.post("/channels")
def create_channel(payload: Dict[str, Any], db: Session = Depends(get_db)):
    kind = str(payload.get("kind") or "email")
    if kind not in CHANNEL_KINDS:
        raise HTTPException(status_code=400,
                            detail=f"Onbekend soort '{kind}' — kies uit: {', '.join(CHANNEL_KINDS)}")
    now = _now_iso()
    c = NotificationChannel(
        name=str(payload.get("name") or kind)[:255], kind=kind,
        enabled=bool(payload.get("enabled", True)),
        config=_schoon_config(kind, payload.get("config"), {}),
        events=[e for e in (payload.get("events") or []) if e in EVENTS],
        allow_reply=bool(payload.get("allow_reply", True)),
        created_at=now, updated_at=now)
    geheim = str(payload.get("secret") or "").strip()
    if geheim:
        from utils.crypto import encrypt
        c.secret_encrypted = encrypt(geheim)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _dict(c)


@router.patch("/channels/{channel_id}")
def update_channel(channel_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    c = _get(db, channel_id)
    if "name" in payload:
        c.name = str(payload["name"] or c.kind)[:255]
    if "enabled" in payload:
        c.enabled = bool(payload["enabled"])
    if "allow_reply" in payload:
        c.allow_reply = bool(payload["allow_reply"])
    if "events" in payload:
        c.events = [e for e in (payload.get("events") or []) if e in EVENTS]
    if "config" in payload:
        c.config = _schoon_config(c.kind, payload.get("config"), c.config or {})
    if "secret" in payload:
        geheim = str(payload.get("secret") or "").strip()
        if geheim:
            from utils.crypto import encrypt
            c.secret_encrypted = encrypt(geheim)
        # Leeg meesturen betekent "niet wijzigen", niet "wissen": een formulier
        # dat het geheim niet toont, stuurt het ook niet mee.
    c.updated_at = _now_iso()
    db.commit()
    db.refresh(c)
    return _dict(c)


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    c = _get(db, channel_id)
    db.query(NotificationLog).filter(NotificationLog.channel_id == c.id).delete(
        synchronize_session=False)
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: int, payload: Optional[Dict[str, Any]] = None,
                       db: Session = Depends(get_db)):
    """Verbinden én (als je dat vraagt) echt een bericht sturen."""
    from services.notify.notify_service import _geheim

    c = _get(db, channel_id)
    geheim = _geheim(c)
    uit: Dict[str, Any] = {}
    try:
        if c.kind == "telegram":
            from services.notify import telegram
            uit = await telegram.controleer(token=geheim)
        else:
            from services.notify import mail
            uit = await mail.controleer(config=c.config or {}, wachtwoord=geheim)
    except Exception as exc:  # noqa: BLE001
        uit = {"ok": False, "error": str(exc)[:500]}

    if uit.get("ok") and (payload or {}).get("send"):
        from services.notify.notify_service import stuur
        # Via de gewone weg, zodat de test ook bewijst dat het loggen en het
        # koppelen van een antwoord werkt — niet alleen dat de server bereikbaar
        # is. `alleen_kanaal` omzeilt de abonnementen zonder ze aan te raken.
        uit["verstuurd"] = await stuur(
            "storing", "LabX-testmelding",
            "Dit is een test vanuit LabX. Antwoord hierop om te controleren of de "
            "weg terug ook werkt — je krijgt dan een bevestiging.",
            {"test": True}, alleen_kanaal=c.id)

    c.last_error = None if uit.get("ok") else str(uit.get("error") or uit)[:1000]
    c.updated_at = _now_iso()
    db.commit()
    return uit


@router.post("/telegram/chats")
async def telegram_chats(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Welke chats hebben deze bot aangeschreven? Geef `channel_id` mee voor een
    bestaand kanaal, of `secret` voor een token dat nog niet opgeslagen is."""
    from services.notify import telegram
    from services.notify.notify_service import _geheim

    token = str(payload.get("secret") or "").strip()
    if not token and payload.get("channel_id"):
        token = _geheim(_get(db, int(payload["channel_id"])))
    if not token:
        raise HTTPException(status_code=400, detail="Geen bot-token")
    try:
        chats = await telegram.ontdek_chats(token=token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)[:400])
    return {"chats": chats,
            "hint": ("Geen chats? Stuur de bot in Telegram eerst zelf een bericht — "
                     "een bot mag niemand als eerste aanschrijven.")}


@router.get("/log")
def list_log(limit: int = 30, db: Session = Depends(get_db)):
    rows = (db.query(NotificationLog)
            .order_by(NotificationLog.id.desc()).limit(max(1, min(limit, 200))).all())
    return [{"id": r.id, "channel_id": r.channel_id, "event": r.event, "title": r.title,
             "status": r.status, "error": r.error, "created_at": r.created_at,
             "context": r.context or {}} for r in rows]
