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


def _ontdaan_van_aanhalingstekens(waarde: Any) -> tuple[Any, bool]:
    """`'simpel'` in een invoerveld betekent bijna altijd simpel.

    Niemand tikt in een schermpje aanhalingstekens om een waarde omdat hij ze
    erbij wil hebben; hij tikt ze omdat het veld op code lijkt. Vergelijken op
    de letterlijke tekst levert dan een `nee` waar niets aan te zien is."""
    if not isinstance(waarde, str):
        return waarde, False
    tekst = waarde.strip()
    if len(tekst) >= 2 and tekst[0] == tekst[-1] and tekst[0] in ("'", '"'):
        return tekst[1:-1], True
    return waarde, False


def beschikbaar_naast(verwijzing: str, context: Dict[str, Any]) -> List[str]:
    """Welke velden er wél zijn, één niveau boven een verwijzing die niets
    opleverde.

    Een voorwaarde op `item.complexiteit` terwijl het veld `complexity` heet,
    is anders een stille `nee`: geen fout, geen melding, alleen de verkeerde
    tak. Hier staat dan naast de uitkomst wat er wél in zat."""
    delen = [d for d in str(verwijzing or "").split(".") if d]
    if len(delen) < 2:
        return []
    ouder = los_op(".".join(delen[:-1]), context)
    if isinstance(ouder, dict):
        return sorted(str(k) for k in ouder.keys())[:25]
    return []


def evalueer_uitgelegd(conditie: Dict[str, Any],
                       context: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    """Dezelfde uitkomst als `evalueer`, plus waar hij vandaan komt.

    Een `als` die alleen "nee" opschrijft, is achteraf niet na te gaan: je
    ziet de voorwaarde en de tak, maar niet de waarde waarop hij besloot. Dat
    is precies het moment waarop je wil weten of het veld leeg was, anders
    heette, of gewoon iets anders bevatte dan je dacht. Alles wat hier wordt
    teruggegeven, komt in het runverslag van de activiteit terecht."""
    links_ref = str(conditie.get("links") or "")
    operator = str(conditie.get("operator") or "==")
    links = los_op(links_ref, context)

    uitleg: Dict[str, Any] = {
        "links": links_ref,
        "links_waarde": links if isinstance(links, (str, int, float, bool, type(None)))
                        else json.loads(json.dumps(links, ensure_ascii=False, default=str)),
        "operator": operator,
    }
    if links is None and links_ref:
        velden = beschikbaar_naast(links_ref, context)
        uitleg["links_bestaat"] = False
        if velden:
            uitleg["beschikbare_velden"] = velden

    if operator in ("is_leeg", "is_niet_leeg"):
        leeg = links in (None, "", [], {})
        uitkomst = leeg if operator == "is_leeg" else not leeg
        uitleg["uitkomst"] = uitkomst
        return uitkomst, uitleg

    rechts_ruw = conditie.get("rechts")
    # Rechts mag óók een verwijzing zijn: "is het aantal gelijk aan wat stap 1
    # zei". Dat gebeurt als je het expliciet als verwijzing markeert; anders is
    # het gewoon de waarde die er staat.
    if conditie.get("rechts_is_verwijzing"):
        rechts = los_op(str(rechts_ruw), context)
        uitleg["rechts_is_verwijzing"] = True
        uitleg["rechts_ref"] = rechts_ruw
    else:
        rechts, ontdaan = _ontdaan_van_aanhalingstekens(rechts_ruw)
        if ontdaan:
            uitleg["aanhalingstekens_genegeerd"] = True
            uitleg["rechts_ruw"] = rechts_ruw
    uitleg["rechts_waarde"] = (rechts if isinstance(rechts, (str, int, float, bool, type(None)))
                               else json.loads(json.dumps(rechts, ensure_ascii=False, default=str)))

    if operator in ("bevat", "bevat_niet"):
        haystack = links if isinstance(links, (list, dict)) else str(links or "")
        naald = str(rechts or "")
        if isinstance(haystack, list):
            treffer = any(str(x) == naald for x in haystack)
        elif isinstance(haystack, dict):
            treffer = naald in haystack
        else:
            treffer = naald.lower() in haystack.lower()
        uitkomst = treffer if operator == "bevat" else not treffer
        uitleg["uitkomst"] = uitkomst
        return uitkomst, uitleg

    if operator in (">", ">=", "<", "<="):
        a, b = _getal(links), _getal(rechts)
        if a is None or b is None:
            # Een getalvergelijking op tekst is geen fout maar wel een val:
            # zonder deze regel ziet de lezer straks alleen "nee".
            uitleg["reden"] = "geen getal om mee te rekenen"
            uitleg["uitkomst"] = False
            return False, uitleg
        uitkomst = {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[operator]
        uitleg["uitkomst"] = uitkomst
        return uitkomst, uitleg

    gelijk = _gelijk(links, rechts)
    uitkomst = gelijk if operator == "==" else not gelijk
    uitleg["uitkomst"] = uitkomst
    return uitkomst, uitleg


def evalueer(conditie: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Waar of niet waar. Bij twijfel: niet waar.

    Een voorwaarde die nergens op slaat (verwijzing naar een activiteit die
    niet bestaat, een getalvergelijking op tekst) levert `False` — de `nee`-tak
    dus. Dat is de veiligere kant: doorgaan alsof alles goed is, is precies wat
    je niet wilt als je niet weet wat er staat."""
    return evalueer_uitgelegd(conditie, context)[0]


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


def _waarde_uit(uitleg: Dict[str, Any], sleutel: str) -> str:
    waarde = uitleg.get(sleutel)
    if waarde is None:
        return "leeg"
    if isinstance(waarde, (dict, list)):
        return json.dumps(waarde, ensure_ascii=False)[:400]
    return json.dumps(waarde, ensure_ascii=False)


def beschrijf_uitkomst(uitleg: Dict[str, Any]) -> str:
    """Het besluit van een `als`, uitgeschreven voor in het runverslag.

    Dit is wat er in de invoer van de activiteit terechtkomt, zodat je later
    niet de voorwaarde terugleest maar de vergelijking die hij écht maakte."""
    operator = uitleg.get("operator") or "=="
    regels = [f"{uitleg.get('links') or '?'} {operator} "
              + ("" if operator in ("is_leeg", "is_niet_leeg")
                 else _waarde_uit(uitleg, "rechts_waarde")),
              ""]
    links = f"  links    {uitleg.get('links') or '?'} → {_waarde_uit(uitleg, 'links_waarde')}"
    if uitleg.get("links_bestaat") is False:
        links += "   (die verwijzing bestaat hier niet)"
    regels.append(links)
    if uitleg.get("beschikbare_velden"):
        regels.append("           wel aanwezig: " + ", ".join(uitleg["beschikbare_velden"]))
    if operator not in ("is_leeg", "is_niet_leeg"):
        rechts = f"  rechts   {_waarde_uit(uitleg, 'rechts_waarde')}"
        if uitleg.get("rechts_is_verwijzing"):
            rechts += f"   (uit {uitleg.get('rechts_ref')})"
        elif uitleg.get("aanhalingstekens_genegeerd"):
            rechts += (f"   (je tikte {uitleg.get('rechts_ruw')!r}; "
                       "de aanhalingstekens zijn genegeerd)")
        regels.append(rechts)
    if uitleg.get("reden"):
        regels.append(f"  let op   {uitleg['reden']}")
    regels += ["", f"  uitkomst: {'ja' if uitleg.get('uitkomst') else 'nee'}"]
    return "\n".join(regels)
