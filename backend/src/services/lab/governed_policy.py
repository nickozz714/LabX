"""
services/lab/governed_policy.py

Ported verbatim (translated comments only) from
ND3X-public/src/services/playground/governed_policy.py. Governed-tool-policy
for the data-egress guard: classifies a command/query as COUNTING-SAFE or
VALUE-REVEALING. This is the deterministic, shape-based core that a text
scanner alone can't make reliable.

Principle: data-quality work is mostly COUNTS and STRUCTURE — those reveal no
individual customer and may go back to the cloud model. The moment a query
returns REAL VALUES or MAGNITUDES (column content, distinct values,
min/max/sum/avg, group labels, samples), that's customer data and belongs to
the USER, not the model.
"""
from __future__ import annotations

import re
from typing import Tuple

# Gedeeld met data_guard: één plek voor "wat is control-plane", "wat is hard
# data-plane" en "hoe ziet een bestandslezing eruit". Twee kopieën van deze
# patronen die uit elkaar lopen is precies hoe een guard onvoorspelbaar wordt.
from services.lab.data_guard import (_BESTANDSLEZING, _CONTROL_RE,  # noqa: F401
                                     _DATA_HARD_RE)

_BESTANDSLEZING_RE = re.compile(_BESTANDSLEZING, re.IGNORECASE)


def _harde_data(command: str) -> bool:
    """Leest dit script écht records? Dan wint dat van elke control-plane-
    aanwijzing in hetzelfde script."""
    return bool(_DATA_HARD_RE.search(command or ""))

# ── VALUE-REVEALING (block toward the model) ─────────────────────────────────
_VALUE_PATTERNS = [
    r"\b(MIN|MAX|SUM|AVG|MEAN|MEDIAN|MODE|STDDEV|STDEV|VAR|VARIANCE|PERCENTILE\w*|APPROX_PERCENTILE)\s*\(",
    r"\bSELECT\s+DISTINCT\b",
    r"\bGROUP\s+BY\b",
    r"\bORDER\s+BY\b.*\b(LIMIT|TOP)\b",
    r"\.value_counts\s*\(", r"\.unique\s*\(",
    r"\.head\s*\(", r"\.tail\s*\(", r"\.sample\s*\(", r"\.describe\s*\(",
    r"\.mode\s*\(", r"\.nlargest\s*\(", r"\.nsmallest\s*\(",
    r"\.groupby\s*\(",
    r"\.(min|max|mean|median|sum|std|var|quantile)\s*\(",
    r"\.to_(csv|json|dict|markdown|string|records|numpy|list)\s*\(",
    r"\.tolist\s*\(",
]
_VALUE_RE = re.compile("|".join(_VALUE_PATTERNS), re.IGNORECASE)

# ── STRUCTURE-SAFE (metadata; safe even for a SELECT) ────────────────────────
_STRUCTURE_SAFE_PATTERNS = [
    r"\binformation_schema\b",
    r"(?<!\.)\bDESCRIBE\s+\w",
    r"\bSHOW\s+(TABLES|COLUMNS|SCHEMAS|DATABASES|PARTITIONS|TBLPROPERTIES)\b",
    r"\bEXPLAIN\b", r"\bLIMIT\s+0\b",
    r"\.shape\b", r"\.columns\b", r"\.dtypes\b", r"\.info\s*\(",
    r"\.isnull\s*\(\s*\)\s*\.sum", r"\.isna\s*\(\s*\)\s*\.sum",
    r"\.nunique\s*\(", r"\blen\s*\(",
]
_STRUCTURE_SAFE_RE = re.compile("|".join(_STRUCTURE_SAFE_PATTERNS), re.IGNORECASE)

_COUNT_RE = re.compile(r"\bCOUNT\s*\(|\.count\s*\(", re.IGNORECASE)
_SELECT_FROM_RE = re.compile(r"\bSELECT\b(?P<cols>.*?)\bFROM\b", re.IGNORECASE | re.DOTALL)


def _select_returns_values(command: str) -> bool:
    m = _SELECT_FROM_RE.search(command)
    if not m:
        return False
    if "LIMIT 0" in command.upper():
        return False
    cols = m.group("cols")
    wo = re.sub(r"\b(COUNT|EXISTS)\s*\([^)]*\)", "", cols, flags=re.IGNORECASE)
    if "*" in wo:
        return True
    wo = re.sub(r"\bAS\s+\w+", " ", wo, flags=re.IGNORECASE)
    wo = re.sub(r"[-+*/%,()]", " ", wo)
    wo = re.sub(r"\b\d+(\.\d+)?\b", " ", wo)
    return any(re.match(r"^[A-Za-z_]", t) for t in wo.split())


# Een heredoc die naar een BESTAND gaat, is code die geschreven wordt — geen
# query die draait. `cat > cel4.py << 'EOF' … df.groupBy(…) … EOF` produceert
# geen enkele rij; het legt een script neer dat later misschien iets doet, en
# dán wordt dát commando beoordeeld. De inhoud meewegen blokkeerde het
# SCHRIJVEN van pyspark-code, en dat is precies het werk waar een lab voor is.
#
# Let op de grens: een heredoc naar een INTERPRETER (`python3 << 'EOF'`) wordt
# wél uitgevoerd en blijft dus gewoon meetellen. Daar zit het verschil tussen
# "ik leg een bestand neer" en "ik draai dit nu".
_HEREDOC_NAAR_BESTAND = re.compile(
    r"(?:\bcat\b|\btee\b)[^\n<]*>\s*\S+[^\n<]*<<-?\s*['\"]?(?P<eind>\w+)['\"]?\s*\n"
    r".*?^(?P=eind)\s*$",
    re.DOTALL | re.MULTILINE)


def _zonder_geschreven_code(command: str) -> str:
    return _HEREDOC_NAAR_BESTAND.sub("[code weggeschreven]", command or "")


def classify_command(command: str | None) -> Tuple[str, str]:
    """Classificeer een shell/SQL-commando.

    Vier uitkomsten, en het verschil tussen de laatste twee is de kern van deze
    module:

    - `counting_safe`   — telling/structuur/beheer-API; mag terug naar het model.
    - `value_revealing` — de opdracht ZELF onthult waarden: een SELECT van
      kolommen, MIN/MAX/SUM, GROUP BY. Hier is het commando het enige
      betrouwbare signaal, want `SELECT MAX(bedrag)` levert één getal op dat in
      geen enkele uitvoertelling opvalt en tóch een echte magnitude is. Dit
      blokkeert dus op zichzelf.
    - `vermoedelijk`    — de opdracht LIJKT data te lezen (een `cat` op een
      .json), maar dat is een gok op een bestandsextensie. Blokkeert niet zelf;
      zet de beoordeling van de uitvoer op streng.
    - `unknown`         — geen data-query herkend.

    Bij twijfel wint de strengere.
    """
    if not command:
        return ("unknown", "geen commando")
    # Code die wordt WEGGESCHREVEN telt niet mee: die draait nu niets.
    cmd = _zonder_geschreven_code(command)

    if re.search(r"\.(isnull|isna|notnull|notna)\s*\(\s*\)\s*\.sum\s*\(", cmd, re.IGNORECASE):
        return ("counting_safe", "null-telling (.isnull().sum())")

    if _STRUCTURE_SAFE_RE.search(cmd):
        return ("counting_safe", "structuur/metadata (information_schema/DESCRIBE/shape/…)")

    mv = _VALUE_RE.search(cmd)
    if mv:
        return ("value_revealing", f"waarde-onthullende operatie: {mv.group(0).strip()}")

    if _select_returns_values(cmd):
        return ("value_revealing", "SELECT geeft kolomwaarden/ruwe rijen terug")

    # Control-plane: de beheer-API's van Fabric, Power BI en Azure. Die geven
    # DEFINITIES en inventaris terug — een notebook, een pipeline, een lijst
    # workspaces — en dat is code en configuratie, geen klantgegevens. Wie hier
    # niets door laat, blokkeert precies het werk waar een lab voor bedoeld is:
    # op één dag sneuvelden 62 van dit soort commando's, allemaal onterecht.
    #
    # Deze toets staat NA de waarde-onthullers hierboven: doet hetzelfde script
    # ook een SELECT of een aggregatie, dan wint die. En vóór de
    # bestandsheuristiek hieronder, want een definitie is óók een .json.
    if not _harde_data(cmd) and _CONTROL_RE.search(cmd):
        return ("counting_safe",
                "beheer-API (definities/inventaris) — configuratie, geen klantgegevens")

    if _BESTANDSLEZING_RE.search(cmd):
        # VERMOEDEN, geen vaststelling. "Iemand leest een bestand met een
        # extensie die een datafile kán zijn" is een gok op basis van de
        # opdracht, en die gok zat er vaak naast: op één dag sneuvelden er
        # commando's met 13, 63 en 208 bytes uitvoer — een HTTP-status, een
        # padnaam. Die blokkeren omdat het cómmando verdacht oogt, terwijl de
        # uitvoer aantoonbaar geen klantgegevens bevat, is de verkeerde kant op
        # fout: het belemmert het werk zonder iets te beschermen.
        #
        # Daarom blokkeert dit niet meer op zichzelf; het zet de beoordeling
        # van de UITVOER op streng. Zit er wél data in, dan gaat hij alsnog
        # dicht — en dan op grond van wat er echt in staat.
        return ("vermoedelijk", "leest mogelijk een datafile; uitvoer streng beoordelen")

    if _COUNT_RE.search(cmd):
        return ("counting_safe", "telling (COUNT-familie)")

    if re.search(r"\b(pd|pandas)\.read_\w+|\bpl\.read_\w+|\bduckdb\b|\bdeltalake\b|\bpyarrow\b", cmd, re.IGNORECASE):
        return ("unknown", "data ingeladen; uitkomst-vorm onbekend")

    return ("unknown", "geen data-query herkend")


# In-lab MCP tools have no shell command to classify from (see
# services/mcp/lab_stdio_bridge.py); LabX gives each such tool a static
# provenance label instead, defaulting to "data" (strict) when unset — the
# ND3X validation report flagged this as an explicit design gap to close.
def classify_tool_call(provenance_label: str | None) -> Tuple[str, str]:
    label = (provenance_label or "data").strip().lower()
    if label == "control":
        return ("counting_safe", "tool gelabeld als control-plane")
    return ("value_revealing" if label == "value" else "unknown", f"tool gelabeld als {label}")


PLANNER_POLICY = (
    "### Governed data-policy — wat mag TERUG naar jou (het model)\n"
    "In een lab-gebonden run scheidt LabX twee soorten output. TELLING-"
    "VEILIGE resultaten mogen terug in jouw context; WAARDE-ONTHULLENDE resultaten "
    "gaan naar de GEBRUIKER (UI), niet naar jou — die worden voor jou geblokkeerd.\n\n"
    "TELLING-VEILIG (vraag deze gerust op; komt terug):\n"
    "- Structuur/metadata: information_schema, DESCRIBE, SHOW COLUMNS/TABLES, "
    "kolomnamen/types, table properties, partitionering, DESCRIBE HISTORY/DETAIL.\n"
    "- Tellingen: COUNT(*), COUNT(col), COUNT(DISTINCT col), null-counts "
    "(COUNT(*)-COUNT(col)), duplicaat-count voor PK-uniciteit, orphan-count voor "
    "referentiële integriteit, EXISTS/boolean-checks.\n\n"
    "WAARDE-ONTHULLEND (komt NIET bij jou terug; rapporteer dit aan de gebruiker "
    "of render het naar de UI):\n"
    "- SELECT van datakolommen, SELECT *, sample-rijen (head/tail/LIMIT n>0).\n"
    "- SELECT DISTINCT <col> (de echte waarden) — gebruik COUNT(DISTINCT col) als je "
    "alleen het AANTAL wilt.\n"
    "- MIN/MAX/SUM/AVG/STDDEV/MEDIAN/PERCENTILE/MODE (echte magnitudes).\n"
    "- GROUP BY op een waardekolom (de groep-labels + kleine groepen).\n"
    "- pandas/duckdb value-idioms: .value_counts(), .unique(), .head()/.sample(), "
    ".describe(), .min()/.max()/.mean()/.sum(), .to_csv/.to_dict/print(df[col]).\n\n"
    "Werk dus datakwaliteit metadata-gericht: profileer met TELLINGEN (nulls, "
    "uniciteit, integriteit, distinct-counts) en laat echte waarden/verdelingen "
    "aan de gebruiker zien i.p.v. ze zelf op te vragen. Probeer waarde-onthullende "
    "output NIET te omzeilen (chunken/coderen/parafraseren) — dat wordt geblokkeerd.\n"
    "Als output wordt tegengehouden, schrijft LabX de VOLLEDIGE uitkomst automatisch "
    "naar een bestand in /workspace/labx-data/ dat de gebruiker kan openen. Verwijs "
    "de gebruiker naar dat bestand (dat pad krijg je terug als 'data_sink') in plaats "
    "van de inhoud alsnog te proberen tonen."
)
