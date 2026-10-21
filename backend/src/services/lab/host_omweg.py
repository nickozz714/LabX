"""services/lab/host_omweg.py — dezelfde machine, een ander adres.

Het adres van een lab-host vul je één keer in bij het koppelen, en dat blijft
staan. Dat is goed: het is het adres dat jij bedoelt. Het gaat mis zodra die
machine verhuist zonder van identiteit te veranderen -- een laptop die van
huis gaat en via de VPN terugkomt, zit thuis op 192.168.2.65 en door de
tunnel op 10.13.13.x. Het ingestelde adres klopt dan niet meer, en LabX kan
er geen lab meer starten terwijl de machine gewoon bereikbaar is.

De agent weet het wel. Hij meldt zich elke 30 seconden en geeft daarbij het
bronadres waarmee hij LabX bereikt -- per definitie een adres waarover het
verkeer loopt. `agent_url` volgt dus vanzelf mee; alleen `docker_host` niet,
want die heb jij met de hand gezet.

Daarom deze omweg. Werkt het ingestelde adres niet en het gemelde wel, dan
gaat het werk tijdelijk daarlangs. Zodra het ingestelde adres weer antwoordt,
is de omweg weg. Twee dingen die hij met opzet NIET doet:

- Hij verandert `docker_host` niet. Wat jij hebt ingesteld blijft staan, want
  anders is na één keer van huis gaan niet meer te zien wat de bedoeling was.
- Hij verzint geen adres. Alleen wat de agent zelf gemeld heeft komt in
  aanmerking, en alleen als de Docker daar ook echt antwoordt.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)


def _gebruiker(docker_host: str) -> str:
    """De `gebruiker@` uit ssh://gebruiker@machine, want die blijft gelijk."""
    p = urlparse(docker_host or "")
    return f"{p.username}@" if p.username else ""


def gemeld_adres(host) -> Optional[str]:
    """Het ssh-adres dat hoort bij waar de agent zich vandaan meldt.

    Geeft None als dat niets nieuws oplevert: hetzelfde adres als ingesteld,
    of geen bruikbare melding.
    """
    if not host.agent_url or not host.docker_host:
        return None
    gemeld = urlparse(host.agent_url).hostname
    ingesteld = urlparse(host.docker_host).hostname
    if not gemeld or gemeld == ingesteld:
        return None
    return f"ssh://{_gebruiker(host.docker_host)}{gemeld}"


async def _antwoordt(docker_host: str) -> bool:
    from services.lab.docker_runtime import DockerRuntime

    try:
        uit = await DockerRuntime(docker_host=docker_host).diagnose()
    except Exception:  # noqa: BLE001 — onbereikbaar is het antwoord, geen storing
        return False
    return bool(uit.get("cli_present") and uit.get("daemon_up"))


async def bepaal(db: Session) -> List[Dict[str, Any]]:
    """Voor elke gekoppelde machine: moet er een omweg aan of uit?

    Geeft terug wat er veranderd is, zodat de aanroeper het kan melden. Een
    machine die helemaal niet bereikbaar is, laten we met rust: dan valt er
    niets te kiezen en is "uit" net zo misleidend als "aan".
    """
    from models.lab_host import LabHost

    veranderd: List[Dict[str, Any]] = []
    for host in db.query(LabHost).filter(LabHost.status == "gekoppeld").all():
        if not host.docker_host:
            continue
        if await _antwoordt(host.docker_host):
            if host.docker_host_omweg:
                log.infox("Omweg niet meer nodig", machine=host.naam,
                          adres=host.docker_host)
                host.docker_host_omweg = None
                veranderd.append({"machine": host.naam, "omweg": None})
            continue

        kandidaat = gemeld_adres(host)
        if not kandidaat or kandidaat == host.docker_host_omweg:
            continue
        if not await _antwoordt(kandidaat):
            continue
        log.infox("Omweg in gebruik genomen", machine=host.naam,
                  ingesteld=host.docker_host, via=kandidaat)
        host.docker_host_omweg = kandidaat
        veranderd.append({"machine": host.naam, "omweg": kandidaat})

    if veranderd:
        db.commit()
    return veranderd
