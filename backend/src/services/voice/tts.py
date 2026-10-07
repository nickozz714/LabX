"""Voorlezen met een menselijke stem.

De browser kan zelf voorlezen (`speechSynthesis`) en dat is gratis, werkt
offline en heeft geen sleutel nodig. Maar het klinkt als een machine, en als
je de hele dag naar dit ding luistert gaat dat tegenstaan.

Daarom deze tweede weg: dezelfde OpenAI-sleutel die de spraak al verstaat,
leest ook voor. Dat kost geld per zin, dus het is een keuze in de
instellingen en geen stilzwijgende standaard -- en wat het kost telt mee in
het dagplafond, net als de rest.

De audio gaat als bytes naar de browser en wordt daar afgespeeld. Niet
opslaan: een antwoord is één keer relevant, en een map vol mp3's met
klantnamen erin is precies wat je niet wilt hebben rondslingeren.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

STANDAARD_MODEL = "gpt-4o-mini-tts"
STANDAARD_STEM = "coral"

# De stemmen die de spraak-API kent. Vast in de code: een onbekende naam
# levert anders pas bij het afspelen een fout op.
STEMMEN = ("alloy", "ash", "ballad", "coral", "echo", "fable",
           "nova", "onyx", "sage", "shimmer", "verse")

# Zonder sturing leest het model voor als een omroeper. Dit is de toon die
# past bij iemand die even laat weten hoe het ervoor staat.
INSTRUCTIE = (
    "Spreek Nederlands als een rustige, prettige collega. Natuurlijk tempo, "
    "gewone spreektaal, niet overdreven enthousiast en niet als een "
    "voorleesstem. Laat korte zinnen ook kort klinken."
)

# Langer dan dit is geen antwoord meer maar een voordracht, en dan loopt het
# geld harder dan het gesprek.
MAX_TEKENS = 1200

# Ruwe schatting voor de kostenteller: gpt-4o-mini-tts rekent per token
# gegenereerde audio, wat neerkomt op grofweg anderhalve cent per minuut
# spraak. Bij ongeveer 15 tekens per seconde is dat deze prijs per teken.
# Bedoeld om het dagplafond te laten werken, niet om je factuur na te rekenen.
USD_PER_TEKEN = 0.015 / (60 * 15)


def _sleutel(db: Session) -> str:
    from services.settings_service import get_settings
    from utils.crypto import decrypt

    s = get_settings(db)
    rauw = getattr(s, "openai_key_encrypted", None)
    if not rauw:
        raise RuntimeError("Er staat geen OpenAI-sleutel in de instellingen.")
    return decrypt(rauw)


def geschatte_kosten(tekst: str) -> float:
    return round(len(tekst or "") * USD_PER_TEKEN, 6)


async def spreek_uit(db: Session, tekst: str) -> bytes:
    """Eén zin naar gesproken audio (mp3)."""
    import httpx

    from services.settings_service import get_settings

    schoon = (tekst or "").strip()
    if not schoon:
        raise RuntimeError("Er is niets om voor te lezen.")
    schoon = schoon[:MAX_TEKENS]

    s = get_settings(db)
    stem = (s.voice_tts_stem or STANDAARD_STEM).strip().lower()
    if stem not in STEMMEN:
        log.warningx("Onbekende stem, terug naar de standaard", stem=stem)
        stem = STANDAARD_STEM

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {_sleutel(db)}",
                     "Content-Type": "application/json"},
            json={
                "model": s.voice_tts_model or STANDAARD_MODEL,
                "voice": stem,
                "input": schoon,
                "instructions": INSTRUCTIE,
                "response_format": "mp3",
            })
    if resp.status_code >= 400:
        raise RuntimeError(f"Voorlezen mislukt ({resp.status_code}): "
                           f"{resp.text[:200]}")
    return resp.content
