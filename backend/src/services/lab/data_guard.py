"""
services/lab/data_guard.py

Data-egress-guard (rule-based + optional local classifier), ported
near-verbatim from ND3X-public/src/services/playground/data_guard.py. Data in
a lab may not leave the container — not even toward the LLM. Tool output that
comes back from the container into the chat pipeline passes this guard.

The essential distinction: infra-/metadata inventory (workspace names,
schemas, counts — may pass) versus customer RECORDS (rows with business
values — must be blocked). Three layers, increasing in certainty:

1. Hard PII net (checksum-validated: BSN 11-check, IBAN mod-97, credit card
   Luhn, email) — always blocks, regardless of provenance.
2. Provenance/command classification: control-plane (management REST APIs,
   `az … list`, DESCRIBE, LIMIT 0) = metadata -> pass; data-plane (raw
   table/file reads, SELECT rows, pandas.read_*) = customer data -> strict.
3. Structural record detection: a typed result set (>=2 consistent columns
   with >=1 numeric/date/currency column) = records -> block; a single-column
   homogeneous list (names/GUIDs) = inventory -> pass.

Deliberate hard limit: in-container aggregation (read + `value_counts` +
print in one process) yields innocent-looking output no line of text can
distinguish from legitimate prose. For that there is (a) session taint — after
a data-plane read the session gets stricter — and (b) the optional local-model
classifier (track B, data_guard_llm.py), which runs locally so nothing leaks.
The rules here remain the hard floor.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# ── PII patterns (checksum-validated where possible) ─────────────────────────
_NINE_DIGITS = re.compile(r"\b\d{9}\b")                       # NL BSN candidate
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_CARD = re.compile(r"\b\d{13,19}\b|\b\d{4}(?:[ -]\d{4}){3}\b")

MAX_EGRESS_CHARS = 20_000
_PII_HIT_THRESHOLD = 5
_RECORD_ROW_THRESHOLD = 10
_RECORD_ROW_THRESHOLD_STRICT = 3

GUARD_MESSAGE = (
    "[Lab data-guard] De output is TEGENGEHOUDEN: hij lijkt klant- of "
    "recorddata te bevatten ({reason}). Gegevens mogen het lab niet "
    "verlaten. Werk metadata-gericht: vraag schema's, kolomnamen en aantallen "
    "op (bijv. LIMIT 0, DESCRIBE, `head -c` op configbestanden) en rapporteer "
    "geaggregeerde resultaten alleen als die geen klantgegevens prijsgeven."
)

# ── Provenance: control-plane (metadata) vs data-plane (records) ─────────────
# Eén bestandslezing, gedeeld met governed_policy. `[^|;&\n]*` en niet `[^|]*`:
# dat laatste liep over REGELGRENZEN heen, en koppelde dus de `cat` van
# `TOKEN=$(cat /tmp/token.txt)` aan een `.json` tien regels verderop. Zo telde
# élk script dat een token uitleest en ergens een .json noemt als "leest ruwe
# bestandsinhoud" — 62 Fabric-commando's op één dag, allemaal onterecht.
_BESTANDSLEZING = (
    r"\b(cat|head|tail|less|more|xxd|strings|od)\b[^|;&\n]*"
    r"\.(csv|parquet|json|jsonl|ndjson|tsv|avro|orc|xlsx?)\b")

_CONTROL_PLANE = [
    r"api\.fabric\.microsoft\.com/v1/[^ ]*(workspaces|items|capacities|connections|gateways)",
    r"api\.powerbi\.com/v1\.0/",
    r"management\.azure\.com/[^ ]*(providers|resourceGroups|subscriptions)",
    r"\baz\s+\S+(\s+\S+)?\s+(list|show)\b",
    r"\baz\s+account\b",
    r"\binformation_schema\b",
    r"\bDESCRIBE\b", r"\bSHOW\s+(TABLES|COLUMNS|SCHEMAS|DATABASES|PARTITIONS)\b",
    r"\bLIMIT\s+0\b",
    r"\baz\s+storage\s+fs\s+(file\s+)?list\b",
]
# HARDE data-plane: hier worden echt records gelezen. Deze winnen altijd, ook
# van een control-plane-aanwijzing in hetzelfde script.
_DATA_PLANE_HARD = [
    r"onelake\.dfs\.fabric\.microsoft\.com/[^ ]+/(Tables|Files)/",
    r"\baz\s+storage\s+(fs\s+file|blob)\s+download\b",
    r"\bSELECT\b(?![^;]*\bLIMIT\s+0\b)[^;]*\bFROM\b",
    r"\bpd\.read_\w+|\bpandas\.read_\w+",
    r"\.to_(csv|json|dict|string|markdown|records)\(",
    r"\bvalue_counts\(|\.head\(|\.sample\(|\.describe\(|\.groupby\(",
    r"\b(duckdb|pyarrow|deltalake|polars)\b",
]
# VERMOEDELIJKE data-plane: "iemand leest een bestand dat een datafile kán
# zijn". Een gok op de extensie, en die gok verliest van een expliciete
# control-plane-aanwijzing — een pipeline-definitie is óók json.
_DATA_PLANE_ZACHT = [
    _BESTANDSLEZING,
]
_CONTROL_RE = re.compile("|".join(_CONTROL_PLANE), re.IGNORECASE)
_DATA_HARD_RE = re.compile("|".join(_DATA_PLANE_HARD), re.IGNORECASE)
_DATA_ZACHT_RE = re.compile("|".join(_DATA_PLANE_ZACHT), re.IGNORECASE)


def classify_command(command: Optional[str]) -> str:
    """Provenance of a shell command: 'data' (reads records), 'control'
    (metadata/inventory) or 'unknown'.

    Harde data wint van alles. Daarna wint CONTROL van de zachte
    bestandsheuristiek, en niet andersom: een script dat een Fabric-definitie
    ophaalt en het antwoord uitleest, leest geen klantgegevens — het leest
    code. Andersom bleef er van de guard niets over, want zo'n script noemt
    bijna altijd wel een .json.
    """
    if not command:
        return "unknown"
    if _DATA_HARD_RE.search(command):
        return "data"
    if _CONTROL_RE.search(command):
        return "control"
    if _DATA_ZACHT_RE.search(command):
        return "data"
    return "unknown"


# ── Checksums ────────────────────────────────────────────────────────────────
def _is_valid_bsn(value: str) -> bool:
    digits = [int(c) for c in value]
    total = sum(d * w for d, w in zip(digits, (9, 8, 7, 6, 5, 4, 3, 2, -1)))
    return total % 11 == 0 and any(digits)


def _iban_ok(value: str) -> bool:
    v = value.upper()
    v = v[4:] + v[:4]
    try:
        num = "".join(str(int(c, 36)) for c in v)
        return int(num) % 97 == 1
    except ValueError:
        return False


def _luhn_ok(value: str) -> bool:
    digits = [int(c) for c in re.sub(r"[ -]", "", value)]
    if len(digits) < 13:
        return False
    total, alt = 0, False
    for d in reversed(digits):
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


# ── Structural record detection ──────────────────────────────────────────────
_NUM_RE = re.compile(r"^[-+]?(\d{1,3}([.,]\d{3})*|\d+)([.,]\d+)?$")
_CURRENCY_RE = re.compile(r"^[€$£]\s?\d|\d+[.,]\d{2}\s?(kg|g|eur|usd)?$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?|^\d{2}[-/]\d{2}[-/]\d{4}")


def _is_value_cell(s: str) -> bool:
    s = s.strip().strip('"')
    if not s:
        return False
    return bool(_NUM_RE.match(s) or _CURRENCY_RE.match(s) or _DATE_RE.match(s))


def _delimited_record_rows(lines: List[str]) -> Tuple[int, int]:
    best = (0, 0)
    for delim in (",", ";", "\t", "|"):
        rows = [ln for ln in lines if ln.count(delim) >= 1]
        if len(rows) < 3:
            continue
        counts = [ln.count(delim) + 1 for ln in rows]
        ncols = max(set(counts), key=counts.count)
        if ncols < 2:
            continue
        consistent = [r for r, c in zip(rows, counts) if c == ncols]
        if len(consistent) < 0.8 * len(rows):
            continue
        cells = [[c.strip() for c in r.split(delim)] for r in consistent]
        typed_col = False
        for col in range(ncols):
            vals = [row[col] for row in cells if col < len(row)]
            if vals and sum(_is_value_cell(v) for v in vals) >= 0.6 * len(vals):
                typed_col = True
                break
        if typed_col and len(consistent) > best[0]:
            best = (len(consistent), ncols)
    return best


def _json_record_rows(text: str) -> int:
    t = text.strip()
    if not (t.startswith("[") or t.startswith("{")):
        return 0
    try:
        data = json.loads(t)
    except (ValueError, TypeError):
        return 0
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), None)
    if not isinstance(data, list) or len(data) < 3:
        return 0
    objs = [o for o in data if isinstance(o, dict)]
    if len(objs) < 3:
        return 0

    def _has_value_field(o: dict) -> bool:
        for v in o.values():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                return True
            if isinstance(v, str) and _is_value_cell(v):
                return True
        return False

    hits = sum(1 for o in objs if _has_value_field(o))
    return hits if hits >= 0.6 * len(objs) else 0


def record_rows(text: str) -> int:
    """Number of recognized customer-record rows (delimited or JSON). 0 = no
    records (e.g. a name list or free text)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    delim_rows, _ = _delimited_record_rows(lines)
    return max(delim_rows, _json_record_rows(text))


# ── Core inspection ───────────────────────────────────────────────────────────
def inspect_output(text: str, *, provenance: str = "unknown",
                   tainted: bool = False, streng: bool = False) -> Dict[str, Any]:
    """Judge container output before it goes to the model.
    Returns {allowed, reason} — reason only set on a block.

    `streng` komt van een opdracht die eruitzag alsof hij data leest zonder dat
    zeker te weten. Dan geldt de strenge drempel en telt één record al — maar
    het is nog steeds de INHOUD die beslist, niet de vorm van het commando."""
    sample = text[:200_000]

    bsn = sum(1 for m in _NINE_DIGITS.finditer(sample) if _is_valid_bsn(m.group()))
    if bsn >= _PII_HIT_THRESHOLD:
        return {"allowed": False, "reason": f"{bsn} BSN-nummers"}
    iban = sum(1 for m in _IBAN.finditer(sample) if _iban_ok(m.group()))
    if iban >= _PII_HIT_THRESHOLD:
        return {"allowed": False, "reason": f"{iban} IBAN-rekeningnummers"}
    card = sum(1 for m in _CARD.finditer(sample) if _luhn_ok(m.group()))
    if card >= _PII_HIT_THRESHOLD:
        return {"allowed": False, "reason": f"{card} creditcardnummers"}
    email = len(set(_EMAIL.findall(sample)))
    if email >= _PII_HIT_THRESHOLD * 3:
        return {"allowed": False, "reason": f"{email} e-mailadressen"}

    if provenance == "control" and not streng:
        return {"allowed": True, "reason": None}

    rows = record_rows(sample)
    threshold = (_RECORD_ROW_THRESHOLD_STRICT
                 if provenance == "data" or tainted or streng else _RECORD_ROW_THRESHOLD)
    if rows >= threshold:
        return {"allowed": False, "reason": f"~{rows} klant-records (dataset)"}

    if (provenance == "data" or streng) and rows >= 1:
        return {"allowed": False, "reason": "recordinhoud uit een data-lezing"}

    return {"allowed": True, "reason": None}


def guard_lab_output(result: Dict[str, Any], *, enabled: bool,
                     command: Optional[str] = None,
                     lab_id: Optional[str] = None,
                     provenance_override: Optional[str] = None,
                     db: Optional[Any] = None,
                     lab_name: Optional[str] = None,
                     worker_id: Optional[int] = None,
                     run_id: Optional[str] = None) -> Dict[str, Any]:
    """De guard op een exec-resultaat {exit_code, output, truncated}.

    **Classifier en guard zijn gescheiden.** Wat er IN de tekst zit, bepaalt
    services/lab/classifier.py aan de hand van regels die je zelf kunt
    aanpassen. Wat daarmee gebeurt — maskeren, blokkeren, alleen noteren —
    staat per regel in diezelfde tabel. Deze functie voert dat uit en legt vast
    wat er is gebeurd.

    **Maskeren is de standaard, en met reden.** De oude guard verving bij één
    treffer de HELE uitvoer door een melding. Daarmee raakte je ook de
    exitcode, het pad en de foutmelding kwijt die je nodig had — en dat is de
    reden dat de guard het werk in de weg zat in plaats van het te beschermen.

    `db` is optioneel zodat bestaande aanroepers blijven werken; zonder sessie
    valt hij terug op de regelmatige detectors zonder audit.
    """
    if not enabled:
        return result
    text = result.get("output") or ""

    if db is None:
        # Geen sessie meegekregen: eigen kortlevende sessie. De classifier
        # heeft de regels uit de database nodig, en die zijn het hele punt.
        from db.database import SessionLocal
        eigen = SessionLocal()
        try:
            return _guard_met_db(result, text, command=command, lab_id=lab_id,
                                 provenance_override=provenance_override, db=eigen,
                                 lab_name=lab_name, worker_id=worker_id, run_id=run_id)
        finally:
            eigen.close()
    return _guard_met_db(result, text, command=command, lab_id=lab_id,
                         provenance_override=provenance_override, db=db,
                         lab_name=lab_name, worker_id=worker_id, run_id=run_id)


def _guard_met_db(result: Dict[str, Any], text: str, *, command, lab_id,
                  provenance_override, db, lab_name, worker_id, run_id) -> Dict[str, Any]:
    from services.lab import classifier, guard_audit_service as audit

    # 1. De OPDRACHT. Alleen hier kun je `SELECT MAX(bedrag)` tegenhouden: dat
    #    levert één getal op dat in geen enkele uitvoercontrole opvalt.
    opdracht = classifier.beoordeel_opdracht(db, command)
    if opdracht["actie"] == "geweigerd":
        audit.leg_vast(db, lab_id=lab_id, lab_name=lab_name, worker_id=worker_id,
                       run_id=run_id, command=command, outcome="geweigerd",
                       origineel=text, geleverd=None, findings=opdracht["findings"])
        return {
            **result,
            "output": GUARD_MESSAGE.format(reason=opdracht.get("reden") or "opdrachtregel"),
            "guarded": True,
            "guard_reason": f"opdracht geweigerd: {opdracht.get('reden')}",
            "guard_facts": {"fase": "opdracht", "findings": opdracht["findings"],
                            "output_bytes": len(text)},
        }

    # 2. De UITVOER. Maskeren waar het kan, blokkeren waar een regel dat vraagt.
    uitvoer = classifier.beoordeel_uitvoer(db, text)
    bevindingen = list(opdracht["findings"]) + list(uitvoer["findings"])

    if uitvoer["actie"] == "geblokkeerd":
        audit.leg_vast(db, lab_id=lab_id, lab_name=lab_name, worker_id=worker_id,
                       run_id=run_id, command=command, outcome="geblokkeerd",
                       origineel=text, geleverd=None, findings=bevindingen)
        return {
            **result,
            "output": GUARD_MESSAGE.format(reason=uitvoer.get("reden") or "uitvoerregel"),
            "guarded": True,
            "guard_reason": uitvoer.get("reden"),
            "guard_facts": {"fase": "uitvoer", "findings": bevindingen,
                            "output_bytes": len(text)},
        }

    geleverd = uitvoer["tekst"]

    # 3. Het LOKALE MODEL gebeurt HIERNA, in de aanroeper. Die is async en kan
    #    het netjes afwachten; deze functie is synchroon en zou de hele backend
    #    stilzetten. Zie tool_execution_service._tweede_mening, die het oordeel
    #    ophaalt en deze auditregel bijwerkt.
    afgekapt = False
    if len(geleverd) > MAX_EGRESS_CHARS:
        geleverd = geleverd[:MAX_EGRESS_CHARS] + "\n… [afgekapt door data-guard]"
        afgekapt = True

    audit_id = audit.leg_vast(
        db, lab_id=lab_id, lab_name=lab_name, worker_id=worker_id, run_id=run_id,
        command=command,
        outcome="gemaskeerd" if uitvoer["actie"] == "gemaskeerd" else "doorgelaten",
        origineel=text, geleverd=geleverd, findings=bevindingen)
    uit = {**result, "output": geleverd,
           "guard_facts": {"fase": "uitvoer", "findings": bevindingen,
                           "output_bytes": len(text), "audit_id": audit_id,
                           "egress_truncated": afgekapt}}
    if uitvoer["actie"] == "gemaskeerd":
        uit["guard_masked"] = True
    if afgekapt:
        uit["truncated"] = True
    return uit
