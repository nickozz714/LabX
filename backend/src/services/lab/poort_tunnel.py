"""services/lab/poort_tunnel.py — bij een poort in een lab op een andere machine.

Het probleem dat dit oplost: labs op deze server zitten met de backend op één
bridge-netwerk, dus de backend bereikt ze op hun containernaam. Een lab op een
computer uit je netwerk zit op de bridge van DIE machine. De naam resolvet
hier niet, en de gepubliceerde poort staat op de loopback van die pc -- met
opzet, want een labpoort hoort niet op je LAN te staan.

De weg erheen bestaat al: we praten met die daemon over SSH. Dezelfde
verbinding kan een poort doorzetten:

    ssh -N -L 127.0.0.1:<hier>:127.0.0.1:<daar> gebruiker@pc

Loopback aan beide kanten. Er komt dus geen poort bij op je netwerk, en de
authenticatie is dezelfde SSH-sleutel die de containers al aanstuurt. Dat is
ook waarom dit géén nieuwe vertrouwensvraag is: wie deze tunnel kan opzetten,
kon op die machine al containers starten.

Wat je er wél bij koopt: de poort staat op de loopback van die pc, dus wie
fysiek achter die machine zit kan erbij. Op deze server is dat precies zo, en
voor een pc in je eigen netwerk is dat dezelfde afweging.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from component_logging import get_logger

log = get_logger(__name__)

#: Hoe lang we wachten tot de doorgezette poort antwoord geeft. Een SSH die
#: een sleutel moet aanbieden en een host moet opzoeken heeft even nodig; veel
#: langer dan dit betekent dat het niet gaat lukken.
_WACHT_SECONDEN = 15.0


def ssh_doel(docker_host: str) -> Optional[str]:
    """`ssh://nick@werkpc:22` -> `nick@werkpc` (+ losse poort).

    Geeft None voor alles wat geen ssh-adres is -- een lokale socket heeft
    geen tunnel nodig en hoort hier dus ook niet te komen.
    """
    if not docker_host or not docker_host.startswith("ssh://"):
        return None
    u = urlparse(docker_host)
    if not u.hostname:
        return None
    return f"{u.username}@{u.hostname}" if u.username else u.hostname


def _ssh_poort(docker_host: str) -> Optional[int]:
    u = urlparse(docker_host or "")
    return u.port


def _vrije_poort() -> int:
    """Een poort die nu vrij is op onze eigen loopback.

    Er zit een onvermijdelijk gaatje tussen loslaten en opnieuw pakken; in een
    backend die dit een paar keer per dag doet is dat geen echt risico, en het
    alternatief (zelf een bereik beheren) heeft dezelfde race met minder
    zekerheid dat het nummer werkelijk vrij was.
    """
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


async def _poort_leeft(poort: int) -> bool:
    try:
        lezer, schrijver = await asyncio.open_connection("127.0.0.1", poort)
    except OSError:
        return False
    schrijver.close()
    with contextlib.suppress(Exception):
        await schrijver.wait_closed()
    return True


class _Tunnel:
    def __init__(self, proces: asyncio.subprocess.Process, lokaal: int) -> None:
        self.proces = proces
        self.lokaal = lokaal

    @property
    def leeft(self) -> bool:
        return self.proces.returncode is None


class PoortTunnels:
    """Houdt de lopende doorzettingen bij, één per (machine, poort).

    Hergebruik is het punt: het noVNC-scherm doet tientallen verzoeken en een
    websocket, en voor elk daarvan een nieuwe SSH opzetten zou het beeld laten
    haperen én de verbindingen van die machine laten oplopen.
    """

    def __init__(self) -> None:
        self._tunnels: Dict[Tuple[str, int], _Tunnel] = {}
        self._slot = asyncio.Lock()

    async def lokale_poort(self, docker_host: str, verre_poort: int) -> int:
        doel = ssh_doel(docker_host)
        if not doel:
            raise RuntimeError(
                f"Kan geen tunnel opzetten naar '{docker_host}': dat is geen ssh-adres.")
        sleutel = (doel, int(verre_poort))

        async with self._slot:
            bestaand = self._tunnels.get(sleutel)
            if bestaand and bestaand.leeft and await _poort_leeft(bestaand.lokaal):
                return bestaand.lokaal
            if bestaand:
                # Hij stond er wel, maar doet het niet meer (machine herstart,
                # SSH eruit). Opruimen en opnieuw, in plaats van een poort
                # teruggeven waar niemand luistert.
                await self._stop(sleutel)

            lokaal = _vrije_poort()
            argumenten = [
                "-N",
                # Nooit om een wachtwoord of een hostbevestiging vragen: dit
                # draait zonder iemand erbij, en een ssh die op invoer wacht is
                # een verzoek dat nooit terugkomt.
                "-o", "BatchMode=yes",
                "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-L", f"127.0.0.1:{lokaal}:127.0.0.1:{int(verre_poort)}",
            ]
            poort = _ssh_poort(docker_host)
            if poort:
                argumenten += ["-p", str(poort)]
            argumenten.append(doel)

            proces = await asyncio.create_subprocess_exec(
                "ssh", *argumenten,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)

            grens = asyncio.get_event_loop().time() + _WACHT_SECONDEN
            while asyncio.get_event_loop().time() < grens:
                if proces.returncode is not None:
                    fout = b""
                    with contextlib.suppress(Exception):
                        fout = await proces.stderr.read()  # type: ignore[union-attr]
                    raise RuntimeError(
                        f"Tunnel naar {doel} viel meteen weg: "
                        f"{fout.decode('utf-8', 'replace').strip()[:300] or 'geen melding'}")
                if await _poort_leeft(lokaal):
                    self._tunnels[sleutel] = _Tunnel(proces, lokaal)
                    log.infox("Tunnel naar lab-host open", machine=doel,
                              verre_poort=verre_poort, lokale_poort=lokaal)
                    return lokaal
                await asyncio.sleep(0.25)

            proces.kill()
            raise RuntimeError(
                f"Tunnel naar {doel} kwam niet op gang binnen {int(_WACHT_SECONDEN)}s. "
                f"Luistert er iets op poort {verre_poort} van die machine, en kan "
                f"deze server er met SSH bij?")

    async def _stop(self, sleutel: Tuple[str, int]) -> None:
        t = self._tunnels.pop(sleutel, None)
        if t and t.leeft:
            t.proces.kill()
            with contextlib.suppress(Exception):
                await t.proces.wait()

    async def sluit_machine(self, docker_host: str) -> None:
        """Alles naar deze machine dicht. Na een verhuizing of ontkoppelen:
        een tunnel die blijft staan naar een lab dat daar niet meer is, is een
        poort op deze server die nergens meer heen gaat."""
        doel = ssh_doel(docker_host)
        if not doel:
            return
        async with self._slot:
            for sleutel in [k for k in self._tunnels if k[0] == doel]:
                await self._stop(sleutel)

    async def sluit_alles(self) -> None:
        async with self._slot:
            for sleutel in list(self._tunnels):
                await self._stop(sleutel)


#: Eén set tunnels per backend-proces; ze horen bij de machine en niet bij een
#: verzoek, en een nieuwe set per verzoek zou elke klik een SSH kosten.
tunnels = PoortTunnels()
