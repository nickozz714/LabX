"""
services/agent/limiet.py

Herkennen dat de CLI stopte op een GEBRUIKSLIMIET, en wanneer die weer opengaat.

Waarom dit een eigen bestand is: een limiet is geen fout. Alles eromheen werkt,
het werk is niet stuk, en over een uur kan het gewoon verder. Behandel je hem
wél als fout, dan komt een ticket in de mislukt-hoek terecht, stopt de planning
en moet iemand later uitzoeken wat er aan de hand was — terwijl het antwoord
"even wachten" is.

De vorm waarin de CLI het meldt is niet één ding, en dat is precies de reden om
hem hier apart te houden in plaats van in een `if "limit" in tekst` verderop:

    You've hit your session limit · resets 1:40pm (UTC)
    Claude AI usage limit reached|1757600400
    5-hour limit reached ∙ resets 3pm
    Your limit will reset at 15:00

De tijd staat er dus soms als epoch, soms als klok-tijd zonder datum. Bij dat
laatste is "de eerstvolgende keer dat het die tijd is" de enige redelijke
lezing — en dan hoort er een marge bij, want een agent die op de seconde
opnieuw begint, loopt meteen weer tegen dezelfde muur.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from component_logging import get_logger

log = get_logger(__name__)

# Zoveel later dan de opgegeven hersteltijd wordt er opnieuw begonnen. Op de
# seconde herstarten betekent dat de klok van de server en die van de dienst
# het precies eens moeten zijn, en dat zijn ze nooit.
MARGE_SECONDEN = 90

# Wat er gebeurt als er een limiet is maar GEEN tijd bij staat. Lang genoeg om
# niet in een lus te belanden, kort genoeg dat het werk dezelfde dag verdergaat.
BLIND_WACHTEN_MINUTEN = 60

_IS_LIMIET = re.compile(
    r"(session|usage|rate)\s*limit"          # session limit, usage limit, rate limit
    r"|limit\s*(reached|exceeded)"           # limit reached/exceeded
    r"|(hit|reached)\s+your\s+.*limit"       # you've hit/reached your ... limit
    r"|\d+\s*-?\s*hour\s+limit",             # 5-hour limit
    re.I)

# Epoch achter een pijp: "Claude AI usage limit reached|1757600400"
_EPOCH = re.compile(r"\|\s*(\d{9,11})\b")
# Kloktijd: "resets 1:40pm (UTC)", "resets 3pm", "reset at 15:00"
_KLOK = re.compile(
    r"(?:resets?|reset\s+at)\D{0,12}?"
    r"(?P<uur>\d{1,2})(?::(?P<min>\d{2}))?\s*(?P<ampm>am|pm)?"
    r"\s*(?:\((?P<zone>[A-Za-z/_+\-0-9]{2,20})\))?",
    re.I)


def is_limiet(tekst: Optional[str]) -> bool:
    return bool(_IS_LIMIET.search(tekst or ""))


def hersteltijd(tekst: Optional[str], *, nu: Optional[datetime] = None) -> datetime:
    """Wanneer mag het weer? Altijd een antwoord — bij twijfel over een uur.

    Nooit None teruggeven is met opzet: de aanroeper moet het werk kunnen
    inplannen, en "ik weet het niet" zou daar alsnog een fout van maken.
    """
    nu = nu or datetime.now(timezone.utc)
    tekst = tekst or ""

    m = _EPOCH.search(tekst)
    if m:
        try:
            tijd = datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
            # Een epoch uit het verleden of belachelijk ver weg is geen
            # hersteltijd maar een toevallig getal achter een pijp.
            if nu <= tijd <= nu + timedelta(hours=24):
                return tijd + timedelta(seconds=MARGE_SECONDEN)
        except (ValueError, OSError, OverflowError):
            pass

    m = _KLOK.search(tekst)
    if m:
        tijd = _klok_naar_moment(m, nu)
        if tijd is not None:
            return tijd + timedelta(seconds=MARGE_SECONDEN)

    log.warningx("Sessielimiet zonder leesbare hersteltijd", tekst=tekst[:200])
    return nu + timedelta(minutes=BLIND_WACHTEN_MINUTEN)


def _klok_naar_moment(m: "re.Match[str]", nu: datetime) -> Optional[datetime]:
    """Een kloktijd zonder datum -> het eerstvolgende moment dat het die tijd is.

    De zone die erbij staat wordt genegeerd behalve als hij UTC is: LabX draait
    in UTC, de CLI meldt in UTC, en een willekeurige afkorting ("PST", "CEST")
    betrouwbaar omzetten kan niet zonder tijdzonedatabase-gedoe waar de winst
    niet tegenop weegt. Zit ernaast, dan begint het werk hooguit een paar uur
    later opnieuw — en dat is nog altijd beter dan een ticket dat als mislukt
    blijft staan.
    """
    try:
        uur = int(m.group("uur"))
        minuut = int(m.group("min") or 0)
    except (TypeError, ValueError):
        return None
    ampm = (m.group("ampm") or "").lower()
    if ampm == "pm" and uur < 12:
        uur += 12
    elif ampm == "am" and uur == 12:
        uur = 0
    if not (0 <= uur <= 23 and 0 <= minuut <= 59):
        return None

    doel = nu.replace(hour=uur, minute=minuut, second=0, microsecond=0)
    if doel <= nu:
        # Al geweest vandaag: dan is het morgen. Een limiet die "om 1:40"
        # opengaat terwijl het 15:00 is, gaat over de nacht heen.
        doel += timedelta(days=1)
    return doel


class SessieLimiet(Exception):
    """De CLI stopte op een gebruikslimiet.

    Draagt de hersteltijd mee zodat de aanroeper het werk kan inplannen in
    plaats van het als mislukt weg te zetten.
    """

    def __init__(self, melding: str, resets_at: datetime):
        self.melding = melding
        self.resets_at = resets_at
        super().__init__(self.leesbaar())

    def leesbaar(self) -> str:
        return (f"Gepauzeerd op een gebruikslimiet; het werk gaat automatisch verder "
                f"om {self.resets_at.strftime('%H:%M')} UTC. "
                f"Oorspronkelijke melding: {self.melding[:300]}")


def als_limiet(melding: str, *, nu: Optional[datetime] = None) -> Optional[SessieLimiet]:
    """Maak er een SessieLimiet van als de tekst er een is, anders None."""
    if not is_limiet(melding):
        return None
    return SessieLimiet(melding, hersteltijd(melding, nu=nu))
