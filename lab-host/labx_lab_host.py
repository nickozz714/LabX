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
import socket
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


def eigen_ip(labx: str) -> Optional[str]:
    """Het IP waarop LabX deze machine kan terugvinden.

    Niet "mijn IP-adres" in het algemeen -- een laptop heeft er makkelijk drie
    (wifi, ethernet, een VPN) en de vraag is welke LabX moet gebruiken. Dat
    beantwoordt het besturingssysteem zelf: open een socket richting LabX en
    kijk welk adres hij aan onze kant koos. Dat is per definitie het adres
    waarover het verkeer loopt.

    Waarom dit nodig is: LabX kon ook kijken van welk adres het verzoek kwam,
    maar achter een reverse proxy ziet hij dan de proxy. Dat ging mis bij de
    eerste koppeling op de server -- hij stelde `ssh://nick@172.29.0.4` voor,
    het IP van de nginx-container, en inloggen daarop geeft "connection
    refused" op een adres dat je nergens kunt thuisbrengen.
    """
    from urllib.parse import urlparse

    u = urlparse(labx)
    host = u.hostname
    if not host:
        return None
    poort = u.port or (443 if u.scheme == "https" else 80)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP "verbinden" stuurt niets; het kiest alleen een route en daarmee
        # een bronadres. Geen verkeer, geen wachttijd, werkt ook als LabX even
        # niet luistert.
        s.connect((host, poort))
        return str(s.getsockname()[0])
    except OSError:
        return None
    finally:
        s.close()


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

#: Waar Docker Desktop zijn CLI neerzet. Staat niet in het PATH van een
#: SSH-sessie, en juist dat PATH is het PATH dat LabX gebruikt.
_DOCKER_PADEN = (
    "/usr/local/bin/docker",                                    # macOS + Linux
    str(Path.home() / ".docker" / "bin" / "docker"),            # nieuwere Docker Desktop
    "/opt/homebrew/bin/docker",                                 # Apple Silicon + brew
)


def docker_pad() -> Optional[str]:
    """Het docker-commando, ook als het niet in dit PATH staat."""
    gevonden = shutil.which("docker")
    if gevonden:
        return gevonden
    for kandidaat in _DOCKER_PADEN:
        if os.path.isfile(kandidaat) and os.access(kandidaat, os.X_OK):
            return kandidaat
    return None


def zorg_voor_ssh_pad() -> Optional[str]:
    """Docker bereikbaar maken voor een SSH-sessie, en melden wat er gebeurde.

    Dit is de val waar de eerste echte lab-host op stuk liep. LabX stuurt
    containers aan met `ssh ... docker system dial-stdio`, en een SSH-sessie
    op macOS krijgt een kaal PATH: /usr/bin:/bin:/usr/sbin:/sbin. Docker
    Desktop staat in /usr/local/bin en zit daar dus NIET in. Het gevolg is
    `command not found`, en LabX meldt dan iets over docker terwijl het
    probleem het PATH is -- een melding die je de verkeerde kant op stuurt.

    Daarom regelt de agent dit zelf, op de enige plek die een niet-interactieve
    zsh leest: ~/.zshenv. Eén blok, herkenbaar, en alleen als het er nog niet
    staat -- je eigen instellingen blijven.
    """
    pad = docker_pad()
    if not pad:
        return None
    map_ = os.path.dirname(pad)
    # NIET overslaan omdat docker al in ONS PATH staat. Dat was de fout: deze
    # agent draait in een gewone terminal, waar /usr/local/bin er gewoon in
    # zit, en dan leek er niets te doen. Maar het PATH dat telt is dat van een
    # NIET-interactieve SSH-sessie, en dat is altijd de kale versie. Het
    # gevolg was dat de reparatie juist overgeslagen werd op precies de
    # machines die hem nodig hadden -- "docker: command not found" bij het
    # testen van de verbinding, terwijl docker er gewoon draaide.
    if platform.system() != "Darwin":
        # Op Linux komt docker uit een pakket en staat hij in het normale PATH;
        # daar zonder aanleiding in iemands shellbestanden schrijven is erger
        # dan het probleem.
        return None

    zshenv = Path.home() / ".zshenv"
    merk = "# >>> LabX lab-host >>>"
    huidig = zshenv.read_text(encoding="utf-8") if zshenv.exists() else ""
    if merk in huidig:
        return None
    blok = (f"\n{merk}\n"
            "# Een SSH-sessie krijgt op macOS een kaal PATH, waar de docker-CLI van\n"
            "# Docker Desktop niet in staat. LabX stuurt containers aan via\n"
            '# "ssh ... docker system dial-stdio" en struikelt daar zonder deze regel\n'
            "# over, met een melding die naar docker wijst in plaats van naar het PATH.\n"
            f'export PATH="{map_}:$PATH"\n'
            "# <<< LabX lab-host <<<\n")
    try:
        with zshenv.open("a", encoding="utf-8") as f:
            f.write(blok)
    except OSError as exc:
        print(f"[labx] kon {zshenv} niet aanvullen ({exc}); zet zelf {map_} in het "
              f"PATH van een SSH-sessie, anders kan LabX hier geen containers starten.",
              file=sys.stderr)
        return None
    return str(zshenv)


def _docker_versie() -> Optional[str]:
    pad = docker_pad()
    if not pad:
        return None
    try:
        uit = subprocess.run([pad, "version", "--format", "{{.Server.Version}}"],
                             capture_output=True, text=True, timeout=10)
        return uit.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _draaiende_labs() -> int:
    """Hoeveel LabX-containers hier draaien — labs én modelcontainers.

    Dit is wat "er is werk" betekent voor het wakker houden, en de
    modelcontainers hoorden daar vanaf het begin bij. Dat ze er niet in zaten
    kwam meteen boven: een iMac met drie modelrunners en geen enkel lab viel
    in slaap, waarna LabX die runners als "nog niet bereikbaar" zag staan.
    Een machine die modellen draait is net zo goed aan het werk als een
    machine die labs draait.
    """
    pad = docker_pad()
    if not pad:
        return 0
    aantal = 0
    for label in ("labx.managed", "labx.model"):
        try:
            uit = subprocess.run(
                [pad, "ps", "--filter", f"label={label}", "--format", "{{.ID}}"],
                capture_output=True, text=True, timeout=10)
            aantal += len([r for r in uit.stdout.splitlines() if r.strip()])
        except Exception:  # noqa: BLE001
            pass
    return aantal


def _geheugen_mb() -> tuple[Optional[int], Optional[int]]:
    """(totaal, vrij) in MB. Per besturingssysteem anders; onbekend mag."""
    try:
        if sys.platform == "darwin":
            totaal = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                        capture_output=True, text=True,
                                        timeout=5).stdout.strip()) // (1024 * 1024)
            # EERST macOS zelf vragen. `memory_pressure` drukt één getal af dat
            # het systeem zelf hanteert, en dat is beter dan onze eigen
            # optelsom -- die mist de gecomprimeerde laag. Op een MacBook met
            # 24 GB telde die som uit op 5,5 GB "vrij" terwijl macOS zelf 44%
            # (ruim 10 GB) zei. Een lab-host die zichzelf veel voller meldt
            # dan hij is, krijgt werk dat hij prima aankon.
            try:
                mp = subprocess.run(["memory_pressure"], capture_output=True,
                                    text=True, timeout=5).stdout
                for regel in mp.splitlines():
                    if "free percentage" in regel:
                        pct = int(regel.split(":")[1].strip().rstrip("%"))
                        return totaal, totaal * pct // 100
            except Exception:  # noqa: BLE001 — dan de optelsom hieronder
                pass

            # Terugval: `vm_stat` geeft pagina's; vrij = free + inactive +
            # speculative. Een ondergrens, want gecomprimeerd geheugen dat weer
            # vrij kan komen telt hier niet mee.
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

    HET SLOT HANGT AAN ONZE EIGEN PID, en dat is de hele truc. Eerder liep
    `caffeinate` als los kind: ging de agent netjes uit, dan ruimden we hem
    op, maar werd de agent HARD afgebroken (`pkill`, `launchctl bootout`, een
    crash) dan bleef dat kind draaien -- en hield de machine wakker tot
    iemand het toevallig zag. Op de eerste lab-host stond er zo eentje
    negentien uur lang een slapende iMac wakker te houden, zonder dat er nog
    één lab draaide.

    Daarom `caffeinate -w <onze pid>`: caffeinate wacht op ons en stopt
    vanzelf zodra wij weg zijn, hoe we ook weggaan. Op Linux hetzelfde idee
    met een lus die kijkt of wij er nog zijn.
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
        mijn_pid = os.getpid()
        if sys.platform == "darwin":
            # -i: geen idle-sleep. -m: schijf niet laten slapen. Niet -d: het
            # scherm mag gewoon uit, dat scheelt stroom en doet er niet toe.
            # -w: stop zodra WIJ weg zijn -- zie de uitleg bij deze klasse.
            cmd = ["caffeinate", "-i", "-m", "-w", str(mijn_pid)]
        else:
            # systemd-inhibit houdt het slot zolang zijn commando draait; dat
            # commando kijkt dus of wij er nog zijn in plaats van eeuwig te
            # slapen.
            cmd = ["systemd-inhibit", "--what=idle:sleep",
                   "--why=LabX draait hier labs", "--mode=block",
                   "sh", "-c", f"while kill -0 {mijn_pid} 2>/dev/null; do sleep 5; done"]
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

    if not docker_pad():
        print("[labx] Let op: ik vind hier geen docker. Zolang die er niet is kan LabX "
              "op deze machine geen labs draaien. Start Docker Desktop en begin opnieuw.",
              file=sys.stderr)
    else:
        aangevuld = zorg_voor_ssh_pad()
        if aangevuld:
            print(f"[labx] {aangevuld} aangevuld: docker staat nu ook in het PATH van een "
                  f"SSH-sessie. Zonder dat kan LabX hier geen containers starten.")

    laatste_status = None
    #: Hoeveel keer de melding achter elkaar misging. Bestaat omdat een agent
    #: die blijft proberen en niets meer zegt, er voor jou uitziet als een
    #: agent die het doet -- precies hoe de eerste macOS-host een half uur
    #: "draaide" zonder ooit aan te komen.
    mislukt = 0
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
            # Zie eigen_ip(): LabX kan het zelf niet betrouwbaar zien zodra er
            # een reverse proxy tussen zit.
            "agent_ip": eigen_ip(opties.labx),
            "docker_host": (f"ssh://{opties.ssh}" if opties.ssh else None),
            "capaciteit": capaciteit(wakker.actief),
        })
        if antwoord is None:
            mislukt += 1
            if mislukt in (1, 5, 20) or mislukt % 60 == 0:
                print(f"[labx] {mislukt}e poging op rij mislukt. LabX ziet deze machine "
                      f"dus NIET. Op macOS: geef dit programma toegang tot het lokale "
                      f"netwerk (Systeeminstellingen → Privacy en beveiliging → Lokaal "
                      f"netwerk) — zonder die toestemming kan een achtergrondproces geen "
                      f"adres in je eigen netwerk bereiken.", file=sys.stderr)
        elif antwoord is not None and mislukt:
            print(f"[labx] Weer verbinding met LabX na {mislukt} mislukte poging(en).")
            mislukt = 0

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
