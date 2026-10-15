"""services/lab/host_runtime.py — een lab dat op de machine zelf draait.

De tegenhanger van `DockerRuntime`. Dezelfde methoden, een heel andere plek:
niet in een container maar op de computer waar LabX op staat, binnen één
projectmap, via de hostagent (zie backend/host-agent/labx_host_agent.py).

Waarom dat soms moet: een klant kan eisen dat je aanmeldt vanaf een apparaat
dat zij kennen. Een container is geen apparaat en kan die claim niet afgeven --
en hoort dat ook niet te kunnen. Op de host draait het commando als de
gebruiker, met de `az`-sessie die die zelf in een browser heeft opgezet. Er
wordt niets gekopieerd en niets voorgewend.

Wat hier BEWUST niet in zit, en waarom:

- `run_container`, `start`, `stop`, `remove`, `pull`, volumes, poorten. Een
  hostlab heeft geen container om te starten en geen image om te halen. Die
  methoden zouden stil niets doen, en een knop die stil niets doet is erger dan
  een knop die er niet is. Ze gooien hier een duidelijke fout.
- Opschalen naar meer werkers. Er is één machine.

De grenzen liggen bij de AGENT, niet hier. Deze klasse kan er niet omheen
werken: hij stuurt een commando en een projectmap, en de agent beslist. Dat is
met opzet -- een grens die de aanroeper kan verzetten, is geen grens.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from component_logging import get_logger

log = get_logger(__name__)

STANDAARD_POORT = 8099
#: Vanuit de api-container is de machine zelf dit adres; docker-compose mapt
#: hem al (`extra_hosts: host.docker.internal:host-gateway`), juist omdat de
#: guard zijn lokale model ook op de host kan hebben staan.
STANDAARD_HOST = "host.docker.internal"


class HostAgentFout(RuntimeError):
    """De agent is er niet, of weigert. Een eigen type omdat dit vrijwel altijd
    hetzelfde betekent -- hij draait niet -- en de melding dat moet zeggen in
    plaats van een HTTP-code."""


class HostRuntime:
    def __init__(self, *, project_dir: str, token: str,
                 host: Optional[str] = None, port: Optional[int] = None) -> None:
        self.project_dir = project_dir
        self._token = token
        self._basis = f"http://{host or STANDAARD_HOST}:{port or STANDAARD_POORT}"

    # ── wat een hostlab wél kan ──────────────────────────────────────────────

    async def is_bereikbaar(self) -> bool:
        """Draait de agent? Zonder token, zodat dit ook te zien is voordat er
        iets is ingesteld."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=4) as client:
                r = await client.get(f"{self._basis}/health")
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def exec(
        self,
        container_id: str,              # genegeerd: er is geen container
        cmd: List[str],
        *,
        workdir: str = "",              # de projectmap is de werkmap
        stdin: Optional[bytes] = None,
        timeout: float = 120.0,
        max_chars: int = 60_000,
    ) -> Dict[str, Any]:
        """Eén commando op de host, in de zandbak van de agent.

        Dezelfde vorm als `DockerRuntime.exec` zodat alles erboven -- de
        gateway-tools, de guard, de kluis, het auditspoor -- niet hoeft te
        weten waar het draait.

        `cmd` komt binnen als ARGV, want zo geeft docker het door. De agent
        draait een shell, dus het moet één regel worden -- en dan is elk deel
        apart aanhalen de enige juiste vertaling. Met spaties plakken ging mis
        op precies het commando dat de chat gebruikt:

            ["sh", "-lc", 'if command -v bash …; then exec bash -c "$0"; …',
             "pwd && whoami"]

        Dat derde element is een SCRIPT en het vierde is `$0` daarbinnen. Plak
        je ze achter elkaar, dan leest de shell het script als losse woorden en
        krijg je "syntax error near unexpected token `then'". Met shlex.join
        blijft de betekenis staan die docker er ook aan zou geven.
        """
        import httpx
        import shlex

        # `sh -lc` wordt `sh -c`. Een LOGIN-shell leest je profiel, en dat ligt
        # in de thuismap die de zandbak juist afsluit -- elk commando begon
        # daardoor met "/Users/…/.profile: Operation not permitted".
        #
        # Het gat openzetten zou dat stil maken, maar dan laat je je eigen
        # aliassen, PATH-aanpassingen en exports meedoen met wat de agent
        # uitvoert. Juist bij een nachtrun wil je dat niet: het commando hoort
        # te doen wat er staat, niet wat jouw shell ervan maakt. De omgeving
        # die de agent meekrijgt (inclusief PATH) is er al.
        argv = list(cmd)
        if len(argv) >= 2 and argv[1] in ("-lc", "-l"):
            argv[1] = "-c" if argv[1] == "-lc" else "-c"
        regel = shlex.join(argv)
        if stdin is not None:
            # Geen stdin-kanaal naar de agent: dat zou een tweede manier zijn
            # om inhoud binnen te brengen, met een eigen stel randgevallen.
            # Wie een bestand wil neerzetten, gebruikt write_file.
            raise HostAgentFout(
                "Een hostlab neemt geen invoer via stdin aan; schrijf het bestand "
                "eerst weg en lees het in het commando.")
        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                r = await client.post(
                    f"{self._basis}/exec",
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"project": self.project_dir, "command": regel,
                          "timeout": timeout})
        except Exception as exc:  # noqa: BLE001
            raise HostAgentFout(
                f"De hostagent is niet bereikbaar op {self._basis}. Draait hij? "
                f"Start hem met `python3 backend/host-agent/labx_host_agent.py` op de "
                f"machine zelf. ({str(exc)[:160]})") from exc

        if r.status_code == 401:
            raise HostAgentFout(
                "De hostagent wees het token af. Zet in de labinstellingen "
                "hetzelfde token als in LABX_HOST_AGENT_TOKEN.")
        if r.status_code >= 400:
            detail = ""
            try:
                detail = (r.json() or {}).get("detail", "")
            except Exception:  # noqa: BLE001
                pass
            raise HostAgentFout(detail or f"De hostagent gaf HTTP {r.status_code}.")

        uit = r.json()
        tekst = str(uit.get("output") or "")
        afgekapt = bool(uit.get("truncated"))
        if len(tekst) > max_chars:
            tekst = tekst[:max_chars] + "\n… [uitvoer afgekapt]"
            afgekapt = True
        return {"exit_code": int(uit.get("exit_code") or 0),
                "output": tekst, "truncated": afgekapt}

    async def diagnose(self) -> Dict[str, Any]:
        bereikbaar = await self.is_bereikbaar()
        return {
            "daemon_up": bereikbaar,
            "soort": "host",
            "project": self.project_dir,
            "agent": self._basis,
            "hint": None if bereikbaar else (
                "De hostagent draait niet. Start hem op de machine zelf met "
                "`python3 backend/host-agent/labx_host_agent.py`."),
        }

    # ── wat een hostlab niet kan ─────────────────────────────────────────────

    def _niet(self, wat: str) -> None:
        raise HostAgentFout(
            f"{wat} bestaat niet voor een hostlab: er is geen container, alleen "
            f"de projectmap {self.project_dir}.")

    async def run_container(self, **_kw: Any) -> str:
        self._niet("Een container starten")
        return ""                                        # pragma: no cover

    async def run_ephemeral(self, **_kw: Any) -> str:
        self._niet("Een hulpcontainer starten")
        return ""                                        # pragma: no cover

    async def pull(self, *_a: Any, **_kw: Any) -> None:
        self._niet("Een image ophalen")

    async def create_volume(self, *_a: Any, **_kw: Any) -> None:
        self._niet("Een volume maken")

    async def remove_volume(self, *_a: Any, **_kw: Any) -> None:
        self._niet("Een volume verwijderen")

    async def port_map(self, *_a: Any, **_kw: Any) -> Dict[int, int]:
        return {}

    async def container_ip(self, *_a: Any, **_kw: Any) -> Optional[str]:
        return None

    async def list_managed(self) -> List[Dict[str, str]]:
        return []

    # Deze drie mogen stil slagen: ze worden aangeroepen in opruimpaden die
    # over het hele lab gaan, en daar is "er valt niets te stoppen" het juiste
    # antwoord -- geen fout die een verwijdering laat stranden.
    async def start(self, *_a: Any, **_kw: Any) -> None:
        return None

    async def stop(self, *_a: Any, **_kw: Any) -> None:
        return None

    async def remove(self, *_a: Any, **_kw: Any) -> None:
        return None

    async def state(self, *_a: Any, **_kw: Any) -> Optional[str]:
        return "running" if await self.is_bereikbaar() else None

    async def exists(self, *_a: Any, **_kw: Any) -> bool:
        return await self.is_bereikbaar()

    async def wait_until_gone(self, *_a: Any, **_kw: Any) -> bool:
        return True

    async def ensure_network(self) -> None:
        return None
