"""Fish Audio als alternatief voor OpenAI bij verstaan en voorlezen.

Twee losse keuzes, allebei per aanbieder in te stellen: wie verstaat jouw
spraak, en wie leest het antwoord voor. Je kunt ze dus ook mengen -- OpenAI
voor verstaan en Fish voor de stem, bijvoorbeeld -- en zo vergelijken zonder
alles tegelijk om te gooien.

Twee dingen om te weten voordat je dit in gebruik neemt:

1. **Fish kent geen woordenlijst bij het verstaan.** De OpenAI-kant krijgt een
   hint mee met de bordprefixen en klantnamen, en dat is precies wat van
   "Plato" weer "PLAT-2" maakt. Fish accepteert alleen een taalcode. Reken er
   dus op dat ticketsleutels er slechter uitkomen; dat is te meten, niet te
   gokken.
2. **De gratis laag van Fish is voor persoonlijk, niet-commercieel gebruik.**
   Prima om de stemkwaliteit te beoordelen, niet om klantwerk mee te doen.
"""
from __future__ import annotations

import base64
import binascii

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

BASIS = "https://api.fish.audio"
STANDAARD_TTS_MODEL = "s2.1-pro"
STANDAARD_STT_MODEL = "transcribe-1-pro"

# Zelfde grens als bij de OpenAI-kant: groter dan dit is geen spraakcommando
# meer maar een opname.
MAX_BYTES = 8 * 1024 * 1024
MAX_TEKENS = 1200

# Fish rekent per UTF-8 byte ($15 per miljoen). Een schatting voor de
# kostenteller en het dagplafond, niet om je factuur mee na te rekenen.
USD_PER_BYTE = 15.0 / 1_000_000


def _sleutel(db: Session) -> str:
    from services.settings_service import get_settings
    from utils.crypto import decrypt

    s = get_settings(db)
    rauw = getattr(s, "fish_key_encrypted", None)
    if not rauw:
        raise RuntimeError("Er staat geen Fish Audio-sleutel in de instellingen.")
    return decrypt(rauw)


def geschatte_kosten(tekst: str) -> float:
    return round(len((tekst or "").encode("utf-8")) * USD_PER_BYTE, 6)


async def spreek_uit(db: Session, tekst: str) -> bytes:
    """Eén zin naar gesproken audio (mp3)."""
    import httpx

    from services.settings_service import get_settings

    schoon = (tekst or "").strip()
    if not schoon:
        raise RuntimeError("Er is niets om voor te lezen.")
    schoon = schoon[:MAX_TEKENS]

    s = get_settings(db)
    lichaam = {"text": schoon, "format": "mp3", "latency": "balanced"}
    # Zonder stem valt Fish terug op zijn standaard; met een reference_id kies
    # je een stem uit de bibliotheek of een eigen gekloonde stem.
    stem = (s.voice_fish_stem or "").strip()
    if stem:
        lichaam["reference_id"] = stem

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASIS}/v1/tts",
            headers={
                "Authorization": f"Bearer {_sleutel(db)}",
                "Content-Type": "application/json",
                # Het model staat in een HEADER, niet in het lichaam -- anders
                # dan bij OpenAI, en makkelijk over het hoofd te zien.
                "model": s.voice_fish_tts_model or STANDAARD_TTS_MODEL,
            },
            json=lichaam)
    if resp.status_code >= 400:
        raise RuntimeError(f"Fish-voorlezen mislukt ({resp.status_code}): "
                           f"{resp.text[:200]}")
    return resp.content


async def transcribeer_base64(db: Session, audio_base64: str,
                              mime: str = "audio/webm") -> str:
    """Eén fragment spraak naar tekst.

    Let op: Fish neemt geen woordenlijst aan. Alleen een taalcode. Wat de
    OpenAI-kant met een hint rechttrekt (ticketsleutels), moet hier van de
    sleutelherkenning in opzoeken.py komen.
    """
    import httpx

    from services.settings_service import get_settings

    try:
        data = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"De audio is niet leesbaar: {exc}")
    if not data:
        raise RuntimeError("Er is geen audio ontvangen.")
    if len(data) > MAX_BYTES:
        raise RuntimeError("Dit fragment is te lang voor een spraakcommando.")

    s = get_settings(db)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASIS}/v1/asr",
            headers={
                "Authorization": f"Bearer {_sleutel(db)}",
                "model": s.voice_fish_stt_model or STANDAARD_STT_MODEL,
            },
            # Fish leidt het formaat af uit de INHOUD, niet uit de bestandsnaam.
            files={"audio": ("spraak", data, mime)},
            data={"language": "nl"})
    if resp.status_code >= 400:
        raise RuntimeError(f"Fish-transcriberen mislukt ({resp.status_code}): "
                           f"{resp.text[:200]}")
    return str((resp.json() or {}).get("text") or "").strip()
