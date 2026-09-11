"""
services/lab/classifier.py

De classifier van de data-guard: wát er in een commando of in uitvoer zit.

**Classifier en guard zijn twee dingen.** Deze module VINDT en LABELT — hij
zegt "hier staan drie geldige BSN's" of "dit commando doet een SELECT op
kolommen". Wat daar vervolgens mee gebeurt (blokkeren, maskeren, alleen
noteren) is beleid, en dat staat per regel in de database waar je het zelf kunt
aanpassen. Door die twee te scheiden kun je de gevoeligheid bijstellen zonder
dat iemand code hoeft te wijzigen — en kun je in de audit teruglezen wat er
gevonden is, los van wat eraan gedaan is.

**Twee doelen, met een wezenlijk verschil.**

- `opdracht` — kijkt naar het COMMANDO, vóór uitvoeren. Dit is de enige plek
  waar `SELECT MAX(bedrag)` te stoppen is: dat levert één getal op dat in geen
  enkele uitvoercontrole opvalt en tóch een echte magnitude is.
- `uitvoer` — kijkt naar wat terugkomt. Hier hoort MASKEREN thuis. De hele
  uitvoer weggooien omdat er één BSN in staat, kost je ook de exitcode, het pad
  en de foutmelding die je nodig had — en dat is precies waarom de oude guard
  het werk in de weg zat.

**Ingebouwde detectors hebben een checksum.** Een reeks van negen cijfers is
pas een BSN als de elfproef klopt; een IBAN pas als de modulo-97 klopt. Dat
scheelt het overgrote deel van de valse treffers, en het is het verschil tussen
een guard die je vertrouwt en een die je uitzet.

**Presidio is optioneel.** Staat het pakket met een taalmodel geïnstalleerd,
dan komen er namen, adressen en telefoonnummers bij — de herkenning die je met
patronen niet krijgt. Ontbreekt het, dan werkt alles gewoon door met de
ingebouwde detectors; de statuspagina zegt dan dat die laag uit staat in plaats
van stil te blijven.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.guard_rule import GuardRule

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── ingebouwde detectors ────────────────────────────────────────────────────
#
# Elke detector geeft (start, eind, treffer) terug. De checksum zit in de
# detector zelf: een patroon dat alles met negen cijfers pakt is onbruikbaar.

_NEGEN = re.compile(r"\b\d{9}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_KAART = re.compile(r"\b\d{13,19}\b|\b\d{4}(?:[ -]\d{4}){3}\b")
_TELEFOON_NL = re.compile(r"\b(?:\+31|0)\s?6[\s-]?\d{4}[\s-]?\d{4}\b|\b0\d{2,3}[\s-]?\d{6,7}\b")


def _bsn_geldig(waarde: str) -> bool:
    """De elfproef. Zonder deze toets is elk ordernummer van negen cijfers een
    BSN, en dan blokkeert de guard het halve werk."""
    cijfers = [int(c) for c in waarde]
    totaal = sum(d * w for d, w in zip(cijfers, (9, 8, 7, 6, 5, 4, 3, 2, -1)))
    return totaal % 11 == 0


def _iban_geldig(waarde: str) -> bool:
    kaal = waarde.replace(" ", "").upper()
    if len(kaal) < 15:
        return False
    verplaatst = kaal[4:] + kaal[:4]
    getal = ""
    for teken in verplaatst:
        getal += str(ord(teken) - 55) if teken.isalpha() else teken
    try:
        return int(getal) % 97 == 1
    except ValueError:
        return False


def _luhn_ok(waarde: str) -> bool:
    cijfers = [int(c) for c in waarde if c.isdigit()]
    if len(cijfers) < 13:
        return False
    som, dubbel = 0, False
    for c in reversed(cijfers):
        if dubbel:
            c *= 2
            if c > 9:
                c -= 9
        som += c
        dubbel = not dubbel
    return som % 10 == 0


def _met_check(patroon: re.Pattern, controle: Callable[[str], bool]):
    def _zoek(tekst: str) -> List[Tuple[int, int, str]]:
        return [(m.start(), m.end(), m.group())
                for m in patroon.finditer(tekst) if controle(m.group())]
    return _zoek


def _kaal(patroon: re.Pattern):
    def _zoek(tekst: str) -> List[Tuple[int, int, str]]:
        return [(m.start(), m.end(), m.group()) for m in patroon.finditer(tekst)]
    return _zoek


def _dataset(tekst: str) -> List[Tuple[int, int, str]]:
    """Ziet deze uitvoer eruit als een DATASET in plaats van als een resultaat?

    Dit is de detector die geen patroon heeft. Veertig regels met komma's en
    datums, of veertig JSON-objecten met waardevelden: dat is een uitvoer van
    klantgegevens, ook als er geen enkel BSN of IBAN in staat. De andere
    detectors zoeken naar een IDENTIFICATIE; deze naar een VORM.

    Hij geeft één treffer terug die de hele tekst beslaat: bij zoiets valt er
    niets zinnigs te maskeren — je houdt dan veertig regels met uitsluitend
    merktekens over. Daarom staat de meegeleverde regel op blokkeren.
    """
    from services.lab.data_guard import record_rows

    rijen = record_rows(tekst[:200_000])
    if rijen < DATASET_DREMPEL:
        return []
    return [(0, len(tekst), f"{rijen} records")]


# Vanaf hoeveel recordachtige regels iets een dataset is. Tien: minder is in de
# praktijk een voorbeeld of een foutmelding met wat velden erin, en daar wil je
# niet op blokkeren.
DATASET_DREMPEL = 10


INGEBOUWD: Dict[str, Callable[[str], List[Tuple[int, int, str]]]] = {
    "dataset": _dataset,
    "bsn": _met_check(_NEGEN, _bsn_geldig),
    "iban": _met_check(_IBAN, _iban_geldig),
    "creditcard": _met_check(_KAART, _luhn_ok),
    "email": _kaal(_EMAIL),
    "telefoon_nl": _kaal(_TELEFOON_NL),
}


# ── de meegeleverde regels ──────────────────────────────────────────────────
#
# Dit is de stand waarmee LabX start. Alles is daarna aanpasbaar: een regel
# uitzetten, de actie veranderen, een eigen patroon toevoegen.

STANDAARDREGELS: List[Dict[str, Any]] = [
    # — uitvoer: persoonsgegevens. Maskeren, niet blokkeren: de rest van de
    #   uitvoer (exitcode, pad, foutmelding) heb je gewoon nodig.
    {"key": "bsn", "target": "uitvoer", "name": "Burgerservicenummer",
     "kind": "ingebouwd", "category": "persoonsgegeven", "action": "maskeren",
     "sort_order": 10,
     "description": "Negen cijfers die de elfproef doorstaan. Zonder die proef "
                    "is elk ordernummer van negen cijfers een BSN."},
    {"key": "iban", "target": "uitvoer", "name": "IBAN-rekeningnummer",
     "kind": "ingebouwd", "category": "persoonsgegeven", "action": "maskeren",
     "sort_order": 20, "description": "Gecontroleerd met de modulo-97-toets."},
    {"key": "creditcard", "target": "uitvoer", "name": "Creditcardnummer",
     "kind": "ingebouwd", "category": "persoonsgegeven", "action": "maskeren",
     "sort_order": 30, "description": "Gecontroleerd met de Luhn-formule."},
    {"key": "email", "target": "uitvoer", "name": "E-mailadres",
     "kind": "ingebouwd", "category": "persoonsgegeven", "action": "maskeren",
     "sort_order": 40,
     "description": "Let op: ook adressen van collega's en van systemen. Zet hem "
                    "op 'waarschuwen' als dat in jouw werk te veel ruis geeft."},
    {"key": "telefoon_nl", "target": "uitvoer", "name": "Nederlands telefoonnummer",
     "kind": "ingebouwd", "category": "persoonsgegeven", "action": "maskeren",
     "sort_order": 50, "description": "Mobiel en vast, met of zonder landcode."},
    {"key": "dataset", "target": "uitvoer", "name": "Uitvoer is een dataset",
     "kind": "ingebouwd", "category": "klantgegevens", "action": "blokkeren",
     "sort_order": 60,
     "description": "Tien of meer recordachtige regels (komma's, datums, bedragen) of "
                    "JSON-objecten met waardevelden. Dit is de enige detector zonder "
                    "patroon: hij zoekt naar de VORM van klantgegevens in plaats van "
                    "naar een identificatie. Blokkeren en niet maskeren, want bij "
                    "veertig regels houd je met maskeren alleen merktekens over."},

    # — opdracht: hier hoort blokkeren thuis.
    {"key": "select_kolommen", "target": "opdracht", "name": "SELECT van kolomwaarden",
     "kind": "regex", "category": "klantgegevens", "action": "blokkeren",
     "sort_order": 10,
     "pattern": r"\bSELECT\b(?![^;]*\bLIMIT\s+0\b)(?![^;]*\bCOUNT\s*\()[^;]*\bFROM\b",
     "description": "Haalt echte rijen op. COUNT(...) en LIMIT 0 vallen erbuiten: "
                    "die tellen of beschrijven alleen."},
    {"key": "aggregaties", "target": "opdracht", "name": "Aggregaties over echte waarden",
     "kind": "regex", "category": "klantgegevens", "action": "blokkeren",
     "sort_order": 20,
     "pattern": r"\b(MIN|MAX|SUM|AVG|MEAN|MEDIAN|STDDEV|STDEV|VAR|PERCENTILE\w*)\s*\("
                r"|\.(min|max|mean|median|sum|std|var|quantile)\s*\(",
     "description": "Een magnitude is klantdata, ook zonder rijen eromheen. Dit is "
                    "het enige moment waarop je dat kunt zien: `SELECT MAX(bedrag)` "
                    "levert één getal op dat in geen enkele uitvoercontrole opvalt."},
    {"key": "sample_rijen", "target": "opdracht", "name": "Voorbeeldrijen opvragen",
     "kind": "regex", "category": "klantgegevens", "action": "blokkeren",
     "sort_order": 30,
     "pattern": r"\.head\s*\(|\.tail\s*\(|\.sample\s*\(|\.describe\s*\(|"
                r"\.value_counts\s*\(|\.unique\s*\(|\.groupby\s*\(|\bGROUP\s+BY\b",
     "description": "head/sample/value_counts/GROUP BY tonen echte waarden en "
                    "groepslabels."},
    {"key": "beheer_api", "target": "opdracht", "name": "Beheer-API's (uitzondering)",
     "kind": "regex", "category": "configuratie", "action": "toelaten",
     "sort_order": 5,
     "pattern": r"api\.fabric\.microsoft\.com/v1/|api\.powerbi\.com/v1\.0/|"
                r"management\.azure\.com/|\baz\s+\S+(\s+\S+)?\s+(list|show)\b",
     "description": "Definities en inventaris zijn code en configuratie, geen "
                    "klantgegevens. Deze regel wint van de regels hierboven — zet "
                    "hem uit als je ook beheer-API's wilt tegenhouden."},
]


def _vingerafdruk(spec: Dict[str, Any]) -> str:
    velden = (spec.get("name"), spec.get("description"), spec.get("kind"),
              spec.get("pattern"), spec.get("category"), spec.get("action"),
              spec.get("sort_order"), spec.get("target"))
    return hashlib.sha256(repr(velden).encode("utf-8")).hexdigest()[:16]


def _rij_vingerafdruk(rij: GuardRule) -> str:
    return _vingerafdruk({
        "name": rij.name, "description": rij.description, "kind": rij.kind,
        "pattern": rij.pattern, "category": rij.category, "action": rij.action,
        "sort_order": rij.sort_order, "target": rij.target})


def seed_standaardregels(db: Session) -> int:
    """Meegeleverde regels aanmaken of bijwerken.

    Een regel die de gebruiker zelf heeft aangepast blijft staan — dat is te
    zien doordat zijn vingerafdruk niet meer overeenkomt met wat er bij de
    vorige versie is neergezet. Zelfde afspraak als bij lab-extra's: een
    update mag jouw keuzes niet stilletjes terugdraaien."""
    nieuw = 0
    now = _now_iso()
    for spec in STANDAARDREGELS:
        rij = db.query(GuardRule).filter(GuardRule.key == spec["key"]).first()
        afdruk = _vingerafdruk(spec)
        if rij is None:
            db.add(GuardRule(key=spec["key"], target=spec["target"], name=spec["name"],
                             description=spec.get("description"), kind=spec["kind"],
                             pattern=spec.get("pattern"), category=spec["category"],
                             action=spec["action"], sort_order=spec.get("sort_order", 100),
                             enabled=True, builtin_hash=afdruk,
                             created_at=now, updated_at=now))
            nieuw += 1
            continue
        if rij.builtin_hash and rij.builtin_hash == _rij_vingerafdruk(rij) and \
                rij.builtin_hash != afdruk:
            # Ongewijzigd door de gebruiker: mag bijgewerkt worden.
            rij.name, rij.description = spec["name"], spec.get("description")
            rij.kind, rij.pattern = spec["kind"], spec.get("pattern")
            rij.category, rij.action = spec["category"], spec["action"]
            rij.sort_order = spec.get("sort_order", 100)
            rij.target, rij.builtin_hash = spec["target"], afdruk
            rij.updated_at = now
    db.commit()
    return nieuw


# ── Presidio (optioneel) ────────────────────────────────────────────────────

_PRESIDIO: Optional[Any] = None
_PRESIDIO_GEPROBEERD = False
_PRESIDIO_REDEN: Optional[str] = None


def presidio_status() -> Dict[str, Any]:
    _laad_presidio()
    return {"beschikbaar": _PRESIDIO is not None, "reden": _PRESIDIO_REDEN}


def _laad_presidio() -> Optional[Any]:
    """Eén keer proberen, daarna onthouden. Ontbreekt het, dan werkt alles door
    met de ingebouwde detectors — maar de statuspagina zegt het, in plaats van
    dat deze laag stil afwezig is."""
    global _PRESIDIO, _PRESIDIO_GEPROBEERD, _PRESIDIO_REDEN
    if _PRESIDIO_GEPROBEERD:
        return _PRESIDIO
    _PRESIDIO_GEPROBEERD = True
    try:
        import logging

        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        # Presidio praat veel bij het opbouwen van zijn register: voor elke
        # herkenner die niet in het Nederlands bestaat één waarschuwing. Dat is
        # informatie over Presidio's eigen inrichting, niet over onze guard, en
        # het maakt het log onleesbaar op het moment dat je er juist iets in
        # zoekt.
        logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
    except Exception as exc:  # noqa: BLE001
        _PRESIDIO_REDEN = f"presidio-analyzer niet geïnstalleerd ({str(exc)[:80]})"
        return None
    try:
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "nl", "model_name": "nl_core_news_sm"}]})
        _PRESIDIO = AnalyzerEngine(nlp_engine=provider.create_engine(),
                                   supported_languages=["nl"])
        _PRESIDIO_REDEN = None
    except Exception as exc:  # noqa: BLE001
        _PRESIDIO_REDEN = (f"taalmodel ontbreekt: {str(exc)[:120]} — installeer met "
                           f"`python -m spacy download nl_core_news_sm`")
        _PRESIDIO = None
    return _PRESIDIO


# Wat we van Presidio gebruiken: ALLEEN wat het toevoegt aan wat we zelf al
# doen. Namen, plaatsen en telefoonnummers hebben geen vast patroon en komen
# uit het taalmodel — dat is de helft die je met reguliere expressies niet
# krijgt, en de enige reden dat Presidio hier staat.
#
# IBAN, creditcard en e-mail staan er bewust NIET bij. Die doen onze eigen
# detectors al, mét checksum, en dus beter. Ze tóch opvragen kost twee dingen:
# een waarschuwing per aanroep ("Entity CREDIT_CARD doesn't have the
# corresponding recognizer in language: nl" — het Nederlandse register heeft
# ze niet) en een logbestand waarin de échte waarschuwingen wegvallen.
_PRESIDIO_ENTITEITEN = ["PERSON", "LOCATION", "PHONE_NUMBER"]
_PRESIDIO_DREMPEL = 0.6


def _presidio_treffers(tekst: str) -> List[Tuple[int, int, str, str]]:
    """(start, eind, treffer, categorie) volgens Presidio, of niets."""
    motor = _laad_presidio()
    if motor is None:
        return []
    try:
        uit = motor.analyze(text=tekst, language="nl", entities=_PRESIDIO_ENTITEITEN)
    except Exception as exc:  # noqa: BLE001 — de guard mag hier niet op vallen
        log.warningx("Presidio-analyse mislukt", error=str(exc)[:200])
        return []
    return [(r.start, r.end, tekst[r.start:r.end], r.entity_type)
            for r in uit if r.score >= _PRESIDIO_DREMPEL]


# ── classificeren ───────────────────────────────────────────────────────────

def regels(db: Session, doel: str) -> List[GuardRule]:
    return (db.query(GuardRule)
            .filter(GuardRule.target == doel, GuardRule.enabled == True)  # noqa: E712
            .order_by(GuardRule.sort_order, GuardRule.id).all())


def _treffers_van(rij: GuardRule, tekst: str) -> List[Tuple[int, int, str]]:
    if rij.kind == "ingebouwd":
        zoeker = INGEBOUWD.get(str(rij.key or ""))
        return zoeker(tekst) if zoeker else []
    if not rij.pattern:
        return []
    try:
        patroon = re.compile(rij.pattern, re.IGNORECASE)
    except re.error as exc:
        log.warningx("Guard-regel heeft een ongeldig patroon", regel=rij.name,
                     error=str(exc)[:120])
        return []
    return [(m.start(), m.end(), m.group()) for m in patroon.finditer(tekst)]


def beoordeel_opdracht(db: Session, command: Optional[str]) -> Dict[str, Any]:
    """Mag dit commando draaien?

    `toelaten` wint van alles: dat is hoe je een uitzondering maakt zonder de
    onderliggende regel te moeten wissen. Daarna wint de strengste actie.
    """
    tekst = command or ""
    bevindingen: List[Dict[str, Any]] = []
    if not tekst.strip():
        return {"actie": "doorgelaten", "findings": []}

    vrijgesteld = None
    streng = None
    for rij in regels(db, "opdracht"):
        treffers = _treffers_van(rij, tekst)
        if not treffers:
            continue
        bevindingen.append({"regel": rij.name, "categorie": rij.category,
                            "actie": rij.action, "aantal": len(treffers),
                            "voorbeeld": treffers[0][2][:80]})
        if rij.action == "toelaten" and vrijgesteld is None:
            vrijgesteld = rij
        elif rij.action == "blokkeren" and streng is None:
            streng = rij

    if vrijgesteld is not None:
        return {"actie": "doorgelaten", "findings": bevindingen,
                "reden": f"vrijgesteld door '{vrijgesteld.name}'"}
    if streng is not None:
        return {"actie": "geweigerd", "findings": bevindingen,
                "reden": f"{streng.name} ({streng.category})",
                "regel": streng.name}
    return {"actie": "doorgelaten", "findings": bevindingen}


def beoordeel_uitvoer(db: Session, tekst: Optional[str], *,
                      met_presidio: bool = True) -> Dict[str, Any]:
    """Wat mag er van deze uitvoer naar het model?

    Geeft de (eventueel gemaskeerde) tekst terug plus wat er gevonden is.
    Maskeren is de standaard en met reden: de hele uitvoer weggooien omdat er
    één BSN in staat, kost je ook de exitcode, het pad en de foutmelding die je
    nodig had.
    """
    origineel = tekst or ""
    if not origineel:
        return {"actie": "doorgelaten", "tekst": origineel, "findings": []}

    # (start, eind, label) — later van achteren naar voren vervangen, zodat de
    # posities die we al hebben niet verschuiven.
    te_maskeren: List[Tuple[int, int, str]] = []
    bevindingen: List[Dict[str, Any]] = []
    blokkeer: Optional[GuardRule] = None

    for rij in regels(db, "uitvoer"):
        treffers = _treffers_van(rij, origineel)
        if not treffers:
            continue
        bevindingen.append({"regel": rij.name, "categorie": rij.category,
                            "actie": rij.action, "aantal": len(treffers)})
        if rij.action == "blokkeren" and blokkeer is None:
            blokkeer = rij
        elif rij.action == "maskeren":
            for start, eind, _ in treffers:
                te_maskeren.append((start, eind, rij.category))

    if met_presidio:
        # Eén aanroep: de analyse is het duurste wat hier gebeurt.
        presidio = _presidio_treffers(origineel)
        for start, eind, _treffer, soort in presidio:
            te_maskeren.append((start, eind, soort.lower()))
        if presidio:
            bevindingen.append({"regel": "Presidio (namen, plaatsen, nummers)",
                                "categorie": "persoonsgegeven",
                                "actie": "maskeren", "aantal": len(presidio)})

    if blokkeer is not None:
        return {"actie": "geblokkeerd", "tekst": None, "findings": bevindingen,
                "reden": f"{blokkeer.name} ({blokkeer.category})"}

    if not te_maskeren:
        return {"actie": "doorgelaten", "tekst": origineel, "findings": bevindingen}

    uit = maskeer(origineel, te_maskeren)
    return {"actie": "gemaskeerd", "tekst": uit, "findings": bevindingen}


def maskeer(tekst: str, plekken: List[Tuple[int, int, str]]) -> str:
    """Treffers vervangen door een leesbaar merkteken.

    Van achteren naar voren, en overlappingen samengevoegd: twee regels die
    hetzelfde stuk raken (een IBAN die ook als creditcard telt) zouden anders
    over elkaar heen schrijven en de tekst verminken.
    """
    if not plekken:
        return tekst
    geordend = sorted(plekken, key=lambda p: (p[0], -p[1]))
    samen: List[Tuple[int, int, str]] = []
    for start, eind, label in geordend:
        if samen and start <= samen[-1][1]:
            vorig = samen[-1]
            samen[-1] = (vorig[0], max(vorig[1], eind), vorig[2])
        else:
            samen.append((start, eind, label))
    uit = tekst
    for start, eind, label in reversed(samen):
        uit = uit[:start] + f"[{label} verborgen]" + uit[eind:]
    return uit


# ── voor het testveld in de UI ──────────────────────────────────────────────

def test_regel(kind: str, pattern: Optional[str], key: Optional[str],
               voorbeeld: str) -> Dict[str, Any]:
    """Wat zou deze regel doen met deze tekst? Voor het testveld in de UI, dat
    er is omdat een reguliere expressie die alles of niets pakt anders pas
    opvalt als de guard al een week het verkeerde doet."""
    if kind == "ingebouwd":
        zoeker = INGEBOUWD.get(str(key or ""))
        if zoeker is None:
            return {"ok": False, "fout": f"Onbekende ingebouwde detector '{key}'."}
        treffers = zoeker(voorbeeld or "")
    else:
        if not pattern:
            return {"ok": False, "fout": "Geef een patroon op."}
        try:
            patroon = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return {"ok": False, "fout": f"Ongeldig patroon: {exc}"}
        treffers = [(m.start(), m.end(), m.group()) for m in patroon.finditer(voorbeeld or "")]

    uit: Dict[str, Any] = {
        "ok": True,
        "aantal": len(treffers),
        "treffers": [t[2][:120] for t in treffers[:10]],
        "voorbeeld_gemaskeerd": maskeer(voorbeeld or "",
                                        [(a, b, "treffer") for a, b, _ in treffers]),
    }
    # De twee manieren waarop een zelfgemaakte regel meestal fout is.
    if voorbeeld and not treffers:
        uit["waarschuwing"] = ("Geen enkele treffer in deze tekst. Klopt het patroon, "
                               "of is dit geen goed voorbeeld?")
    elif treffers and len("".join(t[2] for t in treffers)) > 0.8 * len(voorbeeld or " "):
        uit["waarschuwing"] = ("Dit patroon raakt bijna de hele tekst. Zo'n regel "
                               "maskeert straks alles en maakt de uitvoer onbruikbaar.")
    return uit
