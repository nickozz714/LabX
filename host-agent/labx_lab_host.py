#!/usr/bin/env python3
"""labx_lab_host.py — deze computer aanbieden als lab-host.

Je zet hem aan op een pc die toch draait, en die pc kan dan labs van LabX
hosten. Eén server die alles doet krijgt het op zijn staart; een machine
erbij verdubbelt je capaciteit zonder dat je iets koopt.

WAT DIT PROGRAMMA WÉL DOET
--------------------------
1. **Zich aanmelden.** Docker broadcast niets — er is geen mDNS, geen
   service-advertentie, niets. Zonder dit zou je IP-adressen moeten overtypen
   en zelf bijhouden welke pc aan staat. Hij meldt zich daarom zelf bij LabX,
   en blijft dat elke 30 seconden doen. Dat is ook meteen het "ik leef nog".

   De melding gaat van de PC naar LabX en niet andersom, omdat de backend van
   LabX in een container draait en het LAN niet ziet: multicast komt de bridge
   niet over.

2. **De machine wakker houden** zolang er labs draaien. Een pc die na tien
   minuten in slaap valt, is geen lab-host — dat merk je dan aan een nachtrun
   die halverwege stopt, en dat is het vervelendste moment om het te merken.

3. **Melden hoe het ermee staat**: kernen, geheugen, vrije schijf, load,
   Docker-versie. Zodat LabX weet waar nog iets bij kan, en jij ziet waarom
   ergens niets bij kan.

WAT DIT PROGRAMMA NIET DOET, EN WAAROM
--------------------------------------
Commando's uitvoeren. Niet één.

Het aansturen van containers gaat volledig buiten deze agent om, via
`DOCKER_HOST=ssh://jij@dezepc` — de docker-CLI op de LabX-server praat dan
rechtstreeks met de Docker hier, over SSH. Geen open poort, geen eigen
protocol, en de authenticatie is je SSH-sleutel.

Dat is met opzet zo gesplitst: wie deze agent overneemt, kan niets starten.
De gevaarlijke kant zit achter een sleutel die jij beheert, niet achter een
dienstje dat op je netwerk staat te luisteren.

STARTEN
-------
    python3 labx_lab_host.py --labx http://192.168.2.15:8080

De eerste keer toont hij een koppelcode. Die typ je in LabX over bij
Labs → Machines, samen met het SSH-adres van deze pc. Tot dat gebeurd is
krijgt deze machine geen enkel lab.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

HARTSLAG_SECONDEN = 30
AGENT_POORT = 8098

#: Blijft gelijk tussen herstarts, zodat LabX deze machine herkent als hij een
#: ander IP krijgt. Een laptop op wifi is anders elke dag een nieuwe host.
ID_BESTAND = Path.home() / ".labx-lab-host-id"


def machine_id() -> str:
    try:
        if ID_BESTAND.exists():
            bestaand = ID_BESTAND.read_text().strip()
            if bestaand:
                return bestaand
    except OSError:
        pass
    verse = uuid.uuid4().hex
    try:
        ID_BESTAND.write_text(verse)
        ID_BESTAND.chmod(0o600)
    except OSError:
        pass          # dan maar een nieuwe per start; beter dan niet draaien
    return verse


# ── wat deze machine te bieden heeft ────────────────────────────────────────

def _docker_versie() -> Optional[str]:
    if not shutil.which("docker"):
        return None
    try:
        uit = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                             capture_output=True, text=True, timeout=10)
        return uit.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _draaiende_labs() -> int:
    """Hoeveel labx-containers hier draaien. Dit is wat "er is werk" betekent
    voor het wakker houden."""
    if not shutil.which("docker"):
        return 0
    try:
        uit = subprocess.run(
            ["docker", "ps", "--filter", "label=labx.managed", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10)
        return len([r for r in uit.stdout.splitlines() if r.strip()])
    except Exception:  # noqa: BLE001
        return 0


def _geheugen_mb() -> tuple[Optional[int], Optional[int]]:
    """(totaal, vrij) in MB. Per besturingssysteem anders; onbekend mag."""
    try:
        if sys.platform == "darwin":
            totaal = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                        capture_output=True, text=True,
                                        timeout=5).stdout.strip()) // (1024 * 1024)
            # `vm_stat` geeft pagina's; vrij = free + inactive + speculative.
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            paginagrootte = 4096
            vrij_paginas = 0
            for regel in vm.splitlines():
                if regel.startswith("Mach Virtual Memory Statistics"):
                    if "page size of" in regel:
                        paginagrootte = int(regel.split("page size of")[1].split()[0])
                for sleutel in ("Pages free:", "Pages inactive:", "Pages speculative:"):
                    if regel.startswith(sleutel):
                        vrij_paginas += int(regel.split(":")[1].strip().rstrip("."))
            return totaal, (vrij_paginas * paginagrootte) // (1024 * 1024)
        # Linux
        info = {}
        for regel in Path("/proc/meminfo").read_text().splitlines():
            sleutel, _, rest = regel.partition(":")
            info[sleutel] = int(rest.strip().split()[0])
        return info["MemTotal"] // 1024, info.get("MemAvailable", info["MemFree"]) // 1024
    except Exception:  # noqa: BLE001
        return None, None


def capaciteit(wakker: bool) -> Dict[str, Any]:
    totaal, vrij = _geheugen_mb()
    try:
        schijf = shutil.disk_usage(str(Path.home())).free // (1024 ** 3)
    except OSError:
        schijf = None
    try:
        load = ", ".join(f"{x:.2f}" for x in os.getloadavg())
    except (OSError, AttributeError):
        load = None
    return {
        "cpus": os.cpu_count(),
        "geheugen_mb": totaal,
        "vrij_mb": vrij,
        "schijf_vrij_gb": schijf,
        "load": load,
        "docker_versie": _docker_versie(),
        "besturingssysteem": f"{platform.system()} {platform.release()}"[:64],
        "blijft_wakker": wakker,
    }


# ── wakker blijven ──────────────────────────────────────────────────────────

class WakkerHouder:
    """Voorkomt dat de machine in slaap valt zolang er labs draaien.

    Per besturingssysteem anders, en op Windows (nog) niet: daar hoort dit in
    de GUI-schil te zitten met `SetThreadExecutionState`, en een los
    Python-proces is daar het verkeerde gereedschap voor. Liever eerlijk
    melden dat het niet kan dan doen alsof.
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None

    @property
    def ondersteund(self) -> bool:
        if sys.platform == "darwin":
            return bool(shutil.which("caffeinate"))
        if sys.platform.startswith("linux"):
            return bool(shutil.which("systemd-inhibit"))
        return False

    @property
    def actief(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def aan(self) -> None:
        if self.actief or not self.ondersteund:
            return
        if sys.platform == "darwin":
            # -i: geen idle-sleep. -m: schijf niet laten slapen. Niet -d: het
            # scherm mag gewoon uit, dat scheelt stroom en doet er niet toe.
            cmd = ["caffeinate", "-i", "-m"]
        else:
            cmd = ["systemd-inhibit", "--what=idle:sleep",
                   "--why=LabX draait hier labs", "--mode=block", "sleep", "infinity"]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        except Exception as exc:  # noqa: BLE001
            print(f"[labx] wakker houden lukte niet: {exc}", file=sys.stderr)

    def uit(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None


# ── aanmelden ───────────────────────────────────────────────────────────────

def aanmelden(labx: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = labx.rstrip("/") + "/api/lab-hosts/aanmelden"
    data = json.dumps(payload).encode()
    verzoek = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(verzoek, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.URLError as exc:
        print(f"[labx] kan LabX niet bereiken op {labx}: {exc}", file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[labx] aanmelden mislukt: {exc}", file=sys.stderr)
        return None


def main() -> int:
    arg = argparse.ArgumentParser(
        description="Bied deze computer aan als lab-host voor LabX.")
    arg.add_argument("--labx", required=True,
                     help="Het adres van LabX, bijvoorbeeld http://192.168.2.15:8080")
    arg.add_argument("--naam", default=platform.node(),
                     help="Hoe deze machine in LabX heet (standaard: de computernaam)")
    arg.add_argument("--ssh", default=None,
                     help="Het SSH-adres waarop LabX bij Docker kan, bijv. nick@werkpc.local. "
                          "Alleen een suggestie; koppelen doe je in LabX.")
    opties = arg.parse_args()

    # Regel voor regel doorgeven. Zonder dit houdt Python de uitvoer vast tot
    # de buffer vol is -- en dan staat de koppelcode, het enige dat je echt
    # moet zien, een kwartier later pas op je scherm. Of nooit, als je hem via
    # een logbestand bekijkt.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:          # oudere Python
        pass

    mid = machine_id()
    wakker = WakkerHouder()
    if not wakker.ondersteund:
        print("[labx] Let op: slapen voorkomen kan op dit systeem niet automatisch. "
              "Zet standby zelf uit, anders stopt een nachtrun halverwege.",
              file=sys.stderr)

    gestopt = False

    def _stop(*_a: Any) -> None:
        nonlocal gestopt
        gestopt = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print(f"LabX lab-host — {opties.naam}")
    print(f"Meldt zich bij {opties.labx} (elke {HARTSLAG_SECONDEN}s). Stoppen met Ctrl-C.\n")

    laatste_status = None
    while not gestopt:
        labs = _draaiende_labs()
        # Alleen wakker blijven als er ECHT iets draait. Een pc die de hele
        # week niet slaapt omdat er ooit een lab op stond, is een pc die je
        # uitzet.
        if labs:
            wakker.aan()
        else:
            wakker.uit()

        antwoord = aanmelden(opties.labx, {
            "machine_id": mid,
            "naam": opties.naam,
            "agent_poort": AGENT_POORT,
            "docker_host": (f"ssh://{opties.ssh}" if opties.ssh else None),
            "capaciteit": capaciteit(wakker.actief),
        })
        if antwoord and antwoord.get("status") != laatste_status:
            laatste_status = antwoord.get("status")
            if laatste_status == "nieuw" and antwoord.get("koppelcode"):
                print("\n" + "─" * 54)
                print("  Deze computer is bij LabX gemeld maar nog niet gekoppeld.")
                print(f"  Koppelcode:   {antwoord['koppelcode']}")
                print("  Vul die in LabX in bij Labs → Machines,")
                print(f"  samen met het SSH-adres van deze pc"
                      + (f" ({opties.ssh})" if opties.ssh else ""))
                print("─" * 54 + "\n")
            elif laatste_status == "gekoppeld":
                print("[labx] Gekoppeld — deze machine kan nu labs draaien.")
            elif laatste_status == "geweigerd":
                print("[labx] Deze machine is geweigerd in LabX; er komt hier geen werk.")

        for _ in range(HARTSLAG_SECONDEN):
            if gestopt:
                break
            time.sleep(1)

    wakker.uit()
    print("\n[labx] Gestopt. Draaiende labs blijven staan; LabX ziet deze machine "
          "straks als offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
