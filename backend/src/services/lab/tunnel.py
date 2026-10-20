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


def _sh(*, lokaal: int, doelhost: str, doelpoort: int, linux: bool,
        lab: str, branch: str) -> str:
    extra = LINUX_EXTRA if linux else ""
    return f"""#!/bin/sh
# LabX-tunnel naar {branch} ({lab})
#
#   http://localhost:{lokaal}  ->  {doelhost}:{doelpoort}
#
# Laat dit venster open staan; sluiten of Ctrl-C ruimt de tunnel op. Na een
# herstart van je machine is hij weg -- start hem dan opnieuw.
set -e

NAAM={_naam(lokaal)}

if ! docker info >/dev/null 2>&1; then
  echo "Docker draait niet. Start Docker en probeer het opnieuw." >&2
  exit 1
fi

# Een oudere tunnel op deze poort wijst naar een ander lab. Die hoort weg,
# anders krijg je stilletjes de verkeerde branch te zien.
docker rm -f "$NAAM" >/dev/null 2>&1 || true

echo "Tunnel open: http://localhost:{lokaal}  ->  {branch}"
echo "Sluit dit venster om hem te stoppen."

exec docker run --rm --name "$NAAM" \\
  -p 127.0.0.1:{lokaal}:{lokaal} {extra}\\
  {IMAGE} \\
  tcp-listen:{lokaal},fork,reuseaddr tcp:{doelhost}:{doelpoort}
"""


def _cmd(*, lokaal: int, doelhost: str, doelpoort: int,
         lab: str, branch: str) -> str:
    # Windows-regeleindes: een .cmd met alleen \n draait wel, maar wordt in
    # Kladblok één lange regel en dan is niet te zien wat je start.
    regels = f"""@echo off
REM LabX-tunnel naar {branch} ({lab})
REM
REM   http://localhost:{lokaal}  ->  {doelhost}:{doelpoort}
REM
REM Laat dit venster open staan; sluiten ruimt de tunnel op. Na een herstart
REM van je machine is hij weg -- start hem dan opnieuw.

set NAAM={_naam(lokaal)}

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker draait niet. Start Docker Desktop en probeer het opnieuw.
  pause
  exit /b 1
)

REM Een oudere tunnel op deze poort wijst naar een ander lab.
docker rm -f %NAAM% >nul 2>&1

echo Tunnel open: http://localhost:{lokaal}  --^>  {branch}
echo Sluit dit venster om hem te stoppen.

docker run --rm --name %NAAM% -p 127.0.0.1:{lokaal}:{lokaal} {IMAGE} ^
  tcp-listen:{lokaal},fork,reuseaddr tcp:{doelhost}:{doelpoort}
"""
    return regels.replace("\n", "\r\n")


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

        # Welke poort van het lab? De eerste is de hoofdpoort (de volgorde
        # komt van de detector en is betekenisvol), maar bij een cluster met
        # een front-end en een API wil je soms die tweede.
        gekozen = container_poort if container_poort in kaart else next(iter(kaart))
        machine_poort = kaart[gekozen]

        # De poort op JOUW machine. Standaard dezelfde als in de container,
        # want dat is precies het geval waarvoor dit bestaat: de applicatie
        # verwacht zichzelf op 5173 omdat daar de redirect op geregistreerd
        # staat.
        lokaal = int(lokaal or gekozen)
        if not 1 <= lokaal <= 65535:
            raise HTTPException(status_code=400, detail="Dat is geen geldige poort.")

        c = self.db.get(Cluster, p.cluster_id)
        doelhost = self._doelhost(c)
        branch = p.branch or p.name or lab_id[:8]

        if os_naam == "windows":
            inhoud = _cmd(lokaal=lokaal, doelhost=doelhost, doelpoort=machine_poort,
                          lab=lab_id, branch=branch)
        else:
            inhoud = _sh(lokaal=lokaal, doelhost=doelhost, doelpoort=machine_poort,
                         linux=(os_naam == "linux"), lab=lab_id, branch=branch)

        veilig = "".join(q if q.isalnum() else "-" for q in branch).strip("-") or "lab"
        return {
            "bestandsnaam": f"labx-tunnel-{veilig}-{lokaal}.{BESTANDSNAAM[os_naam]}",
            "inhoud": inhoud,
            "url": f"http://localhost:{lokaal}",
            "container_poort": gekozen,
            "machine_poort": machine_poort,
        }

    def _doelhost(self, c) -> str:
        """Waar het script naartoe wijst.

        Draait het cluster op deze machine, dan komt de container bij de
        machinepoort via `host.docker.internal`. Staat het op een gekoppelde
        lab-host, dan moet het script naar DIE machine wijzen -- anders
        tunnelt hij vrolijk naar een poort op de verkeerde computer.
        """
        if c is None or not c.host_id:
            return HOSTNAAM
        from models.lab_host import LabHost

        host = self.db.get(LabHost, c.host_id)
        adres = (getattr(host, "agent_url", None) or getattr(host, "docker_host", None) or "")
        adres = str(adres).split("://")[-1].split("/")[0].split(":")[0].strip()
        if not adres:
            raise HTTPException(
                status_code=400,
                detail="LabX weet het adres van de lab-host niet; een tunnel kan daar niet heen.")
        return adres
