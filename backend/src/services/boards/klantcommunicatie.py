"""services/boards/klantcommunicatie.py — wat er NOOIT naar buiten mag.

Een tekst die naar een klant gaat, gaat naar iemand die niets van dit netwerk
weet en er ook niets van hoort te weten. Geen interne ticketsleutels, geen
namen van producten die hier draaien, geen labs, geen branches, geen
servernamen. Die persoon is extern en moet het zonder dat alles begrijpen.

**Waarom een detector en niet alleen een regel in de prompt.** Een regel in
een systeemprompt is een verzoek: een model houdt zich er meestal aan, en
"meestal" is hier niet goed genoeg. Eén keer een interne ticketsleutel in een
mail naar een klant is één keer te veel, en je ziet het pas als het verstuurd
is. Dus staat de regel ÉN in de prompt (zie services/skills/meegeleverd.py)
én in een controle die erna kijkt.

**Waarom de namen uit de database komen en niet uit een lijstje hier.** Wat
er in dit netwerk draait, verandert. Clusters krijgen namen, borden krijgen
voorvoegsels, labs komen en gaan. Een vaste lijst zou vanaf de tweede week
achterlopen -- en precies de nieuwe naam missen.

**Wat dit NIET is.** Geen censuur op de inhoud. De bevinding mag gewoon
beschreven worden; alleen de verwijzingen naar ons eigen gereedschap horen er
niet in. "De validatie faalt bij meer dan 500 rijen" is prima. "Zie IH-4"
niet.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

#: Namen die altijd intern zijn, ongeacht wat er in de database staat. Dit is
#: de bodem onder de controle: ook op een lege installatie hoort "Nectar" niet
#: in een tekst voor een klant.
VASTE_NAMEN = (
    "LabX", "Nectar", "HiveMind", "Hive Mind", "ND3X", "Beeminds",
    "beeminds-kv", "labx-", "hivemind",
)

#: Hoe een interne ticketsleutel eruitziet: letters, streepje, nummer. Het
#: voorvoegsel komt uit de borden zelf, want dat verzin je bij het aanmaken.
_SLEUTEL = r"\b{prefix}-\d+\b"

#: Dingen die altijd van ons zijn, ongeacht de naam: een adres in het eigen
#: netwerk, een docker-containernaam, een pad op de server.
_ALTIJD = [
    ("intern adres", re.compile(
        r"\b(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?::\d+)?\b", re.I)),
    ("interne host", re.compile(r"\b[a-z0-9-]+\.(?:local|internal|lan)\b", re.I)),
    ("serverpad", re.compile(r"(?:^|\s)/(?:workspace|data|Server)/\S+")),
    ("branchnaam", re.compile(r"\b(?:feature|labx|bugfix|hotfix)/[A-Za-z0-9._/-]+")),
]


def _namen_uit_de_database(db: Session) -> List[tuple]:
    """(soort, naam) voor alles wat in deze installatie draait.

    Alleen namen van drie tekens of langer: een cluster dat "AI" heet zou
    anders elke tekst afkeuren waarin dat woord voorkomt, en dan wordt de
    controle weggeklikt in plaats van gelezen.
    """
    uit: List[tuple] = []
    try:
        from models.board import Board
        from models.cluster import Cluster, ClusterDienst
        from models.lab import Lab

        for (prefix,) in db.query(Board.key_prefix).distinct().all():
            if prefix and len(prefix) >= 2:
                uit.append(("ticketsleutel", str(prefix)))
        for (naam,) in db.query(Cluster.naam).all():
            if naam and len(naam) >= 3:
                uit.append(("clusternaam", str(naam)))
        for (naam,) in db.query(ClusterDienst.naam).all():
            if naam and len(naam) >= 3:
                uit.append(("dienstnaam", str(naam)))
        for naam, branch in db.query(Lab.name, Lab.branch).all():
            if naam and len(naam) >= 3:
                uit.append(("labnaam", str(naam)))
            if branch and len(branch) >= 3:
                uit.append(("branchnaam", str(branch)))
        # Tickettitels bewust NIET: die beschrijven juist het werk en mogen
        # vaak gewoon naar buiten. Alleen de sleutels zijn intern.
    except Exception:  # noqa: BLE001 — een controle mag nooit de reden zijn
        pass                                    # dat er niets meer verstuurd kan worden
    return uit


def controleer(tekst: str, db: Optional[Session] = None) -> Dict[str, Any]:
    """Wat er in deze tekst staat dat niet naar een klant hoort.

    Geeft `{"schoon": bool, "bevindingen": [{soort, gevonden, waar}]}`. Geen
    oordeel over de inhoud -- alleen over de verwijzingen.
    """
    tekst = tekst or ""
    bevindingen: List[Dict[str, Any]] = []

    def meld(soort: str, gevonden: str, pos: int) -> None:
        if any(b["gevonden"].lower() == gevonden.lower() and b["soort"] == soort
               for b in bevindingen):
            return
        begin = max(0, pos - 40)
        bevindingen.append({
            "soort": soort,
            "gevonden": gevonden,
            "waar": tekst[begin:pos + len(gevonden) + 40].strip(),
        })

    for naam in VASTE_NAMEN:
        for m in re.finditer(re.escape(naam), tekst, re.I):
            meld("interne naam", m.group(0), m.start())

    for soort, patroon in _ALTIJD:
        for m in patroon.finditer(tekst):
            meld(soort, m.group(0).strip(), m.start())

    if db is not None:
        for soort, naam in _namen_uit_de_database(db):
            if soort == "ticketsleutel":
                patroon = re.compile(_SLEUTEL.format(prefix=re.escape(naam)), re.I)
            else:
                patroon = re.compile(r"\b" + re.escape(naam) + r"\b", re.I)
            for m in patroon.finditer(tekst):
                meld(soort, m.group(0), m.start())

    return {"schoon": not bevindingen, "bevindingen": bevindingen}


def uitleg(uitslag: Dict[str, Any]) -> str:
    """De bevindingen als één zin per stuk, voor in een antwoord."""
    if uitslag.get("schoon"):
        return ""
    regels = [f"- {b['soort']}: {b['gevonden']}" for b in uitslag.get("bevindingen", [])]
    return ("Deze tekst noemt dingen die een klant niet hoort te zien:\n"
            + "\n".join(regels[:10]))
