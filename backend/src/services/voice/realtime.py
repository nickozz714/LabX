"""Het realtime-brein: een kortlevend token voor de browser.

De browser praat rechtstreeks met OpenAI over WebRTC — dat is wat realtime
snel maakt. Maar dan moet de browser zich kunnen aanmelden, en de API-sleutel
daarvoor gebruiken zou betekenen dat hij in de paginabron staat.

Daarom deze omweg: de server vraagt een `client_secret` aan dat enkele minuten
geldig is en alleen voor deze ene sessie werkt. Dezelfde constructie als in
ND3X (`voice_webrtc_conversation_service`).

De toolaanroepen die het model doet, komen daarna gewoon bij LabX binnen via
`POST /voice/sessies/{id}/tool` — dus ook het realtime-brein kan niet om de
bevestigingslus heen.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.orm import Session

from component_logging import get_logger
from services.voice.bevestiging import STANDAARD_WOORD
from services.voice.schema import TOOLSCHEMA

log = get_logger(__name__)

STANDAARD_MODEL = "gpt-realtime-mini"
STANDAARD_STEM = "marin"

INSTRUCTIES = """\
Je bent de spraakassistent van LabX. Je praat Nederlands met één gebruiker die
zijn handen vol heeft.

- Kort en in spreektaal. Geen opsommingen, geen markdown, geen toolnamen.
- Lees nooit ids, paden of lange codes voor.
- Je doet zelf geen werk: je roept een actie aan en vertelt wat eruit komt.
- Verwijzingen uit het gesprek vul je zelf in ("start daar een agent op").
- Een ticketsleutel (zoals PLAT-2) is al uniek: zoek meteen op en vraag niet
  van welke klant of welk bord het is.
- Twijfel je echt, stel dan één korte vraag.
- Bij een schrijfactie krijg je geen resultaat maar een BEVESTIGINGSZIN terug.
  Lees die letterlijk voor en wacht op antwoord. Formuleer nooit je eigen
  versie van wat er gaat gebeuren, en zeg nooit dat iets gedaan is voordat je
  het resultaat hebt gezien.
- Spreek het bevestigingswoord van de gebruiker NOOIT zelf uit: dan bevestig je
  jezelf via de speakers.
"""


def _sleutel(db: Session) -> str:
    from services.settings_service import get_settings
    from utils.crypto import decrypt

    s = get_settings(db)
    rauw = getattr(s, "openai_key_encrypted", None)
    if not rauw:
        raise RuntimeError("Er staat geen OpenAI-sleutel in de instellingen.")
    return decrypt(rauw)


def _sessie_config(db: Session) -> Dict[str, Any]:
    from services.settings_service import get_settings

    s = get_settings(db)
    woord = (s.voice_woord or STANDAARD_WOORD).strip()
    open_microfoon = s.voice_microfoon == "open"

    return {
        "session": {
            "type": "realtime",
            "model": s.voice_realtime_model or STANDAARD_MODEL,
            "instructions": INSTRUCTIES + (
                f"\nHet bevestigingswoord van deze gebruiker is '{woord}'. "
                f"Spreek dat woord zelf nooit uit."),
            "audio": {
                "input": {
                    # De browser moet kunnen zien wat JIJ zei: daar hangt de
                    # bevestiging aan. De server legt die transcriptie tegen
                    # het bevestigingswoord -- het model beslist dat niet.
                    "transcription": {
                        "model": s.voice_stt_model or "gpt-4o-mini-transcribe",
                        "language": "nl",
                    },
                    # Bij push-to-talk bepaalt de browser wanneer er audio gaat;
                    # dan hoort de server niet zelf te beslissen dat iemand
                    # uitgesproken is. Bij een open microfoon wel.
                    "turn_detection": ({
                        "type": "server_vad",
                        # Hoger dan standaard: een collega verderop in de kamer
                        # is zachter dan jij aan de microfoon. Geen echte
                        # sprekerisolatie — die bestaat niet in deze API — maar
                        # het scheelt.
                        "threshold": 0.65,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 700,
                        "create_response": True,
                        # Niet laten onderbreken door iemand anders die begint
                        # te praten: hij praat zijn zin af.
                        "interrupt_response": False,
                    } if open_microfoon else None),
                },
                "output": {"voice": STANDAARD_STEM},
            },
            "tools": TOOLSCHEMA,
            "tool_choice": "auto",
        }
    }


async def maak_client_secret(db: Session) -> Dict[str, Any]:
    """Een token waarmee de browser één sessie mag opzetten."""
    import httpx

    config = _sessie_config(db)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={"Authorization": f"Bearer {_sleutel(db)}",
                     "Content-Type": "application/json"},
            json=config)
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI weigerde de sessie ({resp.status_code}): "
                           f"{resp.text[:300]}")
    uit = resp.json() or {}
    return {
        "client_secret": uit.get("value") or (uit.get("client_secret") or {}).get("value"),
        "expires_at": uit.get("expires_at"),
        "model": config["session"]["model"],
    }
