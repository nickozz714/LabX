"""
services/workflows/expressies.py

Verwijzen naar wat er eerder in de run gebeurde, en daar een voorwaarde over
stellen.

**Waarom geen echte expressietaal.** Een vrij in te tikken expressie die
geëvalueerd wordt, is een manier om code uit te voeren die niemand heeft
gereviewd — en in een GUI is het ook nog eens het moeilijkste veld om in te
vullen. Een voorwaarde is hier daarom een OBJECT: links een verwijzing, een
operator, en rechts een waarde of nog een verwijzing. Dat is precies wat een
schermpje met drie velden kan tonen, en er valt niets mee uit te voeren.

**Verwijzingen** (gebruik ze ook in de tekst van een activiteit, als
`{{ ... }}`):

    stap.<sleutel>.uitvoer        de tekst die die activiteit opleverde
    stap.<sleutel>.json.<pad>     een veld uit gestructureerde uitvoer
    stap.<sleutel>.exit_code      de afloop van een shell-activiteit
    stap.<sleutel>.status         "ok" of "fout"
    invoer.<veld>                 waarmee de run gestart is
    item                          het element van de huidige herhaling
    iteratie                      de hoeveelste ronde (1-gebaseerd)
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

OPERATOREN = ("==", "!=", ">", ">=", "<", "<=", "bevat", "bevat_niet",
              "is_leeg", "is_niet_leeg")

_VERWIJZING = re.compile(r"\{\{\s*([a-zA-Z0-9_.\[\]]+)\s*\}\}")


def _pad(waarde: Any, pad: List[str]) -> Any:
    for deel in pad:
        if waarde is None:
            return None
        if isinstance(waarde, dict):
            waarde = waarde.get(deel)
        elif isinstance(waarde, list) and deel.isdigit():
            index = int(deel)
            waarde = waarde[index] if 0 <= index < len(waarde) else None
        else:
            return None
    return waarde


def los_op(verwijzing: str, context: Dict[str, Any]) -> Any:
    """Eén verwijzing omzetten naar zijn waarde, of None als hij niet bestaat.

    Niet bestaan is géén fout: een activiteit die nog niet gedraaid heeft (de
    andere tak van een `als`) hoort een lege waarde te geven en niet de hele
    run om te gooien."""
    delen = [d for d in str(verwijzing or "").split(".") if d]
    if not delen:
        return None
    kop, rest = delen[0], delen[1:]
    if kop == "item":
        return _pad(context.get("item"), rest)
    if kop == "iteratie":
        return context.get("iteratie")
    if kop == "invoer":
        return _pad(context.get("invoer") or {}, rest)
    if kop == "stap":
        if not rest:
            return None
        resultaat = (context.get("stap") or {}).get(rest[0])
        if resultaat is None:
            return None
        if len(rest) == 1:
            return resultaat.get("uitvoer")
        veld, verder = rest[1], rest[2:]
        if veld == "json":
            return _pad(resultaat.get("json"), verder)
        return _pad(resultaat.get(veld), verder)
    return None


def vul_in(tekst: str, context: Dict[str, Any]) -> str:
    """`{{ stap.controle.json.aantal }}` in een opdracht vervangen door de waarde.

    Een verwijzing die niets oplevert wordt een lege string en blijft niet als
    `{{ ... }}` staan: een model dat zo'n stukje krijgt, gaat erover fantaseren."""
    def _vervang(m: re.Match) -> str:
        waarde = los_op(m.group(1), context)
        if waarde is None:
            return ""
        if isinstance(waarde, (dict, list)):
            return json.dumps(waarde, ensure_ascii=False)
        return str(waarde)
    return _VERWIJZING.sub(_vervang, tekst or "")


def _getal(waarde: Any) -> Optional[float]:
    try:
        return float(str(waarde).strip())
    except (TypeError, ValueError):
        return None


def evalueer(conditie: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Waar of niet waar. Bij twijfel: niet waar.

    Een voorwaarde die nergens op slaat (verwijzing naar een activiteit die
    niet bestaat, een getalvergelijking op tekst) levert `False` — de `nee`-tak
    dus. Dat is de veiligere kant: doorgaan alsof alles goed is, is precies wat
    je niet wilt als je niet weet wat er staat."""
    links_ref = str(conditie.get("links") or "")
    operator = str(conditie.get("operator") or "==")
    links = los_op(links_ref, context)

    if operator == "is_leeg":
        return links in (None, "", [], {})
    if operator == "is_niet_leeg":
        return links not in (None, "", [], {})

    rechts_ruw = conditie.get("rechts")
    # Rechts mag óók een verwijzing zijn: "is het aantal gelijk aan wat stap 1
    # zei". Dat gebeurt als je het expliciet als verwijzing markeert; anders is
    # het gewoon de waarde die er staat.
    rechts = (los_op(str(rechts_ruw), context) if conditie.get("rechts_is_verwijzing")
              else rechts_ruw)

    if operator in ("bevat", "bevat_niet"):
        haystack = links if isinstance(links, (list, dict)) else str(links or "")
        naald = str(rechts or "")
        if isinstance(haystack, list):
            treffer = any(str(x) == naald for x in haystack)
        elif isinstance(haystack, dict):
            treffer = naald in haystack
        else:
            treffer = naald.lower() in haystack.lower()
        return treffer if operator == "bevat" else not treffer

    if operator in (">", ">=", "<", "<="):
        a, b = _getal(links), _getal(rechts)
        if a is None or b is None:
            return False
        return {">"  : a > b, ">=" : a >= b, "<"  : a < b, "<=" : a <= b}[operator]

    gelijk = _gelijk(links, rechts)
    return gelijk if operator == "==" else not gelijk


def _gelijk(a: Any, b: Any) -> bool:
    """Gelijkheid zoals een mens het bedoelt: 3 en "3" zijn hetzelfde, en
    true/waar/ja ook."""
    if a is None and b is None:
        return True
    ga, gb = _getal(a), _getal(b)
    if ga is not None and gb is not None:
        return ga == gb
    sa, sb = str(a).strip().lower(), str(b).strip().lower()
    waar = {"true", "waar", "ja", "1"}
    onwaar = {"false", "onwaar", "nee", "0"}
    if sa in waar and sb in waar:
        return True
    if sa in onwaar and sb in onwaar:
        return True
    return sa == sb


def beschrijf(conditie: Dict[str, Any]) -> str:
    """De voorwaarde als leesbare regel, voor in het runverslag."""
    links = conditie.get("links") or "?"
    operator = conditie.get("operator") or "=="
    if operator in ("is_leeg", "is_niet_leeg"):
        return f"{links} {operator.replace('_', ' ')}"
    return f"{links} {operator} {conditie.get('rechts')!r}"
