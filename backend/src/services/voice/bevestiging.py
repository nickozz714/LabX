"""Bevestigen van een schrijfactie uit de spraaklaag.

Twee dingen staan of vallen hiermee, en ze zijn allebei subtiel.

**De zin komt van de server.** Het model roept een tool aan, de backend lost de
verwijzing op ("het Holland Malt ticket binnen Swinkels" → SWI-141) en stelt
dáár de bevestigingszin uit samen — uit de parameters die hij werkelijk gaat
uitvoeren. Het model leest die zin alleen voor. Schrijft het model de zin zelf,
dan kan het iets anders voorlezen dan het uitvoert, en zeg je ja tegen de goede
zin met de verkeerde actie als gevolg.

**Het woord moet het HELE antwoord zijn.** Niet "bevat het woord", want dan
bevestigt "Henk, kun jij even kijken?" een actie terwijl je tegen een collega
praat. Dat is geen theoretisch geval: juist een naam als bevestigingswoord
maakt het waarschijnlijk.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

# Zo lang blijft een bevestiging staan. Daarna is "ja" een gok naar wat er ook
# alweer gevraagd werd, dus dan vervalt hij en vraagt de assistent opnieuw.
VERVAL_SECONDEN = 20

# Wat er zonder eigen woord geldt. Bewust GEEN "ja": dat is precies het woord
# dat een omstander per ongeluk zegt.
STANDAARD_WOORD = "bevestigd"

_WEG = re.compile(r"[^\w\s]", re.UNICODE)


def normaliseer(tekst: Optional[str]) -> str:
    """Kleine letters, leestekens weg, accenten weg, spaties samengetrokken.

    Accenten eruit omdat een transcriptie "Henk" en "Hènk" door elkaar haalt;
    dat verschil mag niet bepalen of je actie doorgaat.
    """
    if not tekst:
        return ""
    plat = unicodedata.normalize("NFKD", str(tekst))
    plat = "".join(c for c in plat if not unicodedata.combining(c))
    plat = _WEG.sub(" ", plat.lower())
    return " ".join(plat.split())


def is_bevestiging(antwoord: Optional[str], woord: Optional[str]) -> bool:
    """Bevestigt dit antwoord, ja of nee.

    Alleen als het genormaliseerde antwoord exact het genormaliseerde woord is.
    Alles anders — een zin eromheen, een ander woord, stilte — telt als niet
    bevestigd, en de actie verloopt gewoon.
    """
    gezocht = normaliseer(woord) or normaliseer(STANDAARD_WOORD)
    return bool(gezocht) and normaliseer(antwoord) == gezocht


def vervalt_op(vanaf: Optional[datetime] = None) -> str:
    moment = (vanaf or datetime.now(timezone.utc)) + timedelta(seconds=VERVAL_SECONDEN)
    return moment.isoformat()


def is_verlopen(expires_at: Optional[str], nu: Optional[datetime] = None) -> bool:
    if not expires_at:
        return True
    try:
        grens = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if grens.tzinfo is None:
        grens = grens.replace(tzinfo=timezone.utc)
    return (nu or datetime.now(timezone.utc)) >= grens


def hoe_bevestigen(stand: Optional[str], woord: Optional[str]) -> str:
    """De staartzin die vertelt hóé je bevestigt, passend bij de instelling.

    Zonder dit sta je "Henk" te roepen tegen een systeem dat in deze stand
    alleen naar een klik luistert.
    """
    w = (woord or STANDAARD_WOORD).strip()
    if stand == "klik":
        return "Bevestig je het op het scherm?"
    if stand == "spraak":
        return f"Zeg '{w}' om door te gaan."
    return f"Zeg '{w}' of bevestig het op het scherm."
