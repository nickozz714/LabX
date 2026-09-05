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
        "description": "Zet `@playwright/mcp` in het lab neer, zodat de agent browser-TOOLS krijgt "
                       "(browser_navigate, browser_click, ...) in plaats van scripts te moeten "
                       "schrijven. De browser draait dan in de sandbox achter de egress-guard. "
                       "Haalt ook de browserbuild op die deze server zelf nodig heeft. LabX "
                       "registreert de server na installatie, zet hem op de allowlist van dit lab "
                       "en haalt zijn tools op — je hoeft niets meer te koppelen.",
        # De controle vraagt het aan de tool zelf, en dat is met opzet: welke
        # browserbuild deze server nodig heeft hangt af van de Playwright-versie
        # die IN @playwright/mcp zit, niet van de losse `playwright` uit het
        # Node-pakket. Die twee liepen uit elkaar, en dan staat er wel een
        # browser maar niet de zijne — met een foutmelding die naar een pad
        # wijst dat niemand kan raden. `install-browser` is klaar in nul
        # seconden als het goed zit, en haalt hem anders alsnog op.
        "check_cmd": ("command -v playwright-mcp >/dev/null 2>&1 && "
                      "playwright-mcp install-browser chrome-for-testing >/dev/null 2>&1"),
        "install_script": (
            "set -e\n"
            "npm install -g --silent @playwright/mcp@latest\n"
            # De binary heet `playwright-mcp` (niet mcp-server-playwright — die
            # naam is van een oudere release en levert hier een stille 127 op).
            "command -v playwright-mcp\n"
            "playwright-mcp install-browser chrome-for-testing"
        ),
        "requires": ["node", "playwright-node"],
        "timeout_s": 900,
        "sort_order": 50,
        "mcp_server": {
            "slug": "playwright-lab",
            "name": "Playwright (in dit lab)",
            # --user-data-dir op /workspace: dáár landt een ingelogde sessie, en
            # /workspace staat op een eigen volume — dus een cookie overleeft
            # een herstart én een opnieuw opgebouwde container. Het standaardpad
            # zit in de containerlaag en is bij een rebuild weg.
            "command": ("playwright-mcp --headless --browser chromium "
                        "--user-data-dir /workspace/.labx-browser"),
            "description": "Browserautomatisering IN de labcontainer; de browser blijft in de "
                           "sandbox en achter de data-egress-guard.",
            # De host-variant levert dezelfde browser_*-tools vanuit de
            # LabX-container, waar geen browser staat. Zolang die op de
            # allowlist van dit lab staat, wint hij of botst hij — dus haalt de
            # registratie hem hier weg.
            "replaces": ["ms-playwright"],
        },
    },
    {
        "key": "browser-vnc",
        "label": "Zelf inloggen in de browser van het lab",
        "description": "Draait de browser van de agent zichtbaar (Xvfb + noVNC) in plaats van "
                       "onzichtbaar, zodat je hem via LabX kunt openen en er ZELF in kunt inloggen "
                       "— ook bij tweestapsverificatie. Je kijkt naar dezelfde browser als de agent, "
                       "dus na jouw login werkt hij verder in die sessie. Het profiel staat op "
                       "/workspace en blijft dus bewaard.",
        # De controle is "serveert noVNC?" en niet "staat het pakket er?": het
        # X-scherm en de VNC-brug moeten ook na een herstart van het lab weer
        # draaien, en inrichten gebeurt bij elke start. Zo komt de boel vanzelf
        # weer omhoog zonder dat iemand iets hoeft te starten.
        "check_cmd": "curl -sf http://127.0.0.1:6080/vnc.html >/dev/null 2>&1",
        "install_script": (
            "set -e\n"
            "if ! command -v Xvfb >/dev/null 2>&1 || ! command -v x11vnc >/dev/null 2>&1 "
            "|| ! command -v websockify >/dev/null 2>&1; then\n"
            "  apt-get update -qq\n"
            "  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xvfb x11vnc novnc "
            "websockify openbox procps curl fonts-liberation\n"
            "fi\n"
            "pgrep -x Xvfb >/dev/null 2>&1 || "
            "(nohup Xvfb :99 -screen 0 1440x900x24 >/tmp/xvfb.log 2>&1 &)\n"
            "sleep 1\n"
            # Zonder vensterbeheerder krijgen pop-ups (en dat IS een Microsoft-login
            # vaak) geen focus en kun je er niets in typen.
            "pgrep -x openbox >/dev/null 2>&1 || "
            "(DISPLAY=:99 nohup openbox >/tmp/openbox.log 2>&1 &)\n"
            # -localhost: de VNC-poort zelf is van buiten de container onbereikbaar.
            # De enige weg naar binnen loopt via LabX, dus achter jouw login.
            "pgrep -x x11vnc >/dev/null 2>&1 || "
            "(nohup x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 "
            ">/tmp/x11vnc.log 2>&1 &)\n"
            "pgrep -f 'websockify.*6080' >/dev/null 2>&1 || "
            "(nohup websockify --web /usr/share/novnc 6080 localhost:5900 "
            ">/tmp/websockify.log 2>&1 &)\n"
            "sleep 2\n"
            "curl -sf http://127.0.0.1:6080/vnc.html >/dev/null"
        ),
        "requires": ["playwright-mcp"],
        "timeout_s": 1200,
        "sort_order": 55,
        "mcp_server": {
            "slug": "playwright-lab",
            "name": "Playwright (in dit lab, zichtbaar)",
            # Zonder --headless, op het X-scherm van hierboven: dit IS de browser
            # die je in beeld krijgt. Zelfde --user-data-dir als de onzichtbare
            # variant, zodat een login in de een geldt voor de ander.
            "command": ("sh -c 'DISPLAY=:99 exec playwright-mcp --browser chromium "
                        "--user-data-dir /workspace/.labx-browser'"),
            "description": "Browserautomatisering in de labcontainer, zichtbaar via het "
                           "Browser-tabblad van het lab zodat je zelf kunt inloggen.",
            "replaces": ["ms-playwright"],
        },
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


# De velden die het meegeleverde origineel bepaalt. Alles daarbuiten (aan/uit,
# standaard aangevinkt) is een keuze van de gebruiker en telt niet mee.
_MANAGED = ("label", "description", "check_cmd", "install_script", "requires",
            "timeout_s", "sort_order", "mcp_server")


def _fingerprint(values: Dict[str, Any]) -> str:
    import hashlib
    import json
    payload = json.dumps({k: values.get(k) for k in _MANAGED}, sort_keys=True,
                         ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _spec_fingerprint(spec: Dict[str, Any]) -> str:
    return _fingerprint({
        "label": spec["label"], "description": spec.get("description"),
        "check_cmd": spec.get("check_cmd"), "install_script": spec["install_script"],
        "requires": list(spec.get("requires") or []),
        "timeout_s": int(spec.get("timeout_s") or 900),
        "sort_order": int(spec.get("sort_order") or 100),
        "mcp_server": spec.get("mcp_server"),
    })


def _row_fingerprint(row) -> str:
    return _fingerprint({
        "label": row.label, "description": row.description, "check_cmd": row.check_cmd,
        "install_script": row.install_script, "requires": list(row.requires or []),
        "timeout_s": int(row.timeout_s or 900), "sort_order": int(row.sort_order or 100),
        "mcp_server": row.mcp_server,
    })


def _apply_spec(row, spec: Dict[str, Any], now: str) -> None:
    row.label = spec["label"]
    row.description = spec.get("description")
    row.check_cmd = spec.get("check_cmd")
    row.install_script = spec["install_script"]
    row.requires = list(spec.get("requires") or [])
    row.timeout_s = int(spec.get("timeout_s") or 900)
    row.sort_order = int(spec.get("sort_order") or 100)
    row.mcp_server = spec.get("mcp_server")
    row.builtin_hash = _spec_fingerprint(spec)
    row.updated_at = now


def seed_builtin_extras(db) -> Dict[str, int]:
    """Ontbrekende ingebouwde pakketten aanmaken, en een ONGEWIJZIGD pakket
    bijwerken als het meegeleverde origineel veranderd is.

    Dat tweede is niet luxe: de eerste versie van het Playwright-MCP-pakket had
    een verkeerde binarynaam en bracht nog geen serverkoppeling mee. Zonder deze
    stap zou geen enkele bestaande installatie die verbetering ooit zien —
    alleen wie LabX voor het eerst opzette had een werkend pakket, en dat is de
    ergste soort verschil om te moeten debuggen.

    Een rij die de gebruiker zelf heeft aangepast blijft met rust: dat blijkt
    uit een vingerafdruk die niet meer overeenkomt met het origineel waarmee de
    rij is gezet."""
    from models.lab_extra import LabExtra

    now = datetime.now(timezone.utc).isoformat()
    rows = {r.key: r for r in db.query(LabExtra).all()}
    added = updated = kept = 0
    for spec in BUILTIN_EXTRAS:
        row = rows.get(spec["key"])
        if row is None:
            row = LabExtra(
                key=spec["key"], default_on=bool(spec.get("default_on", False)),
                is_enabled=True, builtin=True, created_at=now, updated_at=now,
                install_script=spec["install_script"], label=spec["label"])
            _apply_spec(row, spec, now)
            db.add(row)
            added += 1
            continue
        if not row.builtin:
            continue
        spec_fp = _spec_fingerprint(spec)
        if _row_fingerprint(row) == spec_fp:
            continue  # al gelijk aan het origineel
        # builtin_hash leeg = gezet door een versie van vóór deze vingerafdruk;
        # dan is er geen bewijs van een eigen aanpassing en wint het origineel.
        if row.builtin_hash and row.builtin_hash != _row_fingerprint(row):
            kept += 1
            log.infox("Lab-extra aangepast door de gebruiker — origineel niet doorgevoerd",
                      pakket=row.key)
            continue
        _apply_spec(row, spec, now)
        updated += 1
    if added or updated:
        db.commit()
        log.infox("Lab-extra's bijgewerkt", toegevoegd=added, vernieuwd=updated,
                  eigen_aanpassing_behouden=kept)
    return {"added": added, "updated": updated, "kept": kept}
