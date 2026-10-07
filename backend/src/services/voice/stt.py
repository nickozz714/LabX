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

# whisper-1 en niet gpt-4o-mini-transcribe. Gemeten op vijf Nederlandse
# zinnen uit dit domein: mini maakte van "Wat loopt er nu op het bord
# Platformwerk" -> "Zo vloopt er nu", en van "pipelines" -> "piepelines".
# whisper-1 haalde dezelfde vijf zinnen foutloos. Twee keer zo duur
# ($0,006 tegen $0,003 per minuut), en dat is het dubbel en dwars waard:
# een transcriptie die je zin verminkt kost een hele beurt opnieuw.
STANDAARD_MODEL = "whisper-1"

# Groter dan dit is geen spraakcommando meer maar een opname. Weigeren is
# beter dan er stilzwijgend geld aan uitgeven.
MAX_BYTES = 8 * 1024 * 1024

# Woorden die in dit domein steeds terugkomen maar niet in de database staan.
# Zonder deze kwam er "piepelines" en "lapfabrik" uit.
VAKTERMEN = (
    "pipeline", "pipelines", "lakehouse", "notebook", "workspace",
    "deployment", "repository", "branch", "commit", "schema", "dataset",
    "bronzen laag", "zilveren laag", "gouden laag", "workflow", "scheduling",
    "agent", "ticket", "bord", "lab", "skill",
)

# whisper-1 gebruikt alleen de LAATSTE 224 tokens van de prompt. Een te lange
# lijst duwt daarmee juist de termen eruit die je wilde meegeven -- vandaar
# deze grens, ruim onder dat plafond.
MAX_HINT_TEKENS = 700


def _woordenlijst(db: Session) -> str:
    """De eigennamen en vaktermen die in dit systeem voorkomen.

    Dit is de belangrijkste knop aan de transcriptiekwaliteit, en dat is
    gemeten: met een kale opsomming van bordnamen kwam er "Zo vloopt er nu op
    het bord Platformwerk" uit, met deze lijst de zin zoals hij bedoeld was.
    Naast de namen uit de database gaan de woorden uit TICKETTITELS mee --
    daar zitten de termen in die je werkelijk uitspreekt ("zilveren
    pipelines"), en die staan nergens anders.
    """
    from models.board import Board, Ticket
    from models.lab import Lab
    from services.voice.opzoeken import _STOPWOORDEN

    namen: List[str] = []
    titelwoorden: List[str] = []
    try:
        for b in db.query(Board).all():
            if b.name:
                namen.append(b.name)
            if b.key_prefix:
                namen.append(b.key_prefix)
        namen += [l.name for l in db.query(Lab).all() if l.name]
        namen += sorted({t[0] for t in db.query(Ticket.project).distinct().all() if t[0]})

        for (titel,) in db.query(Ticket.title).limit(300).all():
            for woord in (titel or "").replace("-", " ").split():
                schoon = woord.strip(".,:;()[]\"'").lower()
                if len(schoon) > 3 and schoon not in _STOPWOORDEN:
                    titelwoorden.append(schoon)
    except Exception as exc:  # noqa: BLE001 — zonder hint werkt het ook, alleen slechter
        log.warningx("Woordenlijst opbouwen mislukt", error=str(exc)[:200])

    # Eigennamen eerst: die zijn het meest waard en mogen niet wegvallen als
    # we straks moeten afkappen.
    uniek = list(dict.fromkeys(
        [w.strip() for w in namen if w and w.strip()]
        + list(VAKTERMEN)
        + list(dict.fromkeys(titelwoorden))))
    return ", ".join(uniek)[:MAX_HINT_TEKENS].rstrip(", ")


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

    velden = {"model": (None, model), "language": (None, "nl")}

    # De hint is géén instructie maar een VOORBEELD van hoe de tekst eruit
    # hoort te zien. Dat de zin met context begint is niet cosmetisch: met een
    # kale opsomming kwam er "Zo vloopt er nu" uit, met deze zin de juiste
    # tekst. Het model stemt zich af op het register, niet alleen op de woorden.
    stukjes = ["Een gesprek over softwareontwikkeling en dataplatformen, "
               "in het Nederlands."]
    hint = _woordenlijst(db)
    if hint:
        stukjes.append(f"Termen die voorkomen: {hint}.")
    stukjes.append("Ticketsleutels schrijf je met een streepje en een nummer, "
                   "zoals PLAT-12 of KRI-114.")
    velden["prompt"] = (None, " ".join(stukjes))

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {_sleutel(db)}"},
            files={"file": (f"spraak.{extensie}", data, mime), **velden})
    if resp.status_code >= 400:
        raise RuntimeError(f"Transcriberen mislukt ({resp.status_code}): "
                           f"{resp.text[:200]}")
    return str((resp.json() or {}).get("text") or "").strip()
