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
_DATA_PLANE = [
    r"onelake\.dfs\.fabric\.microsoft\.com/[^ ]+/(Tables|Files)/",
    r"\baz\s+storage\s+(fs\s+file|blob)\s+download\b",
    r"\bSELECT\b(?![^;]*\bLIMIT\s+0\b)[^;]*\bFROM\b",
    r"\b(cat|head|tail|less|more|xxd|strings|od)\b[^|]*\.(csv|parquet|json|jsonl|ndjson|tsv|avro|orc|xlsx?)",
    r"\bpd\.read_\w+|\bpandas\.read_\w+",
    r"\.to_(csv|json|dict|string|markdown|records)\(",
    r"\bvalue_counts\(|\.head\(|\.sample\(|\.describe\(|\.groupby\(",
    r"\b(duckdb|pyarrow|deltalake|polars)\b",
]
_CONTROL_RE = re.compile("|".join(_CONTROL_PLANE), re.IGNORECASE)
_DATA_RE = re.compile("|".join(_DATA_PLANE), re.IGNORECASE)


def classify_command(command: Optional[str]) -> str:
    """Provenance of a shell command: 'data' (reads records), 'control'
    (metadata/inventory) or 'unknown'. Data wins over control on doubt."""
    if not command:
        return "unknown"
    if _DATA_RE.search(command):
        return "data"
    if _CONTROL_RE.search(command):
        return "control"
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
                   tainted: bool = False) -> Dict[str, Any]:
    """Judge container output before it goes to the model.
    Returns {allowed, reason} — reason only set on a block."""
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

    if provenance == "control":
        return {"allowed": True, "reason": None}

    rows = record_rows(sample)
    threshold = (_RECORD_ROW_THRESHOLD_STRICT
                 if provenance == "data" or tainted else _RECORD_ROW_THRESHOLD)
    if rows >= threshold:
        return {"allowed": False, "reason": f"~{rows} klant-records (dataset)"}

    if provenance == "data" and rows >= 1:
        return {"allowed": False, "reason": "data-plane read met recordinhoud"}

    return {"allowed": True, "reason": None}


def guard_lab_output(result: Dict[str, Any], *, enabled: bool,
                     command: Optional[str] = None,
                     lab_id: Optional[str] = None,
                     provenance_override: Optional[str] = None) -> Dict[str, Any]:
    """Apply the guard to an exec-style result {exit_code, output, truncated}.

    ``command`` gives the provenance (metadata vs customer data) for shell
    commands. ``provenance_override`` lets a non-shell origin (an in-lab MCP
    tool call, which has no command string) supply its own control/data
    label instead — see services/mcp/lab_stdio_bridge.py, which defaults
    unlabeled tools to "data" (strict) rather than "unknown"."""
    if not enabled:
        return result
    text = result.get("output") or ""

    try:
        from services.lab.governed_policy import classify_command as _gov
        gov_class, gov_reason = _gov(command)
    except Exception:  # noqa: BLE001 — policy must never break the guard
        gov_class, gov_reason = ("unknown", "")

    provenance = provenance_override or (
        "control" if gov_class == "counting_safe" else classify_command(command)
    )

    tainted = False
    if lab_id:
        try:
            from services.lab.execution_context import (
                is_data_plane_tainted, mark_data_plane_touched)
            tainted = is_data_plane_tainted(lab_id)
            if provenance == "data" or gov_class == "value_revealing":
                mark_data_plane_touched(lab_id)
        except Exception:  # noqa: BLE001 — taint must never break the guard
            pass

    facts = {
        "gov_class": gov_class,
        "gov_reason": gov_reason,
        "provenance": provenance,
        "tainted": tainted,
        "output_bytes": len(text),
    }

    if gov_class == "value_revealing":
        return {
            **result,
            "output": GUARD_MESSAGE.format(reason=f"waarde-onthullende query ({gov_reason})"),
            "guarded": True,
            "guard_reason": f"value_revealing: {gov_reason}",
            "guard_facts": facts,
        }

    verdict = inspect_output(text, provenance=provenance, tainted=tainted)
    if not verdict["allowed"]:
        return {
            **result,
            "output": GUARD_MESSAGE.format(reason=verdict["reason"]),
            "guarded": True,
            "guard_reason": verdict["reason"],
            "guard_facts": {**facts, "verdict_reason": verdict["reason"]},
        }
    if len(text) > MAX_EGRESS_CHARS:
        return {**result, "output": text[:MAX_EGRESS_CHARS] + "\n… [afgekapt door data-guard]",
                "truncated": True, "guard_facts": {**facts, "egress_truncated": True}}
    return {**result, "guard_facts": facts}
