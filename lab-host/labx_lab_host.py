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

4. **Een labpoort bereikbaar maken, als LabX erom vraagt.** Labs publiceren
   hun poorten op de loopback van deze machine, met opzet. Draait je browser
   op een ándere computer dan het lab, dan is "openen" daarmee onmogelijk
   geworden: de knop wijst naar localhost en daar staat niets. Op verzoek van
   een gekoppelde LabX zet deze agent zo'n poort door naar je netwerk. Zie de
   vier grendels bij `Doorgangen` verderop.

WAT DIT PROGRAMMA NIET DOET, EN WAAROM
--------------------------------------
Commando's uitvoeren. Niet één.

Het aansturen van containers gaat volledig buiten deze agent om, via
`DOCKER_HOST=ssh://jij@dezepc` — de docker-CLI op de LabX-server praat dan
rechtstreeks met de Docker hier, over SSH. Geen eigen protocol, en de
authenticatie is je SSH-sleutel.

Dat is met opzet zo gesplitst: wie deze agent overneemt, kan niets starten.
De gevaarlijke kant zit achter een sleutel die jij beheert.

Wat hij sinds het doorzetten van labpoorten wél heeft, is een ingang: een
HTTP-poort die pas opengaat zodra LabX een token heeft gegeven, en dat geeft
LabX pas nadat jij de koppelcode hebt bevestigd. Wie die ingang overneemt kan
precies één ding: een poort uit LabX' eigen bereik (18000-18999) van deze
machine op je netwerk zetten. Geen commando's, geen shell, geen Docker.

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
import threading
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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



# ── een labpoort bereikbaar maken ───────────────────────────────────────────
#
# Waarom dit erbij hoort, terwijl hierboven staat dat deze agent geen open
# poort heeft: labs publiceren hun poorten op de LOOPBACK van deze machine.
# Dat is opzet -- een labpoort hoort niet ongevraagd op je netwerk. Maar zodra
# LabX op een andere computer draait dan je browser, is "openen" daarmee
# onmogelijk geworden: de knop wijst naar localhost en daar staat niets.
#
# Dit is de kleinst mogelijke uitweg. Geen commando's, geen shell, geen
# Docker: één TCP-doorgeefluik van een poort op dit netwerk naar een
# loopbackpoort hier. En wel met vier grendels:
#
#   1. Alleen na een verzoek met het token dat LabX pas uitdeelt nadat JIJ de
#      koppelcode hebt bevestigd. Zonder koppeling bestaat de ingang niet.
#   2. Alleen naar poorten in het bereik dat LabX voor labs uitdeelt. Een
#      doorgeefluik naar je ssh of je printer komt er niet doorheen.
#   3. Alleen zolang hij gebruikt wordt. Blijft het stil, dan gaat hij dicht;
#      een doorgang die blijft bestaan wijst op een dag naar een lab dat er
#      niet meer is.
#   4. Weg bij het afsluiten van de agent, want dan is er ook geen lab meer
#      om naartoe te wijzen.

#: Het bereik waarin LabX machinepoorten voor labs en vaste diensten uitdeelt.
#: Alles daarbuiten weigeren we: wat hier binnenkomt is een poortnummer, en een
#: poortnummer zonder grens is een doorgang naar alles wat op deze machine
#: luistert.
LABPOORT_MIN, LABPOORT_MAX = 18000, 18999

#: Waar het doorgeefluik zelf op komt te staan. Hetzelfde nummer plus tienduizend,
#: zodat je aan het adres ziet welk lab je voor je hebt en twee doorgangen elkaar
#: niet verdringen.
POORT_VERSCHUIVING = 10_000

#: Hoe lang een doorgang stil mag zijn voordat we gaan KIJKEN of hij nog
#: ergens naartoe wijst. Niet hoe lang hij open blijft -- dat was het eerst,
#: en dat is de verkeerde maat.
#:
#: Gemeten op 23-10-2026: een tunnelscript stond 46 uur open naar de API van
#: een lab. Die API werd een halve dag niet gebruikt, de doorgang ging na vier
#: uur dicht, en toen hij nodig was kwam er "localhost didn't send any data".
#: De API draaide gewoon; alleen de weg ernaartoe was opgeruimd.
#:
#: Wat ertoe doet is niet of er verkeer was, maar of er aan de andere kant nog
#: iets luistert. Draait het lab, dan hoort de doorgang te blijven; is de
#: poort dood, dan wijst hij nergens meer heen en mag hij weg -- en dat is ook
#: meteen het geval waarvoor het opruimen bedoeld was: LabX hergebruikt
#: poortnummers, en een doorgang naar een verdwenen lab mag geen nieuw lab
#: gaan aanwijzen.
STIL_VOOR_CONTROLE = 15 * 60

#: Hoe lang we wachten tot het lab de verbinding aanneemt. ALLEEN daarvoor:
#: zie _verbind() waarom die time-out daarna weer af moet.
VERBIND_TIJD = 10


def _luistert(poort: int) -> bool:
    """Is er aan de andere kant nog iets? Eén tel is genoeg: het is localhost,
    en een lab dat er is antwoordt meteen."""
    try:
        with socket.create_connection(("127.0.0.1", int(poort)), timeout=1):
            return True
    except OSError:
        return False


class Doorgeefluik:
    """Eén poort op dit netwerk, doorgezet naar een loopbackpoort hier."""

    def __init__(self, doel: int) -> None:
        self.doel = int(doel)
        self.poort = self.doel + POORT_VERSCHUIVING
        self.laatst_gebruikt = time.time()
        self._stop = threading.Event()
        self._sok = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sok.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sok.bind(("0.0.0.0", self.poort))
        self._sok.listen(64)
        self._sok.settimeout(1.0)
        threading.Thread(target=self._luister, daemon=True).start()

    def _luister(self) -> None:
        while not self._stop.is_set():
            try:
                klant, _ = self._sok.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.laatst_gebruikt = time.time()
            threading.Thread(target=self._verbind, args=(klant,), daemon=True).start()
        try:
            self._sok.close()
        except OSError:
            pass

    def _verbind(self, klant: socket.socket) -> None:
        try:
            lab = socket.create_connection(("127.0.0.1", self.doel),
                                           timeout=VERBIND_TIJD)
        except OSError:
            klant.close()
            return
        # De time-out hoort bij het VERBINDEN en niet bij het gesprek daarna.
        # In Python blijft hij anders op de socket staan, erft elke recv hem,
        # en gaat een verbinding die tien seconden stil is eruit. Een
        # HMR-websocket is precies zo'n stille verbinding: die werd elke tien
        # seconden afgekapt, waarna de dev-server-client opnieuw verbond en de
        # pagina herlaadde. Dat zag eruit als een front-end die blijft
        # herstarten -- en alleen via de doorgang, want rechtstreeks op die
        # machine zit dit ertussenuit.
        lab.settimeout(None)
        klant.settimeout(None)
        for van, naar in ((klant, lab), (lab, klant)):
            threading.Thread(target=self._giet, args=(van, naar), daemon=True).start()

    def _giet(self, van: socket.socket, naar: socket.socket) -> None:
        try:
            while True:
                brok = van.recv(65536)
                if not brok:
                    break
                naar.sendall(brok)
                self.laatst_gebruikt = time.time()
        except OSError:
            pass
        finally:
            # Eén kant dicht is de hele verbinding dicht. Een half open socket
            # laat een browser eindeloos wachten op iets dat niet meer komt.
            for s in (van, naar):
                try:
                    s.close()
                except OSError:
                    pass

    def sluit(self) -> None:
        self._stop.set()
        try:
            self._sok.close()
        except OSError:
            pass


class Doorgangen:
    """Welke poorten er open staan, en hoe ze weer dichtgaan."""

    def __init__(self) -> None:
        self._open: Dict[int, Doorgeefluik] = {}
        self._slot = threading.Lock()

    def open(self, doel: int) -> Doorgeefluik:
        if not LABPOORT_MIN <= doel <= LABPOORT_MAX:
            raise ValueError(
                f"Poort {doel} valt buiten het bereik dat LabX voor labs uitdeelt "
                f"({LABPOORT_MIN}-{LABPOORT_MAX}).")
        with self._slot:
            bestaand = self._open.get(doel)
            if bestaand is not None:
                bestaand.laatst_gebruikt = time.time()
                return bestaand
            luik = Doorgeefluik(doel)
            self._open[doel] = luik
            print(f"[labx] poort {luik.poort} staat nu door naar het lab op {doel}")
        self.onthoud()
        return luik

    def sluit(self, doel: int) -> bool:
        with self._slot:
            luik = self._open.pop(int(doel), None)
        if luik is None:
            return False
        luik.sluit()
        print(f"[labx] poort {luik.poort} weer dicht")
        self.onthoud()
        return True

    def lijst(self) -> list:
        with self._slot:
            return [{"lab_poort": q.doel, "poort": q.poort,
                     "stil_seconden": int(time.time() - q.laatst_gebruikt)}
                    for q in self._open.values()]

    def ruim_op(self) -> None:
        """Een doorgang die nergens meer heen wijst, gaat dicht.

        Stilte alleen is geen reden: zie STIL_VOOR_CONTROLE. We wachten tot
        hij een tijdje ongebruikt is -- dat scheelt bij elke tik een rondje
        sockets openen -- en kijken dan of het lab er nog is.
        """
        nu = time.time()
        for doel, luik in list(self._open.items()):
            if nu - luik.laatst_gebruikt <= STIL_VOOR_CONTROLE:
                continue
            if _luistert(doel):
                continue          # het lab draait; de weg blijft
            print(f"[labx] poort {doel} luistert niet meer")
            self.sluit(doel)

    def alles_dicht(self) -> None:
        """Afsluiten, maar NIET vergeten wat er open stond.

        Dit is het afsluiten van de agent, niet jouw keuze om een doorgang
        dicht te doen. Wie hier het bestand zou bijwerken, wist precies de
        lijst die de herstart nodig heeft -- en dan heeft onthouden geen zin.
        """
        with self._slot:
            luiken = list(self._open.values())
            self._open.clear()
        for luik in luiken:
            luik.sluit()

    def onthoud(self) -> None:
        """Opschrijven wat er open staat, zodat een herstart het terugzet."""
        try:
            DOORGANG_BESTAND.write_text(json.dumps({
                "bijgewerkt": time.time(),
                "poorten": sorted(self._open),
            }), encoding="utf-8")
        except OSError as exc:
            print(f"[labx] kan doorgangen niet onthouden: {exc}", file=sys.stderr)

    def zet_terug(self) -> None:
        """Wat er voor de herstart open stond weer openzetten.

        Alleen als het kort geleden is: LabX hergebruikt vrijgekomen
        poortnummers, dus een doorgang van vorige week kan intussen naar een
        ander lab wijzen. Liever niets dan het verkeerde.
        """
        try:
            opgeslagen = json.loads(DOORGANG_BESTAND.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        oud = time.time() - float(opgeslagen.get("bijgewerkt") or 0)
        if oud > ONTHOUDEN_UREN * 3600:
            return
        for doel in opgeslagen.get("poorten") or []:
            try:
                self.open(int(doel))
            except (ValueError, OSError) as exc:
                print(f"[labx] doorgang {doel} niet teruggezet: {exc}", file=sys.stderr)


#: Waar we onthouden welke doorgangen er open stonden.
#:
#: Een doorgang gaat dicht als de agent afsluit -- terecht, want hij wijst
#: naar een lab op DEZE machine. Maar het tunnelscript dat jij gedownload
#: hebt, is een bestand dat blijft bestaan en naar een vast nummer wijst. Na
#: een herstart van de agent wees dat nergens meer naar, en dan "dropt de
#: tunnel de connectie" zonder dat iemand kan zien waarom. Gemeten op
#: 21-10-2026, nadat ik deze agent had bijgewerkt.
DOORGANG_BESTAND = Path.home() / ".labx-doorgangen.json"

#: Hoe oud een onthouden doorgang mag zijn om opnieuw open te gaan. LabX
#: hergebruikt vrijgekomen poortnummers, dus een doorgang van vorige week kan
#: intussen naar een ander lab wijzen. Een halve dag is ruim genoeg voor "de
#: agent is net herstart" en kort genoeg om dat te voorkomen.
ONTHOUDEN_UREN = 12

DOORGANGEN = Doorgangen()


class _AgentHandler(BaseHTTPRequestHandler):
    server_version = "LabXLabHost/1.0"
    #: Gevuld door main(); zonder token neemt hij niets aan.
    token = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write(f"[labx] {self.address_string()} {fmt % args}\n")

    def _antwoord(self, code: int, body: Dict[str, Any]) -> None:
        rauw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(rauw)))
        self.end_headers()
        self.wfile.write(rauw)

    def _mag(self) -> bool:
        kop = self.headers.get("Authorization", "")
        return bool(_AgentHandler.token) and kop == f"Bearer {_AgentHandler.token}"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            # Zonder token: alleen "ik leef". Zo kan LabX zien of deze agent
            # draait zonder het geheim te hoeven tonen.
            return self._antwoord(200, {"ok": True, "agent": "labx-lab-host"})
        if self.path == "/poorten":
            if not self._mag():
                return self._antwoord(401, {"detail": "Ongeldig of ontbrekend token"})
            return self._antwoord(200, {"open": DOORGANGEN.lijst()})
        self._antwoord(404, {"detail": "Onbekend pad"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._mag():
            return self._antwoord(401, {"detail": "Ongeldig of ontbrekend token"})
        lengte = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(lengte) or b"{}")
        except json.JSONDecodeError:
            return self._antwoord(400, {"detail": "Geen geldige JSON"})
        try:
            doel = int(body.get("poort") or 0)
        except (TypeError, ValueError):
            return self._antwoord(400, {"detail": "poort moet een getal zijn"})

        if self.path == "/poort/dicht":
            return self._antwoord(200, {"gesloten": DOORGANGEN.sluit(doel)})
        if self.path != "/poort":
            return self._antwoord(404, {"detail": "Onbekend pad"})
        try:
            luik = DOORGANGEN.open(doel)
        except ValueError as exc:
            return self._antwoord(400, {"detail": str(exc)})
        except OSError as exc:
            return self._antwoord(409, {"detail": f"Poort openen lukte niet: {exc}"})
        self._antwoord(200, {"poort": luik.poort, "lab_poort": luik.doel})


def start_agentserver() -> Optional[ThreadingHTTPServer]:
    """De ingang voor LabX. Faalt stil als de poort al bezet is: dan draait er
    al een agent, en dat is geen reden om de hartslag te laten stoppen."""
    try:
        server = ThreadingHTTPServer(("0.0.0.0", AGENT_POORT), _AgentHandler)
    except OSError as exc:
        print(f"[labx] kan niet op poort {AGENT_POORT} luisteren: {exc}", file=sys.stderr)
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


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
    #: Pas starten als LabX een token heeft gegeven; zie start_agentserver().
    agentserver: Optional[ThreadingHTTPServer] = None
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

        # Het token van een gekoppelde machine. Zodra we dat hebben kan de
        # ingang open: eerder heeft hij geen zin, want zonder token neemt hij
        # toch niets aan.
        if antwoord and antwoord.get("agent_token"):
            if _AgentHandler.token != antwoord["agent_token"]:
                _AgentHandler.token = antwoord["agent_token"]
            if agentserver is None:
                agentserver = start_agentserver()
                if agentserver is not None:
                    print(f"[labx] Ingang open op poort {AGENT_POORT}: LabX kan nu een "
                          f"labpoort van deze machine bereikbaar laten maken.")
                    # Wat er voor de herstart open stond weer openzetten. Een
                    # tunnelscript op je eigen machine wijst naar een vast
                    # nummer en weet niet dat deze agent even weg was.
                    DOORGANGEN.zet_terug()
        DOORGANGEN.ruim_op()

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
    # Doorgangen gaan dicht bij het afsluiten: ze wijzen naar labs op deze
    # machine, en wie hier straks niet meer draait hoort ook niet meer
    # bereikbaar te zijn.
    DOORGANGEN.alles_dicht()
    if agentserver is not None:
        agentserver.shutdown()
    print("\n[labx] Gestopt. Draaiende labs blijven staan; LabX ziet deze machine "
          "straks als offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
