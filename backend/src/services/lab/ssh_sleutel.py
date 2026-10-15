"""services/lab/ssh_sleutel.py — de SSH-identiteit van LabX zelf.

Een lab-host wordt aangestuurd over SSH. Dat betekent dat LabX een sleutel
nodig heeft die op die machine bekend is, en dat de machine op zijn beurt bij
LabX bekend moet zijn. Allebei is precies één keer werk, en allebei hoort het
hier te gebeuren in plaats van in een handleiding:

- **De sleutel** wordt bij de eerste vraag aangemaakt en staat in een volume,
  niet in de image. Zonder dat volume zou elke `docker compose up --build` een
  nieuwe sleutel geven en waren al je machines ineens onbekend.
- **De machine vertrouwen** gebeurt op het moment dat een MENS op "koppelen"
  drukt. Dat is het enige eerlijke moment voor een eerste-keer-vertrouwen: er
  staat iemand bij, hij heeft net de code van dat scherm overgetypt, en hij
  bevestigt daarmee naar welke machine hij kijkt. Een `StrictHostKeyChecking=no`
  op elke verbinding zou hetzelfde vertrouwen stilzwijgend elke keer opnieuw
  geven -- en dan betekent het niets meer.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from component_logging import get_logger

log = get_logger(__name__)

#: Waar ssh zelf kijkt. In de container is dat /root/.ssh; dat pad moet een
#: volume zijn, anders overleeft de sleutel geen herbouw van de image.
SSH_MAP = Path(os.environ.get("LABX_SSH_DIR") or (Path.home() / ".ssh"))
SLEUTEL = SSH_MAP / "id_ed25519"
KNOWN_HOSTS = SSH_MAP / "known_hosts"


def _zorg_voor_map() -> None:
    SSH_MAP.mkdir(parents=True, exist_ok=True)
    os.chmod(SSH_MAP, 0o700)


async def _draai(*args: str, timeout: float = 30.0) -> Tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        uit, fout = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"time-out na {timeout}s"
    return (proc.returncode or 0, uit.decode("utf-8", "replace"),
            fout.decode("utf-8", "replace"))


async def publieke_sleutel() -> str:
    """De publieke sleutel van deze LabX; maakt hem aan als hij er nog niet is.

    Ed25519 en geen wachtwoordzin: er is niemand om die in te typen als de
    backend om vier uur 's nachts een container start.
    """
    _zorg_voor_map()
    pub = SLEUTEL.with_suffix(".pub")
    if not SLEUTEL.exists() or not pub.exists():
        code, _, fout = await _draai(
            "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "labx",
            "-f", str(SLEUTEL), timeout=60)
        if code != 0:
            raise RuntimeError(f"Sleutel aanmaken mislukte: {fout.strip()[:300]}")
        os.chmod(SLEUTEL, 0o600)
        log.infox("SSH-sleutel voor lab-hosts aangemaakt", pad=str(SLEUTEL))
    return pub.read_text(encoding="utf-8").strip()


def _host_en_poort(docker_host: str) -> Tuple[Optional[str], int]:
    u = urlparse(docker_host or "")
    return u.hostname, int(u.port or 22)


async def vertrouw_host(docker_host: str) -> bool:
    """De hostsleutel van die machine opnemen in known_hosts.

    Zonder dit loopt elke verbinding vast op "Host key verification failed",
    en dat is een melding die naar niets wijst wat je in LabX kunt aanpassen.

    Geeft False als het niet lukte (machine uit, geen SSH); dat is geen reden
    om het koppelen tegen te houden -- de testknop vertelt het je, en de
    machine kan later alsnog aangaan.
    """
    naam, poort = _host_en_poort(docker_host)
    if not naam:
        return False
    _zorg_voor_map()
    code, uit, _ = await _draai("ssh-keyscan", "-T", "8", "-p", str(poort), naam,
                                timeout=20)
    regels = [r for r in uit.splitlines() if r and not r.startswith("#")]
    if code != 0 or not regels:
        log.warningx("Hostsleutel van lab-host niet op te halen", machine=naam)
        return False

    bestaand = (KNOWN_HOSTS.read_text(encoding="utf-8").splitlines()
                if KNOWN_HOSTS.exists() else [])
    nieuw = [r for r in regels if r not in bestaand]
    if nieuw:
        with KNOWN_HOSTS.open("a", encoding="utf-8") as f:
            for r in nieuw:
                f.write(r + "\n")
        os.chmod(KNOWN_HOSTS, 0o600)
        log.infox("Hostsleutel van lab-host vertrouwd", machine=naam, regels=len(nieuw))
    return True


async def toegang_test(docker_host: str) -> Tuple[bool, str]:
    """Komt LabX met deze sleutel binnen op die machine?

    Apart van de docker-test, want het zijn twee verschillende problemen met
    bijna dezelfde melding: "ik kan niet inloggen" is iets anders dan "ik ben
    binnen maar er draait geen Docker", en alleen de eerste los je op met de
    publieke sleutel.
    """
    naam, poort = _host_en_poort(docker_host)
    if not naam:
        return False, "Dat is geen ssh-adres."
    u = urlparse(docker_host)
    doel = f"{u.username}@{naam}" if u.username else naam
    await publieke_sleutel()
    code, _, fout = await _draai(
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-p", str(poort), doel, "echo ok", timeout=25)
    if code == 0:
        return True, f"Ingelogd op {doel}."
    melding = (fout or "").strip().splitlines()
    return False, (melding[-1][:300] if melding else "Inloggen mislukte.")
