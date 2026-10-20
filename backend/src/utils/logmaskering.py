"""utils/logmaskering.py — geheimen uit een logbestand halen voordat je het leest.

Dit bestaat door een gemeten geval. Het startlog van een branch-lab bevatte de
Key Vault-responses van de applicatie op DEBUG-niveau, compleet met het
`value`-veld: de sleutel van een database en een API-sleutel, leesbaar, in een
tekst die LabX zó op je scherm zou zetten en aan een model zou geven.

De kluismaskering van LabX helpt daar niet: die vervangt waarden die LabX
kent, en deze kwamen op dat moment uit een vault die LabX niet beheert. Wat
overblijft is het herkennen van de VORM. Dat is per definitie onnauwkeurig, en
die onnauwkeurigheid gaat hier bewust één kant op: liever een versienummer
onleesbaar dan een sleutel leesbaar. Wie de echte waarde nodig heeft, kan in
het lab kijken -- dat is een bewuste handeling en dit is een logvenster.
"""
from __future__ import annotations

import re
from typing import List, Pattern, Tuple

VERVANGING = "[geheim gemaskeerd]"

#: Woorden die aankondigen dat wat erna komt niet op je scherm hoort. In een
#: JSON-veld, een omgevingsvariabele of een querystring.
_VERDACHT = (
    r"value|secret|password|passwd|pwd|token|api[_-]?key|apikey|"
    r"access[_-]?key|account[_-]?key|primary[_-]?key|secondary[_-]?key|"
    r"client[_-]?secret|connection[_-]?string|sas|signature|credential"
)

_PATRONEN: List[Tuple[Pattern, int]] = [
    # "value":"…"  en  "clientSecret": "…"   (JSON, de Key Vault-vorm)
    (re.compile(rf'("(?:{_VERDACHT})"\s*:\s*")([^"]{{6,}})(")', re.I), 2),
    # VALUE='…' of API_KEY=…   (omgeving, .env, querystring)
    (re.compile(rf'\b((?:{_VERDACHT})\s*=\s*)([^\s&;"\']{{6,}})', re.I), 2),
    # Authorization: Bearer …
    (re.compile(r'\b(Bearer\s+)([A-Za-z0-9._~+/-]{20,}=*)', re.I), 2),
    # Een losse sleutel-achtige brok: lang, base64 of hex, zonder spaties.
    # Alleen vanaf 40 tekens, want korter is te vaak een hash of een pad.
    (re.compile(r'(^|[\s"\'=:>(])([A-Za-z0-9+/]{40,}={0,2})($|[\s"\'<),;])'), 2),
]


def maskeer_log(tekst: str) -> str:
    """Alles wat eruitziet als een geheim onleesbaar maken.

    Laat de omliggende tekst staan: je wilt nog steeds kunnen zien WELKE
    sleutel er werd opgehaald en of dat lukte. Dat is de hele reden dat je
    het log leest; de waarde zelf heb je daar niet voor nodig.
    """
    uit = tekst or ""
    if not uit:
        return uit
    for patroon, groep in _PATRONEN:
        def _vervang(m: re.Match) -> str:
            stukken = list(m.groups())
            stukken[groep - 1] = VERVANGING
            return "".join(s or "" for s in stukken)

        uit = patroon.sub(_vervang, uit)
    return uit
