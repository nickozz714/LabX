#!/usr/bin/env python3
"""labx_host_agent.py — LabX laten werken op DEZE machine, binnen één map.

Waarom dit bestaat
------------------
Een gewoon lab is een container: afgeschermd, wegwerpbaar, met een eigen
Azure-profiel dat je er expliciet in zet. Dat werkt — behalve als de klant eist
dat je aanmeldt vanaf een apparaat dat zij kennen. Een container is geen
apparaat; die claim kan hij niet afgeven en dat hoort hij ook niet te kunnen.

Dit proces is het antwoord: de agent voert zijn commando's uit op JOUW machine,
als jou, met de `az`-sessie die jij in een browser hebt opgezet. Er wordt niets
gekopieerd en niets voorgewend. Precies zoals je het zelf zou typen.

De backend van LabX draait in een container en kan daar niet uit. Vandaar deze
brug: een klein proces dat jij start, dat alleen op localhost luistert en alleen
praat met wie het gedeelde geheim kent.

Wat het NIET is
---------------
Geen shell-op-afstand. Twee grenzen liggen vast en zijn niet door de aanroeper
te verzetten:

1. **De map.** Elk commando draait onder `sandbox-exec` met een profiel dat de
   hele thuismap dichtzet en alleen de projectmap opent. Dat is een grens in de
   kernel, geen filter op commandotekst: een symlink naar buiten, een pad in een
   variabele of een subshell komen er net zo goed niet door. Nagemeten, niet
   aangenomen.

2. **De Azure-sessie.** Elk commando krijgt `AZURE_CONFIG_DIR` naar een map ín
   het project. De agent ziet daardoor alleen de aanmeldingen die jij voor dát
   project hebt gedaan — niet die van je andere klanten. De zandbak sluit
   `~/.azure` sowieso af.

Starten
-------
    export LABX_HOST_AGENT_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
    python3 labx_host_agent.py

Zet dat token ook in LabX bij de instellingen van het hostlab. Zonder token
start de agent niet: een onbeveiligde uitvoerdienst op je eigen machine is
precies het soort gemak waar je spijt van krijgt.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Tuple

POORT = int(os.environ.get("LABX_HOST_AGENT_PORT", "8099"))
TOKEN = os.environ.get("LABX_HOST_AGENT_TOKEN", "").strip()
MAX_UITVOER = 60_000

#: Alleen op de loopback. Een hostagent die op het netwerk luistert is een
#: achterdeur, hoe goed het token ook is.
ADRES = "127.0.0.1"


def zandbak_profiel(project: Path) -> str:
    """Het sandbox-exec-profiel voor één projectmap.

    Opgebouwd als "alles mag, behalve je thuismap, behalve dit project" en niet
    als "alles dicht, op een paar gaten na". Dat laatste klinkt veiliger maar
    werkt niet: een dynamisch gelinkt programma heeft zoveel nodig voordat het
    ook maar begint dat je het gat-voor-gat weer openzet, en dan weet niemand
    meer wat er precies dicht zit. Zo is het te overzien: de thuismap is het
    enige dat beschermd hoeft te worden, want daar staan de sleutels, de
    sessies van andere klanten en alles wat privé is.

    Latere regels winnen, dus de projectmap komt NA het dichtzetten.
    """
    thuis = str(Path.home())
    return (
        "(version 1)\n"
        "(allow default)\n"
        f'(deny file-read* file-write* (subpath {json.dumps(thuis)}))\n'
        f'(allow file-read* file-write* (subpath {json.dumps(str(project))}))\n'
    )


def _antwoord(handler: BaseHTTPRequestHandler, code: int, body: Dict[str, Any]) -> None:
    rauw = json.dumps(body).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(rauw)))
    handler.end_headers()
    handler.wfile.write(rauw)


def voer_uit(project: Path, commando: str, *, timeout: float = 120.0,
             extra_env: Dict[str, str] | None = None) -> Tuple[int, str, bool]:
    """Eén commando, in de zandbak, met de werkmap op het project.

    Geeft (exit_code, uitvoer, afgekapt). stdout en stderr gaan samen, net als
    bij een lab: wie een commando laat mislukken wil de foutmelding zien zonder
    erom te hoeven vragen.
    """
    project.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # Een eigen Azure-sessiemap per project. Dit is de reden dat een hostlab
    # niet meteen bij al je klanten kan: wat hier niet is aangemeld, bestaat
    # voor dit lab niet.
    env["AZURE_CONFIG_DIR"] = str(project / ".azure")
    env["LABX_PROJECT_DIR"] = str(project)
    env.pop("LABX_HOST_AGENT_TOKEN", None)   # nooit doorgeven aan het commando
    env.update(extra_env or {})

    with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as f:
        f.write(zandbak_profiel(project))
        profiel = f.name
    try:
        proc = subprocess.run(
            ["sandbox-exec", "-f", profiel, "/bin/sh", "-c", commando],
            cwd=str(project), env=env, capture_output=True, text=True,
            timeout=timeout)
        uit = proc.stdout
        if proc.stderr:
            uit = (uit + "\n" if uit else "") + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return 124, f"[afgebroken na {int(timeout)} seconden]", False
    except FileNotFoundError as exc:
        return 127, f"[kon sandbox-exec niet starten: {exc}]", False
    finally:
        try:
            os.unlink(profiel)
        except OSError:
            pass

    afgekapt = len(uit) > MAX_UITVOER
    if afgekapt:
        uit = uit[:MAX_UITVOER] + "\n… [uitvoer afgekapt]"
    return code, uit, afgekapt


class Handler(BaseHTTPRequestHandler):
    server_version = "LabXHostAgent/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Eén regel per verzoek, zonder de commando-inhoud: die kan klantdata
        # bevatten en hoort niet in een terminal-log te blijven staan.
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _geautoriseerd(self) -> bool:
        kop = self.headers.get("Authorization", "")
        return kop.startswith("Bearer ") and kop[7:].strip() == TOKEN

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            # Zonder token: alleen "ik leef". Zo kan LabX zien of de agent
            # draait zonder het geheim te hoeven kennen.
            return _antwoord(self, 200, {"ok": True, "agent": "labx-host"})
        _antwoord(self, 404, {"detail": "Onbekend pad"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._geautoriseerd():
            return _antwoord(self, 401, {"detail": "Ongeldig of ontbrekend token"})
        lengte = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(lengte) or b"{}")
        except json.JSONDecodeError:
            return _antwoord(self, 400, {"detail": "Geen geldige JSON"})

        if self.path != "/exec":
            return _antwoord(self, 404, {"detail": "Onbekend pad"})

        project = (body.get("project") or "").strip()
        commando = body.get("command") or ""
        if not project or not commando:
            return _antwoord(self, 400, {"detail": "project en command zijn verplicht"})

        pad = Path(project).expanduser().resolve()
        thuis = Path.home().resolve()
        # De projectmap moet ÍN je thuismap liggen. Niet uit netheid: het
        # zandbakprofiel beschermt de thuismap en opent daarbinnen één map.
        # Ligt het project ergens anders, dan beschermt het profiel niets en
        # zou de grens een illusie zijn.
        if thuis not in pad.parents and pad != thuis:
            return _antwoord(self, 400, {
                "detail": (f"De projectmap moet binnen {thuis} liggen; "
                           f"anders sluit de zandbak niets af.")})
        if pad == thuis:
            return _antwoord(self, 400, {
                "detail": "De projectmap mag niet je hele thuismap zijn."})

        code, uit, afgekapt = voer_uit(
            pad, commando,
            timeout=float(body.get("timeout") or 120.0),
            extra_env=body.get("env") or {})
        _antwoord(self, 200, {"exit_code": code, "output": uit, "truncated": afgekapt})


def main() -> int:
    if not TOKEN:
        print("LABX_HOST_AGENT_TOKEN is niet gezet — de agent start niet.\n"
              "Maak er een met:\n"
              "  export LABX_HOST_AGENT_TOKEN=\"$(python3 -c 'import secrets;"
              "print(secrets.token_urlsafe(32))')\"", file=sys.stderr)
        return 2
    if sys.platform != "darwin":
        print("Deze agent gebruikt sandbox-exec en werkt alleen op macOS.", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer((ADRES, POORT), Handler)
    print(f"LabX-hostagent luistert op http://{ADRES}:{POORT} — alleen localhost.")
    print("Stoppen met Ctrl-C.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nGestopt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
