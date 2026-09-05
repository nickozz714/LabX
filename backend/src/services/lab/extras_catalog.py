"""
services/lab/extras_catalog.py

De meegeleverde lab-extra's: kant-en-klare pakketten die je bij het aanmaken
van een lab aanvinkt. Ze worden bij het opstarten in de `lab_extras`-tabel
gezet als ze er nog niet zijn — daarna is het gewone data die je in de UI mag
aanpassen, en overschrijft een nieuwe LabX-versie jouw aanpassing niet (er is
wel een "terugzetten naar origineel" per pakket, zie `builtin_for`).

Waarom niet één "Playwright"-pakket maar vier: `playwright install` haalt de
BROWSER binnen voor de taal-binding die je gebruikt. Python-scripts hebben de
Python-binding nodig, een MCP-server in het lab de Node-variant, en "echte
Chrome" is nog eens een aparte download naast Chromium. Ze door elkaar halen
levert precies het soort stille mislukking op waar dit hele scherm voor
bestaat: alles lijkt geïnstalleerd, en dan vindt de agent geen browser.

Alle scripts gaan uit van een Debian/Ubuntu-basis (elk preset-image is dat) en
draaien als root. Ze zijn best-effort: faalt er één, dan wordt dat op het lab
gelogd en gaan de andere gewoon door.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from component_logging import get_logger

log = get_logger(__name__)

# Zorgt dat curl/ca-certificates er zijn voordat een script iets ophaalt: de
# kale Debian- en sommige slim-images hebben ze niet, en dan faalt de eerste
# regel van bijna elk pakket hieronder.
_NEED_CURL = ('command -v curl >/dev/null 2>&1 || (apt-get update -qq && '
              'DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates)')

# Playwright zet zijn browsers hier neer (root draait de container).
_BROWSERS_DIR = "/root/.cache/ms-playwright"

BUILTIN_EXTRAS: List[Dict[str, Any]] = [
    {
        "key": "node",
        "label": "Node.js (LTS)",
        "description": "Node + npm + npx via NodeSource. Nodig voor alles wat met npx draait — "
                       "een MCP-server in het lab, of een front-end-project.",
        "check_cmd": "command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1",
        "install_script": (
            "set -e\n"
            f"{_NEED_CURL}\n"
            "curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -\n"
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs\n"
            "node --version"
        ),
        "requires": [],
        "timeout_s": 900,
        "sort_order": 10,
    },
    {
        "key": "playwright-python",
        "label": "Playwright (Python) + Chromium",
        "description": "De Python-binding van Playwright mét Chromium en alle systeembibliotheken "
                       "(`--with-deps`). Hiermee schrijft de agent Python-scripts die een browser "
                       "aansturen. Reken op ~400 MB en een paar minuten bij het eerste lab.",
        "check_cmd": (
            'python3 -c "import playwright" >/dev/null 2>&1 && '
            f'ls -d {_BROWSERS_DIR}/chromium-* >/dev/null 2>&1'
        ),
        "install_script": (
            "set -e\n"
            "command -v python3 >/dev/null 2>&1 || (apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-pip)\n"
            # --break-system-packages is nodig op images met een PEP 668-markering
            # (Debian bookworm en later); oudere pip's kennen de vlag niet, vandaar
            # de terugval.
            "python3 -m pip install --quiet --break-system-packages playwright "
            "|| python3 -m pip install --quiet playwright\n"
            "python3 -m playwright install --with-deps chromium"
        ),
        "requires": [],
        "timeout_s": 1800,
        "sort_order": 20,
    },
    {
        "key": "playwright-node",
        "label": "Playwright (Node) + Chromium",
        "description": "De Node-variant: `playwright` en `@playwright/test` globaal, plus Chromium "
                       "met systeembibliotheken. Voor JS/TS-projecten en als basis onder de "
                       "Playwright MCP-server.",
        "check_cmd": (
            "command -v playwright >/dev/null 2>&1 && "
            f"ls -d {_BROWSERS_DIR}/chromium-* >/dev/null 2>&1"
        ),
        "install_script": (
            "set -e\n"
            "npm install -g --silent playwright @playwright/test\n"
            "playwright install --with-deps chromium"
        ),
        "requires": ["node"],
        "timeout_s": 1800,
        "sort_order": 30,
    },
    {
        "key": "playwright-chrome",
        "label": "Echte Google Chrome (naast Chromium)",
        "description": "Installeert Chrome stable als extra browserkanaal — nodig als een site zich "
                       "op Chromium anders gedraagt of propriëtaire codecs/DRM vraagt. Vereist een "
                       "van de Playwright-pakketten hierboven; die kiest zelf het juiste kanaal.",
        "check_cmd": ("command -v google-chrome >/dev/null 2>&1 || "
                      "command -v google-chrome-stable >/dev/null 2>&1"),
        "install_script": (
            "set -e\n"
            "if command -v playwright >/dev/null 2>&1; then\n"
            "  playwright install --with-deps chrome\n"
            'elif python3 -c "import playwright" >/dev/null 2>&1; then\n'
            "  python3 -m playwright install --with-deps chrome\n"
            "else\n"
            '  echo "Geen Playwright gevonden — vink eerst Playwright (Python) of (Node) aan." >&2\n'
            "  exit 1\n"
            "fi"
        ),
        "requires": [],
        "timeout_s": 1800,
        "sort_order": 40,
    },
    {
        "key": "playwright-mcp",
        "label": "Playwright MCP-server (in het lab)",
        "description": "Zet `@playwright/mcp` in het lab neer, zodat de agent browser-TOOLS krijgt in "
                       "plaats van scripts te moeten schrijven. Koppel hem daarna op de MCP-pagina als "
                       "server met locatie 'lab' en commando: "
                       "mcp-server-playwright --headless --browser chromium",
        "check_cmd": "npm ls -g --depth=0 @playwright/mcp >/dev/null 2>&1",
        "install_script": (
            "set -e\n"
            "npm install -g --silent @playwright/mcp@latest\n"
            "command -v mcp-server-playwright"
        ),
        "requires": ["node", "playwright-node"],
        "timeout_s": 900,
        "sort_order": 50,
    },
    {
        "key": "uv",
        "label": "uv (snelle Python-packagemanager)",
        "description": "uv + uvx van Astral: pakketten en losse tools installeren zonder een venv op "
                       "te tuigen. Ook de runner van veel Python-MCP-servers.",
        "check_cmd": "command -v uv >/dev/null 2>&1",
        "install_script": (
            "set -e\n"
            f"{_NEED_CURL}\n"
            "curl -LsSf https://astral.sh/uv/install.sh | sh\n"
            "ln -sf /root/.local/bin/uv /usr/local/bin/uv\n"
            "ln -sf /root/.local/bin/uvx /usr/local/bin/uvx\n"
            "uv --version"
        ),
        "requires": [],
        "timeout_s": 600,
        "sort_order": 60,
    },
    {
        "key": "build-essential",
        "label": "Compilers (build-essential)",
        "description": "gcc, make en headers — nodig zodra een pip- of npm-pakket zichzelf moet "
                       "compileren in plaats van een kant-en-klare wheel te vinden.",
        "check_cmd": "command -v gcc >/dev/null 2>&1 && command -v make >/dev/null 2>&1",
        "install_script": (
            "set -e\n"
            "apt-get update -qq\n"
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential"
        ),
        "requires": [],
        "timeout_s": 900,
        "sort_order": 70,
    },
]


def builtin_for(key: str) -> Optional[Dict[str, Any]]:
    return next((e for e in BUILTIN_EXTRAS if e["key"] == key), None)


def seed_builtin_extras(db) -> int:
    """Ontbrekende ingebouwde pakketten aanmaken. Bestaande rijen blijven zoals
    ze zijn — een aangepast script van de gebruiker mag niet bij elke start van
    LabX teruggedraaid worden."""
    from models.lab_extra import LabExtra

    now = datetime.now(timezone.utc).isoformat()
    existing = {k for (k,) in db.query(LabExtra.key).all()}
    added = 0
    for spec in BUILTIN_EXTRAS:
        if spec["key"] in existing:
            continue
        db.add(LabExtra(
            key=spec["key"], label=spec["label"], description=spec.get("description"),
            check_cmd=spec.get("check_cmd"), install_script=spec["install_script"],
            requires=list(spec.get("requires") or []),
            timeout_s=int(spec.get("timeout_s") or 900),
            default_on=bool(spec.get("default_on", False)),
            is_enabled=True, builtin=True,
            sort_order=int(spec.get("sort_order") or 100),
            created_at=now, updated_at=now,
        ))
        added += 1
    if added:
        db.commit()
        log.infox("Lab-extra's toegevoegd", count=added)
    return added
