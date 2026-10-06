"""Verwijzingen uit spraak terugvertalen naar iets in LabX.

Jij zegt "het Holland Malt ticket binnen Swinkels", niet "board_id 1,
ticket_id 141". En een transcriptie maakt van "KRI-114" met enige regelmaat
"krie honderdveertien" of "K R I 114".

Daarom wordt hier niet vertrouwd op een exact gespelde sleutel. Er wordt
getrapt gezocht: eerst een genormaliseerde sleutel, dan op titel. Wat eruit
komt gaat daarna ALTIJD door de bevestigingszin, met de gevonden sleutel
erin — dus een verkeerde treffer hoor je voordat er iets gebeurt. Dat is de
reden dat ruim zoeken hier veilig is.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Sequence, Tuple

# Woorden die niets zeggen over wélk ticket je bedoelt. Een stopwoordenlijst
# en geen lengtefilter: "het" en "een" zijn drie letters en zouden er zo
# doorheen glippen, terwijl "D3", "MLV" en "BSN" juist kort én betekenisvol
# zijn. Filteren op lengte gooit precies de verkeerde helft weg.
_STOPWOORDEN = {
    "de", "het", "een", "en", "of", "van", "voor", "met", "op", "in", "bij",
    "aan", "naar", "dat", "die", "deze", "dit", "er", "is", "zijn", "was",
    "ticket", "tickets", "board", "bord", "lab", "workflow", "agent",
    "even", "maar", "nog", "wel", "niet", "ook", "al", "dan", "als",
}

# Hoe getallen worden uitgesproken. Alleen wat je in een ticketnummer
# tegenkomt; dit is geen volwaardige getalparser en hoeft dat niet te zijn.
_GETALLEN = {
    "nul": 0, "een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5,
    "zes": 6, "zeven": 7, "acht": 8, "negen": 9, "tien": 10, "elf": 11,
    "twaalf": 12, "dertien": 13, "veertien": 14, "vijftien": 15, "zestien": 16,
    "zeventien": 17, "achttien": 18, "negentien": 19, "twintig": 20,
    "dertig": 30, "veertig": 40, "vijftig": 50, "zestig": 60, "zeventig": 70,
    "tachtig": 80, "negentig": 90, "honderd": 100, "duizend": 1000,
}


def _plat(tekst: str) -> str:
    plat = unicodedata.normalize("NFKD", str(tekst or ""))
    plat = "".join(c for c in plat if not unicodedata.combining(c))
    return plat.lower()


def _woorden_naar_getal(woorden: Sequence[str]) -> Optional[int]:
    """"honderdveertien" of "honderd veertien" → 114.

    Simpele optel-en-vermenigvuldig: genoeg voor ticketnummers, en bij twijfel
    geeft hij None terug zodat er op titel gezocht wordt.
    """
    totaal, lopend, gezien = 0, 0, False
    for w in woorden:
        if w not in _GETALLEN:
            return None
        waarde = _GETALLEN[w]
        gezien = True
        if waarde in (100, 1000):
            lopend = (lopend or 1) * waarde
            totaal += lopend
            lopend = 0
        else:
            lopend += waarde
    return (totaal + lopend) if gezien else None


def _splits_samengesteld(woord: str) -> Optional[int]:
    """"honderdveertien" als één woord uit de transcriptie."""
    for kop in ("duizend", "honderd"):
        if woord.startswith(kop) and woord != kop:
            rest = _woorden_naar_getal([woord[len(kop):]])
            if rest is not None:
                return _GETALLEN[kop] + rest
    return _GETALLEN.get(woord)


def normaliseer_sleutel(tekst: Optional[str], prefixen: Sequence[str]) -> Optional[str]:
    """Een uitgesproken ticketsleutel terug naar "KRI-114".

    Vangt: "KRI-114", "kri 114", "K R I 114", "krie honderdveertien".
    De prefixen komen uit de borden zelf, dus dit werkt ook voor een bord dat
    morgen bestaat.
    """
    if not tekst:
        return None
    plat = _plat(tekst)
    plat = re.sub(r"[^\w\s]", " ", plat)
    stukken = plat.split()
    if not stukken:
        return None

    bekend = {_plat(p): p for p in prefixen if p}
    # "K R I" als losse letters weer aaneen: pak de langste prefix die past.
    samen = "".join(stukken)
    for plat_prefix, echt in sorted(bekend.items(), key=lambda x: -len(x[0])):
        if not samen.startswith(plat_prefix):
            # "krie" voor "kri": de transcriptie maakt er een woord van.
            if not (stukken and stukken[0].startswith(plat_prefix)):
                continue
        rest = samen[len(plat_prefix):]
        if rest.isdigit():
            return f"{echt}-{int(rest)}"
        # Geen cijfers? Dan staat het nummer er waarschijnlijk uitgeschreven.
        staart = [w for w in stukken[1:] if w]
        getal = _woorden_naar_getal(staart)
        if getal is None and len(staart) == 1:
            getal = _splits_samengesteld(staart[0])
        if getal is not None:
            return f"{echt}-{getal}"
    return None


def _kernwoorden(tekst: str) -> set:
    """De woorden die iets zeggen over wélk ding je bedoelt."""
    return {w for w in _plat(re.sub(r"[^\w\s]", " ", tekst or "")).split()
            if len(w) > 1 and w not in _STOPWOORDEN}


def kies_op_titel(zoek: str, kandidaten: Sequence[Tuple[str, str]],
                  *, drempel: float = 0.45) -> List[Tuple[str, str]]:
    """Tickets waarvan de titel op de gesproken omschrijving lijkt.

    Geen slimme fuzzy-bibliotheek: woordoverlap is hier genoeg en veel beter
    uit te leggen. "het Holland Malt ticket" vindt "Holland Malt — silver
    reload" omdat beide woorden erin staan.

    Geeft ALLE treffers boven de drempel terug, gesorteerd. Wie één antwoord
    forceert waar er twee zijn, kiest de verkeerde helft van de tijd — dus
    hier mag de aanroeper doorvragen.
    """
    gezocht = _kernwoorden(zoek)
    if not gezocht:
        return []
    uit: List[Tuple[float, Tuple[str, str]]] = []
    for sleutel, titel in kandidaten:
        woorden = _kernwoorden(titel)
        if not woorden:
            continue
        overlap = len(gezocht & woorden) / len(gezocht)
        if overlap >= drempel:
            uit.append((overlap, (sleutel, titel)))
    uit.sort(key=lambda x: (-x[0], x[1][0]))
    return [t for _, t in uit]


def kies_op_naam(zoek: Optional[str], namen: Sequence[str]) -> List[str]:
    """Hetzelfde voor borden, labs en workflows: "Krimpenerwaard" → het bord.

    Eerst een exacte treffer (genormaliseerd), dan "begint met", dan "bevat".
    Zonder zoekterm komt alles terug — dan kiest de aanroeper.
    """
    if not zoek or not str(zoek).strip():
        return list(namen)
    z = _plat(zoek).strip()
    exact = [n for n in namen if _plat(n) == z]
    if exact:
        return exact
    begint = [n for n in namen if _plat(n).startswith(z)]
    if begint:
        return begint
    return [n for n in namen if z in _plat(n)]
