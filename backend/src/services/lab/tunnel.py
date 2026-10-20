"""services/lab/tunnel.py — een vaste poort op jouw machine naar een branch-lab.

LabX deelt oplopende machinepoorten uit vanaf 18000, zodat meerdere branches
naast elkaar kunnen draaien zonder elkaar te verdringen. Precies dat botst met
applicaties die een VASTE poort op localhost eisen. Een OAuth-redirect staat
als `http://localhost:5173` bij de identity provider geregistreerd en nergens
anders: bij IntelligentHive kwam daar `AADSTS50011` uit zodra je het lab op
18004 opende, en dat is niet op te lossen in de repo of in het startscript --
het zit in een registratie waar het lab niet bij kan.

De tunnel haalt die twee uit elkaar. LabX blijft oplopende poorten uitdelen;
jij draait er op je eigen machine een doorgeefluik voor dat localhost:5173
naar het lab stuurt. Welk lab dat is, kies je door te kiezen welk script je
start.

**Je draait hem zelf, en hij is weg na een herstart.** Dat is opzet, geen
beperking: een tunnel die blijft bestaan is een tunnel die naar een lab wijst
dat er niet meer is, en dat vergissen kost meer dan hem opnieuw starten. Eén
poort kan maar naar één lab tegelijk, dus "welke branch heeft 5173" moet een
zichtbare keuze zijn.

Het script is `docker run` met socat en verder niets. Geen installatie, geen
achtergronddienst, geen rechten: Docker heb je al, anders draaide LabX niet.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException

#: Het image dat het doorgeefluik doet. Klein en overal hetzelfde.
IMAGE = "alpine/socat:latest"

#: De besturingssystemen waarvoor we een script schrijven, met de extensie
#: die ervoor zorgt dat dubbelklikken werkt.
BESTANDSNAAM = {"mac": "sh", "linux": "sh", "windows": "cmd"}

#: Hoe een container bij de machine komt waarop hij draait. Op macOS en
#: Windows zet Docker Desktop deze naam zelf; op Linux bestaat hij niet en
#: moet je hem erbij maken.
HOSTNAAM = "host.docker.internal"
LINUX_EXTRA = f"--add-host={HOSTNAAM}:host-gateway "


def _naam(lokaal: int) -> str:
    return f"labx-tunnel-{lokaal}"


def _sh(*, paren: list, linux: bool, lab: str, branch: str) -> str:
    extra = LINUX_EXTRA if linux else ""
    regels = "\n".join(f"#   http://localhost:{a}  ->  {h}:{d}" for a, h, d in paren)
    namen = " ".join(_naam(a) for a, _h, _d in paren)
    starts = "\n".join(
        f'docker run -d --rm --name {_naam(a)} \\\n'
        f'  -p 127.0.0.1:{a}:{a} {extra}\\\n'
        f'  {IMAGE} \\\n'
        f'  tcp-listen:{a},fork,reuseaddr tcp:{h}:{d} >/dev/null'
        for a, h, d in paren)
    opruimen = "\n".join(f'docker rm -f {_naam(a)} >/dev/null 2>&1 || true'
                          for a, _h, _d in paren)
    return f"""#!/bin/sh
# LabX-tunnel naar {branch} ({lab})
#
{regels}
#
# Alle poorten van dit lab tegelijk, want een applicatie die zijn eigen API
# op een vaste poort verwacht heeft aan één doorgang niets.
#
# Laat dit venster open staan; sluiten of Ctrl-C ruimt de tunnels op. Na een
# herstart van je machine zijn ze weg -- start dit dan opnieuw.
set -e

if ! docker info >/dev/null 2>&1; then
  echo "Docker draait niet. Start Docker en probeer het opnieuw." >&2
  exit 1
fi

# Een oudere tunnel op deze poort wijst naar een ander lab. Die hoort weg,
# anders krijg je stilletjes de verkeerde branch te zien.
{opruimen}

{starts}

echo "Tunnel open naar {branch}:"
{chr(10).join(f'echo "  http://localhost:{a}"' for a, _h, _d in paren)}
echo "Sluit dit venster (Ctrl-C) om ze te stoppen."

# Opruimen zodra jij stopt, ook bij Ctrl-C.
trap 'docker rm -f {namen} >/dev/null 2>&1; exit 0' INT TERM
while docker ps --format "{{{{.Names}}}}" | grep -q "{_naam(paren[0][0])}"; do
  sleep 2
done
"""


def _cmd(*, paren: list, lab: str, branch: str) -> str:
    # Windows-regeleindes: een .cmd met alleen \n draait wel, maar wordt in
    # Kladblok één lange regel en dan is niet te zien wat je start.
    regels = "\n".join(f"REM   http://localhost:{a}  --^>  {h}:{d}" for a, h, d in paren)
    opruimen = "\n".join(f"docker rm -f {_naam(a)} >nul 2>&1" for a, _h, _d in paren)
    starts = "\n".join(
        f"docker run -d --rm --name {_naam(a)} -p 127.0.0.1:{a}:{a} {IMAGE} ^\n"
        f"  tcp-listen:{a},fork,reuseaddr tcp:{h}:{d} >nul"
        for a, h, d in paren)
    adressen = "\n".join(f"echo   http://localhost:{a}" for a, _h, _d in paren)
    namen = " ".join(_naam(a) for a, _h, _d in paren)
    inhoud = f"""@echo off
REM LabX-tunnel naar {branch} ({lab})
REM
{regels}
REM
REM Alle poorten van dit lab tegelijk, want een applicatie die zijn eigen API
REM op een vaste poort verwacht heeft aan één doorgang niets.
REM
REM Laat dit venster open staan; sluiten ruimt de tunnels op.

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker draait niet. Start Docker Desktop en probeer het opnieuw.
  pause
  exit /b 1
)

REM Een oudere tunnel op deze poort wijst naar een ander lab.
{opruimen}

{starts}

echo Tunnel open naar {branch}:
{adressen}
echo Druk op een toets om ze te stoppen.
pause >nul
docker rm -f {namen} >nul 2>&1
"""
    return inhoud.replace("\n", "\r\n")


class Tunnel:
    """Schrijft het tunnelscript voor één branch-lab."""

    def __init__(self, db) -> None:
        self.db = db

    def script(self, lab_id: str, *, os_naam: str,
               lokaal: Optional[int] = None,
               container_poort: Optional[int] = None) -> Dict[str, Any]:
        """Het script, plus hoe het bestand moet heten en wat het oplevert."""
        from models.cluster import Cluster
        from models.lab import Lab

        if os_naam not in BESTANDSNAAM:
            raise HTTPException(
                status_code=400,
                detail=f"Onbekend besturingssysteem '{os_naam}'; kies mac, windows of linux.")

        p = self.db.get(Lab, lab_id)
        if p is None or not p.cluster_id:
            raise HTTPException(status_code=404, detail="Dit is geen branch-lab van een cluster.")
        kaart = {int(k): int(v) for k, v in (p.poort_map or {}).items()}
        if not kaart:
            raise HTTPException(
                status_code=400,
                detail="Dit lab publiceert geen poorten; start het eerst.")

        # ALLE poorten van dit lab, niet één. Een applicatie die zijn eigen
        # API op een vaste poort verwacht heeft aan één doorgang niets: bij
        # IntelligentHive draait de front-end op 5173 en de API op 7071, en
        # met alleen die eerste krijg je een wit scherm in plaats van een
        # foutmelding. Met `container_poort` kun je het nog steeds tot één
        # poort beperken, want twee branches tegelijk op 5173 kan niet.
        gekozen = [container_poort] if container_poort in kaart else list(kaart)

        # Een eigen lokaal nummer slaat alleen ergens op bij één poort: bij
        # twee is "welke krijgt 3000?" niet te beantwoorden, en stil de
        # eerste kiezen levert een tunnel op waarvan je het halve adres zelf
        # moet raden.
        if lokaal and len(gekozen) > 1:
            raise HTTPException(
                status_code=400,
                detail=("Dit lab publiceert meerdere poorten. Kies er één als je "
                        "zelf een lokaal nummer wilt geven."))

        # Waar het script naartoe moet wijzen. Dat is NIET zomaar de
        # machinepoort: labs publiceren op de loopback van hun eigen machine,
        # dus staat het lab ergens anders, dan moet de agent daar hem eerst
        # doorzetten. Dat is precies wat adres_voor_lab regelt -- en zonder
        # dit gaf het script "connection refused" op een poort die van buiten
        # dicht is, wat eruitziet als een stukke tunnel.
        from services.lab.lab_adres import adres_voor_lab

        c = self.db.get(Cluster, p.cluster_id)
        paren = []
        for container_p in gekozen:
            lokale_poort = int(lokaal) if (lokaal and len(gekozen) == 1) else int(container_p)
            if not 1 <= lokale_poort <= 65535:
                raise HTTPException(status_code=400, detail="Dat is geen geldige poort.")
            if c is not None and c.host_id:
                uit = adres_voor_lab(self.db, lab_id, container_p)
                doel = urlparse(uit["url"])
                paren.append((lokale_poort, doel.hostname, int(doel.port or 80)))
            else:
                # LabX en dit lab staan op dezelfde machine als jouw browser;
                # dan komt een container daar via host.docker.internal bij.
                paren.append((lokale_poort, HOSTNAAM, kaart[container_p]))

        branch = p.branch or p.name or lab_id[:8]
        if os_naam == "windows":
            inhoud = _cmd(paren=paren, lab=lab_id, branch=branch)
        else:
            inhoud = _sh(paren=paren, linux=(os_naam == "linux"), lab=lab_id, branch=branch)

        veilig = "".join(q if q.isalnum() else "-" for q in branch).strip("-") or "lab"
        return {
            "bestandsnaam": f"labx-tunnel-{veilig}.{BESTANDSNAAM[os_naam]}",
            "inhoud": inhoud,
            "url": f"http://localhost:{paren[0][0]}",
            "poorten": [a for a, _h, _d in paren],
        }

    # _doelhost is weg. Hij wees naar het ADRES van de lab-host, en daar
    # staat de labpoort op de loopback: van buiten dicht, dus elke tunnel naar
    # een lab op een andere machine gaf "connection refused". Het adres komt
    # nu van adres_voor_lab, die de agent daar eerst om een doorgang vraagt.
