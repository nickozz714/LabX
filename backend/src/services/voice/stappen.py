"""Een workflow met álle activiteitsoorten, opgebouwd in spreektaal.

De tekenaar kent zes soorten: een agent, een shell-commando, een als met twee
takken, een wacht, een bubbel waarin dingen tegelijk gebeuren, en een lus over
een lijst. Een vlakke opsomming van zinnen kan dat niet uitdrukken -- en toen
iemand zei "als de logging is uitgelezen hoeft er niets te gebeuren, anders
moet hij het hello-script aanmaken" werd dat één tekststap in plaats van een
vertakking.

Hoe dit werkt:

- De stappen zijn een BOOM, geen lijst. Een `als` heeft een ja- en een
  nee-tak; een bubbel en een lus hebben kinderen. Een pad (`pad`) wijst aan
  waar we in die boom bezig zijn.
- De SOORT wordt geraden uit wat je zegt ("als ...", "wacht ...", "voor elke
  ..."), en daarna vraagt de server alleen nog wat er voor díé soort mist. Je
  hoeft dus niet te weten dat een lus "voorelk" heet.
- Raden mag hier, want elke gok wordt zichtbaar: de bevestigingszin aan het
  eind leest de hele boom voor, inclusief de soorten.

De omzetting naar nodes en edges staat onderaan. Die moet kloppen met wat
services/workflows/graph.py verwacht, anders krijg je een workflow die niet
opent -- vandaar dat we zijn eigen normalisatie eroverheen laten lopen.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

SOORTEN = ("agent", "shell", "als", "wacht", "parallel", "voorelk")

NAAM_VAN_SOORT = {
    "agent": "een agent-stap",
    "shell": "een commando",
    "als": "een keuze",
    "wacht": "een wachtmoment",
    "parallel": "werk dat tegelijk gebeurt",
    "voorelk": "een lus over een lijst",
}


# ── Raden wat voor soort stap je bedoelt ─────────────────────────────────────

def raad_soort(tekst: str) -> str:
    t = " " + (tekst or "").lower().strip() + " "
    if re.search(r"\b(voor elke|voor iedere|voor elk|per stuk|voor alle)\b", t):
        return "voorelk"
    if re.search(r"\b(tegelijk|tegelijkertijd|parallel|naast elkaar)\b", t):
        return "parallel"
    if re.search(r"\b(als|indien|wanneer|mocht|controleer of|kijk of|bepaal of)\b", t):
        return "als"
    if re.search(r"\bwacht\b", t):
        return "wacht"
    if re.search(r"\b(commando|shell|terminal|voer uit|draai het script|bash)\b", t):
        return "shell"
    return "agent"


_SECONDEN = {"seconde": 1, "seconden": 1, "minuut": 60, "minuten": 60,
             "uur": 3600, "uren": 3600}


def raad_seconden(tekst: str) -> Optional[int]:
    t = (tekst or "").lower()
    m = re.search(r"(\d+)\s*(seconden?|minuten?|uur|uren)", t)
    if m:
        return min(3600, max(1, int(m.group(1)) * _SECONDEN[m.group(2)]))
    m = re.search(r"\b(een|één|twee|drie|vier|vijf|tien|vijftien|dertig)\s+"
                  r"(seconden?|minuten?|uur|uren)", t)
    if m:
        getallen = {"een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4,
                    "vijf": 5, "tien": 10, "vijftien": 15, "dertig": 30}
        return min(3600, getallen[m.group(1)] * _SECONDEN[m.group(2)])
    return None


# Waarmee je een tak leeg laat. Geldt voor BEIDE takken: ook "als het goed is
# hoeft er niets te gebeuren" is een volstrekt normale uitspraak.
LEEG_WOORDEN = {"niets", "niks", "geen", "nee", "niets doen", "niks doen",
                "dan niets", "dan niks", "overslaan", "sla over"}


# Hoe je een vergelijking uitspreekt. De volgorde telt: "niet gelijk aan" moet
# vóór "gelijk aan" komen, anders wint de verkeerde.
_OPERATOREN: List[Tuple[str, str]] = [
    (r"niet gelijk (?:is )?aan|ongelijk aan|niet is", "!="),
    (r"gelijk (?:is )?aan|hetzelfde als|gelijk is|is gelijk", "=="),
    (r"groter(?: is)? dan of gelijk", ">="),
    (r"kleiner(?: is)? dan of gelijk", "<="),
    (r"groter(?: is)? dan|meer dan", ">"),
    (r"kleiner(?: is)? dan|minder dan", "<"),
    (r"niet bevat|niet voorkomt", "bevat_niet"),
    (r"bevat|voorkomt", "bevat"),
    (r"niet leeg is|gevuld is|bestaat", "is_niet_leeg"),
    (r"leeg is|niets bevat|ontbreekt", "is_leeg"),
]


def lees_voorwaarde(tekst: str) -> Dict[str, Any]:
    """"gelijk is aan ok" → {"operator": "==", "rechts": "ok"}.

    Zonder herkenbare vergelijking vallen we terug op "is niet leeg": dat is
    de minst verrassende betekenis van "kijk of er iets uitkwam", en hij staat
    straks gewoon in de bevestigingszin zodat je hem kunt tegenhouden.
    """
    t = (tekst or "").strip()
    plat = t.lower()
    for patroon, operator in _OPERATOREN:
        m = re.search(patroon, plat)
        if not m:
            continue
        if operator in ("is_leeg", "is_niet_leeg"):
            return {"operator": operator, "rechts": None}
        rechts = t[m.end():].strip(" .,:;\"'")
        return {"operator": operator, "rechts": rechts or None}
    return {"operator": "is_niet_leeg", "rechts": None}


def voorwaarde_in_woorden(conditie: Dict[str, Any]) -> str:
    uitleg = {"==": "gelijk is aan", "!=": "niet gelijk is aan",
              ">": "groter is dan", ">=": "minstens is",
              "<": "kleiner is dan", "<=": "hoogstens is",
              "bevat": "bevat", "bevat_niet": "niet bevat",
              "is_leeg": "leeg is", "is_niet_leeg": "gevuld is"}
    links = conditie.get("links") or "iets"
    op = uitleg.get(conditie.get("operator") or "==", "gelijk is aan")
    rechts = conditie.get("rechts")
    return f"{links} {op}{(' ' + str(rechts)) if rechts not in (None, '') else ''}"


# ── De boom ──────────────────────────────────────────────────────────────────

def huidige_lijst(velden: Dict[str, Any]) -> List[Dict[str, Any]]:
    stappen = velden.setdefault("stappen", [])
    pad = velden.get("pad") or []
    lijst = stappen
    for deel in pad:
        knoop = lijst[deel[0]] if isinstance(deel, (list, tuple)) else None
        if knoop is None:
            break
        lijst = knoop.setdefault(deel[1], [])
    return lijst


def _laatste(velden: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lijst = huidige_lijst(velden)
    return lijst[-1] if lijst else None


def vraag(velden: Dict[str, Any]) -> str:
    """Welke vraag staat er nu open?"""
    open_vraag = velden.get("open")
    laatste = _laatste(velden)

    if open_vraag == "conditie_links":
        return "Waar moet ik naar kijken? Bijvoorbeeld de uitvoer van een eerdere stap."
    if open_vraag == "conditie_rechts":
        return "En wat moet daarmee zo zijn? Bijvoorbeeld 'gelijk aan ok', 'bevat fout' of 'niet leeg'."
    if open_vraag == "ja":
        return "Wat gebeurt er als dat klopt?"
    if open_vraag == "nee":
        return "En als dat niet zo is? Zeg 'niets' als er dan niets hoeft te gebeuren."
    if open_vraag == "commando":
        return "Welk commando moet er draaien?"
    if open_vraag == "seconden":
        return "Hoe lang moet hij wachten?"
    if open_vraag == "bron":
        return "Waar komt die lijst vandaan? Bijvoorbeeld de uitvoer van een eerdere stap."
    if open_vraag == "kinderen":
        woord = "lus" if (laatste or {}).get("type") == "voorelk" else "bubbel"
        binnen = len((laatste or {}).get("_hier") or [])
        if binnen:
            return f"Nog iets in die {woord}? Zeg 'klaar' als je er bent."
        return f"Wat moet er in die {woord} gebeuren?"

    pad = velden.get("pad") or []
    if pad:
        return "En daarna? Zeg 'klaar' als die tak af is."
    if huidige_lijst(velden):
        return "En de volgende stap? Zeg 'klaar' als je er bent."
    return "Wat is de eerste stap? Zeg wat er moet gebeuren."


def _naam_uit(tekst: str) -> str:
    woorden = (tekst or "").split()
    return (" ".join(woorden[:6])[:60] or "Stap").rstrip(".,;:")


def verwerk(velden: Dict[str, Any], tekst: str, klaar_woorden) -> Dict[str, Any]:
    """Eén antwoord verwerken. Geeft terug wat er daarna gevraagd wordt."""
    t = (tekst or "").strip()
    plat = t.lower().strip(" .!?")
    open_vraag = velden.get("open")

    # -- een deelvraag die openstaat --
    if open_vraag == "conditie_links":
        knoop = _laatste(velden)
        knoop.setdefault("conditie", {})["links"] = t
        velden["open"] = "conditie_rechts"
        return velden
    if open_vraag == "conditie_rechts":
        knoop = _laatste(velden)
        knoop["conditie"].update(lees_voorwaarde(t))
        velden["open"] = "ja"
        return velden
    if open_vraag == "commando":
        _laatste(velden)["commando"] = t
        velden["open"] = None
        return velden
    if open_vraag == "seconden":
        knoop = _laatste(velden)
        knoop["seconden"] = raad_seconden(t) or 30
        velden["open"] = None
        return velden
    if open_vraag == "bron":
        _laatste(velden)["bron"] = t
        velden["open"] = "kinderen"
        return velden

    # -- een tak of een bubbel binnengaan --
    if open_vraag in ("ja", "nee", "kinderen"):
        leeg = plat in klaar_woorden or plat in LEEG_WOORDEN
        if leeg:
            # Lege ja-tak: dan nog wel de nee-tak vragen.
            velden["open"] = "nee" if open_vraag == "ja" else None
            return velden
        sleutel = "_hier" if open_vraag == "kinderen" else open_vraag
        index = len(huidige_lijst(velden)) - 1
        velden.setdefault("pad", []).append([index, sleutel])
        velden["open"] = None
        _voeg_toe(velden, t)
        return velden

    # -- een nieuwe stap op dit niveau, of dit niveau afsluiten --
    if plat in klaar_woorden:
        pad = velden.get("pad") or []
        if pad:
            # Uit de tak omhoog. Kwamen we uit de ja-tak, dan is de nee-tak
            # het logische vervolg -- anders zou je die moeten raden.
            _index, sleutel = pad.pop()
            velden["open"] = "nee" if sleutel == "ja" else None
        else:
            velden["stappen_klaar"] = True
        return velden

    _voeg_toe(velden, t)
    return velden


def _voeg_toe(velden: Dict[str, Any], tekst: str) -> None:
    soort = raad_soort(tekst)
    knoop: Dict[str, Any] = {"type": soort, "naam": _naam_uit(tekst),
                             "tekst": tekst}
    huidige_lijst(velden).append(knoop)

    if soort == "agent":
        knoop["prompt"] = tekst
        velden["open"] = None
    elif soort == "shell":
        velden["open"] = "commando"
    elif soort == "als":
        velden["open"] = "conditie_links"
    elif soort == "wacht":
        seconden = raad_seconden(tekst)
        if seconden:
            knoop["seconden"] = seconden
            velden["open"] = None
        else:
            velden["open"] = "seconden"
    elif soort == "voorelk":
        velden["open"] = "bron"
    elif soort == "parallel":
        velden["open"] = "kinderen"


def compleet(velden: Dict[str, Any]) -> bool:
    return bool(velden.get("stappen_klaar") and velden.get("stappen"))


# ── Voorlezen ────────────────────────────────────────────────────────────────

def in_woorden(stappen: List[Dict[str, Any]], diepte: int = 0) -> List[str]:
    """De boom als voorleesbare regels, voor de bevestigingszin."""
    regels: List[str] = []
    for i, k in enumerate(stappen, start=1):
        inspring = "— " * diepte
        soort = k.get("type", "agent")
        if soort == "als":
            regels.append(f"{inspring}{i}. als {voorwaarde_in_woorden(k.get('conditie') or {})}")
            if k.get("ja"):
                regels.append(f"{inspring}— dan:")
                regels += in_woorden(k["ja"], diepte + 1)
            if k.get("nee"):
                regels.append(f"{inspring}— anders:")
                regels += in_woorden(k["nee"], diepte + 1)
        elif soort == "wacht":
            regels.append(f"{inspring}{i}. wacht {k.get('seconden', 30)} seconden")
        elif soort == "shell":
            regels.append(f"{inspring}{i}. commando: {k.get('commando', '')}")
        elif soort in ("parallel", "voorelk"):
            wat = ("tegelijk" if soort == "parallel"
                   else f"voor elk item uit {k.get('bron', 'een lijst')}")
            regels.append(f"{inspring}{i}. {wat}:")
            regels += in_woorden(k.get("_hier") or [], diepte + 1)
        else:
            regels.append(f"{inspring}{i}. {k.get('prompt') or k.get('tekst', '')}")
    return regels


# ── Naar nodes en edges ──────────────────────────────────────────────────────

class _Bouwer:
    """Zet de boom om in de platte nodes/edges die de tekenaar verwacht."""

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return f"n{self._n}"

    def bouw(self, stappen: List[Dict[str, Any]], groep: Optional[str] = None
             ) -> Tuple[Optional[str], List[str]]:
        """Bouwt een reeks. Geeft (eerste id, open eindes) terug.

        De open eindes zijn de knopen waar nog niets achter hangt; de aanroeper
        verbindt die met wat erna komt. Bij een `als` zijn dat de uiteinden van
        beide takken -- dat is precies waarom dit een boom moet zijn.
        """
        eerste: Optional[str] = None
        eindes: List[Tuple[str, str]] = []     # (node-id, soort verbinding)

        for knoop in stappen:
            nid = self._id()
            soort = knoop.get("type", "agent")
            node: Dict[str, Any] = {"id": nid, "type": soort,
                                    "naam": knoop.get("naam") or "Stap"}
            if groep:
                node["groep"] = groep
            if soort == "agent":
                node["prompt"] = knoop.get("prompt") or knoop.get("tekst") or ""
            elif soort == "shell":
                node["commando"] = knoop.get("commando") or ""
            elif soort == "als":
                node["conditie"] = knoop.get("conditie") or {}
            elif soort == "wacht":
                node["seconden"] = knoop.get("seconden") or 30
            elif soort == "voorelk":
                node["bron"] = knoop.get("bron") or ""
            self.nodes.append(node)

            if eerste is None:
                eerste = nid
            for van, verbinding in eindes:
                self.edges.append({"van": van, "naar": nid, "soort": verbinding})
            eindes = []

            if soort == "als":
                for tak, verbinding in (("ja", "ja"), ("nee", "nee")):
                    kinderen = knoop.get(tak) or []
                    if not kinderen:
                        # Geen tak: dit uiteinde loopt met deze verbinding door
                        # naar wat er ná de keuze komt.
                        eindes.append((nid, verbinding))
                        continue
                    kop, staart = self.bouw(kinderen, groep=groep)
                    self.edges.append({"van": nid, "naar": kop, "soort": verbinding})
                    eindes += [(e, "succes") for e in staart]
            elif soort in ("parallel", "voorelk"):
                # De kinderen hangen aan de groep; de motor start ze zelf.
                self.bouw(knoop.get("_hier") or [], groep=nid)
                eindes = [(nid, "succes")]
            else:
                eindes = [(nid, "succes")]

        return eerste, [e for e, _ in eindes]


def naar_graaf(stappen: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]],
                                                       List[Dict[str, Any]]]:
    bouwer = _Bouwer()
    bouwer.bouw(stappen)
    return bouwer.nodes, bouwer.edges
