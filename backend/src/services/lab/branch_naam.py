"""services/lab/branch_naam.py — van een titel naar een branchnaam.

Je typt "Zoekfilter op het overzicht"; git wil `feature/zoekfilter-op-het-
overzicht`. Die vertaling hoort op één plek, want ze moet op twee plekken
hetzelfde doen: in het scherm (je ziet vooraf wat de branch gaat heten) en in
de backend (die hem werkelijk aanmaakt). Lopen die uiteen, dan maak je een
andere branch dan je dacht -- en dat merk je pas bij de push.

Wat git NIET accepteert, en waar een titel zo in loopt: spaties, een dubbele
punt, een vraagteken, een tilde, een accolade, twee punten achter elkaar, een
naam die op `.lock` eindigt of met een punt begint. En hoofdletters zijn
toegestaan maar vragen om ellende op een bestandssysteem dat niet op
hoofdletters let.
"""
from __future__ import annotations

import re
import unicodedata

#: Waar een nieuwe branch standaard onder valt. Geen regel maar een gewoonte:
#: wie een andere wil, typt hem gewoon zelf.
VOORVOEGSEL = "feature/"
MAX_LENGTE = 80


def slug(titel: str) -> str:
    """De kale naam, zonder voorvoegsel. Leeg als er niets bruikbaars in zit."""
    tekst = unicodedata.normalize("NFKD", titel or "")
    tekst = tekst.encode("ascii", "ignore").decode("ascii").lower()
    tekst = re.sub(r"[^a-z0-9]+", "-", tekst).strip("-")
    tekst = re.sub(r"-{2,}", "-", tekst)[:MAX_LENGTE].strip("-")
    # Een naam die op `.lock` eindigt botst met git's eigen lockbestanden.
    return "" if tekst in ("", "lock") else tekst


def uit_titel(titel: str, *, voorvoegsel: str = VOORVOEGSEL) -> str:
    """De volledige branchnaam voor deze titel, of "" als die er niet is."""
    kern = slug(titel)
    return f"{voorvoegsel}{kern}" if kern else ""


#: Wat git als refnaam accepteert, streng genomen. Geen spaties, geen `..`,
#: niet beginnen of eindigen met een schuine streep of punt.
_GELDIG = re.compile(r"^(?!/)(?!.*//)(?!.*\.\.)(?!.*@\{)[A-Za-z0-9._/-]+(?<![/.])$")


def geldig(naam: str) -> bool:
    naam = (naam or "").strip()
    if not naam or len(naam) > 255 or naam.endswith(".lock"):
        return False
    return bool(_GELDIG.fullmatch(naam))
