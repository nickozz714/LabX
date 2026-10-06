"""Audio naar tekst, voor het pijplijn-brein.

Via OpenAI en niet lokaal: Whisper op de doelhardware (een i5 uit 2012 zonder
GPU) doet er seconden over een seconde spraak, en dan is het gesprek dood. De
afweging staat in het hive-besluit.

Wat hier wél lokaal gebeurt, is de woordenlijst. Een transcriptie maakt van
"KRI-114" anders moeiteloos "krie honderdveertien", en daar hangt de hele
functie op. De prefixen, klantnamen en projectnamen komen uit de database en
gaan als hint mee.
"""
from __future__ import annotations

import base64
import binascii
from typing import List, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

STANDAARD_MODEL = "gpt-4o-mini-transcribe"
# Groter dan dit is geen spraakcommando meer maar een opname. Weigeren is
# beter dan er stilzwijgend geld aan uitgeven.
MAX_BYTES = 8 * 1024 * 1024


def _woordenlijst(db: Session) -> str:
    """De eigennamen die in dit systeem voorkomen, als hint voor de transcriptie."""
    from models.board import Board, Ticket
    from models.lab import Lab

    woorden: List[str] = []
    try:
        for b in db.query(Board).all():
            if b.name:
                woorden.append(b.name)
            if b.key_prefix:
                woorden.append(b.key_prefix)
        woorden += [l.name for l in db.query(Lab).all() if l.name]
        projecten = {t[0] for t in db.query(Ticket.project).distinct().all() if t[0]}
        woorden += sorted(projecten)
    except Exception as exc:  # noqa: BLE001 — zonder hint werkt het ook, alleen slechter
        log.warningx("Woordenlijst opbouwen mislukt", error=str(exc)[:200])

    uniek = list(dict.fromkeys(w.strip() for w in woorden if w and w.strip()))
    return ", ".join(uniek[:60])


def _sleutel(db: Session) -> str:
    from services.settings_service import get_settings
    from utils.crypto import decrypt

    s = get_settings(db)
    rauw = getattr(s, "openai_key_encrypted", None)
    if not rauw:
        raise RuntimeError("Er staat geen OpenAI-sleutel in de instellingen.")
    return decrypt(rauw)


async def transcribeer_base64(db: Session, audio_base64: str,
                              mime: str = "audio/webm") -> str:
    """Eén fragment spraak naar tekst."""
    import httpx

    try:
        data = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"De audio is niet leesbaar: {exc}")
    if not data:
        raise RuntimeError("Er is geen audio ontvangen.")
    if len(data) > MAX_BYTES:
        raise RuntimeError("Dit fragment is te lang voor een spraakcommando.")

    from services.settings_service import get_settings
    model = getattr(get_settings(db), "voice_stt_model", None) or STANDAARD_MODEL

    extensie = "webm"
    if "mp4" in mime or "m4a" in mime:
        extensie = "mp4"
    elif "wav" in mime:
        extensie = "wav"
    elif "ogg" in mime:
        extensie = "ogg"

    hint = _woordenlijst(db)
    velden = {"model": (None, model), "language": (None, "nl")}
    if hint:
        # De hint is géén instructie maar een voorbeeld van hoe de woorden
        # geschreven horen te worden.
        velden["prompt"] = (None, f"Termen die kunnen voorkomen: {hint}.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {_sleutel(db)}"},
            files={"file": (f"spraak.{extensie}", data, mime), **velden})
    if resp.status_code >= 400:
        raise RuntimeError(f"Transcriberen mislukt ({resp.status_code}): "
                           f"{resp.text[:200]}")
    return str((resp.json() or {}).get("text") or "").strip()
