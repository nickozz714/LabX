"""
services/mcp/lab_session_pool.py

Eén LANGLEVEND MCP-proces per (lab-server, container), in plaats van een nieuw
proces per tool-aanroep.

Waarom dit moest: mcp_client startte voor elke aanroep een eigen
`docker exec -i <container> <commando>`. Dat is prima voor een server die per
aanroep een vraag beantwoordt, en onbruikbaar voor een server die iets
VASTHOUDT. Een browser is het duidelijkste geval: de eerste `browser_navigate`
liet een Chrome achter die het profiel op slot hield, en elke volgende aanroep
kreeg "Browser is already in use for ... use --isolated". Meerstaps-browsen —
en dus elke login — kon zo niet werken, en ondertussen liepen de zombies in de
container op (het proces met PID 1 in een lab is `sleep infinity` en ruimt geen
kinderen op).

Met één blijvend proces per lab is de browser tussen twee aanroepen gewoon nog
open, met zijn pagina, zijn cookies en zijn ingelogde sessie.

Twee dingen zijn hier subtiel:

- **De eigenaarstaak.** `stdio_client` en `ClientSession` zijn anyio-context
  managers: ze moeten worden afgesloten in dezelfde taak die ze opende, anders
  krijg je "Attempted to exit cancel scope in a different task". Vandaar dat
  elke sessie een eigen taak heeft die de contexts openhoudt en pas afsluit als
  hem dat gevraagd wordt; aanroepers gebruiken alleen de `ClientSession` zelf,
  en dat mag wél vanuit een andere taak.
- **Eén aanroep tegelijk.** Twee chats kunnen aan hetzelfde lab hangen. Een
  slot per sessie serialiseert ze; voor een browser is dat toch al de enige
  zinnige volgorde.
"""
from __future__ import annotations

import asyncio
import shlex
import time
from typing import Any, Dict, Optional, Tuple

from component_logging import get_logger

log = get_logger(__name__)

# Na deze tijd zonder aanroep wordt het proces opgeruimd: een browser die
# niemand gebruikt hoeft geen geheugen in het lab te bezetten. De sessie start
# vanzelf opnieuw bij de volgende aanroep — alleen wat er in het geheugen van
# de browser stond (de open pagina) is dan weg; het profiel op schijf niet.
IDLE_TIMEOUT_SECONDS = 900


class LabMcpSession:
    """Eén draaiend MCP-proces in een labcontainer."""

    def __init__(self, key: Tuple[int, str], command: str, container_id: str) -> None:
        self.key = key
        self.command = command
        self.container_id = container_id
        self.session: Any = None
        self.error: Optional[BaseException] = None
        self.last_used = time.monotonic()
        self.lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())
        await self._ready.wait()
        if self.session is None:
            raise RuntimeError(
                f"MCP-server in het lab starten mislukt: {self.error}"
            ) from self.error

    async def _run(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parts = shlex.split(self.command)
        params = StdioServerParameters(
            command="docker",
            # `-i` en nooit `-t`: een tty zou de JSON-RPC-framing bederven.
            args=["exec", "-i", "-w", "/workspace", self.container_id, *parts],
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.session = session
                    self._ready.set()
                    log.infox("Lab-MCP-sessie gestart", server_id=self.key[0],
                              container=self.container_id[:12])
                    await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 — ook een cancel hoort hier te landen
            self.error = exc
            log.warningx("Lab-MCP-sessie gestopt met een fout", server_id=self.key[0],
                         error=str(exc)[:300])
        finally:
            self.session = None
            self._ready.set()

    @property
    def alive(self) -> bool:
        return self.session is not None and self._task is not None and not self._task.done()

    async def close(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
            except Exception:  # noqa: BLE001
                pass
        self.session = None


_SESSIONS: Dict[Tuple[int, str], LabMcpSession] = {}
_POOL_LOCK = asyncio.Lock()


async def call_lab_tool(server: Any, remote_name: str, args: Dict[str, Any], *,
                        container_id: str) -> Any:
    """Roep een tool aan op de blijvende sessie van deze lab-server.

    Is het proces intussen gestorven (lab herstart, browser gecrasht, `docker
    exec` verbroken), dan wordt het één keer opnieuw opgestart en de aanroep
    herhaald — anders zou de eerste aanroep na elke labherstart altijd falen."""
    for poging in (1, 2):
        sess = await _get_session(server, container_id)
        try:
            async with sess.lock:
                sess.last_used = time.monotonic()
                result = await sess.session.call_tool(remote_name, args)
                sess.last_used = time.monotonic()
                return result
        except Exception as exc:  # noqa: BLE001
            if poging == 2 or sess.alive:
                # Een levende sessie die een fout teruggeeft is een fout VAN de
                # tool; die hoort bij de aanroeper, niet bij deze laag.
                raise
            log.warningx("Lab-MCP-sessie was weg, opnieuw starten", server_id=server.id,
                         error=str(exc)[:200])
            await _drop(sess.key)
    raise RuntimeError("onbereikbaar")


async def _get_session(server: Any, container_id: str) -> LabMcpSession:
    command = (server.stdio_command or "").strip()
    if not command:
        raise RuntimeError(f"MCP-server '{server.name}' heeft geen stdio_command geconfigureerd.")
    key = (int(server.id), container_id)
    async with _POOL_LOCK:
        sess = _SESSIONS.get(key)
        # Een gewijzigd commando is een andere server: het oude proces draait
        # nog met de oude vlaggen, en dat is precies het soort verschil dat je
        # uren laat zoeken.
        if sess is not None and (not sess.alive or sess.command != command):
            await sess.close()
            _SESSIONS.pop(key, None)
            sess = None
        if sess is None:
            sess = LabMcpSession(key, command, container_id)
            await sess.start()
            _SESSIONS[key] = sess
        return sess


async def _drop(key: Tuple[int, str]) -> None:
    async with _POOL_LOCK:
        sess = _SESSIONS.pop(key, None)
    if sess is not None:
        await sess.close()


async def close_for_container(container_id: str) -> int:
    """Alle sessies in deze container sluiten — bij het stoppen, opnieuw
    opbouwen of verwijderen van een lab. Zonder dit blijft er een `docker
    exec`-proces hangen dat naar een container wijst die niet meer bestaat."""
    async with _POOL_LOCK:
        keys = [k for k in _SESSIONS if k[1] == container_id]
        sessions = [_SESSIONS.pop(k) for k in keys]
    for sess in sessions:
        await sess.close()
    if sessions:
        log.infox("Lab-MCP-sessies gesloten", container=container_id[:12], count=len(sessions))
    return len(sessions)


async def close_idle(max_idle_seconds: int = IDLE_TIMEOUT_SECONDS) -> int:
    now = time.monotonic()
    async with _POOL_LOCK:
        keys = [k for k, s in _SESSIONS.items()
                if not s.alive or (now - s.last_used) > max_idle_seconds]
        sessions = [_SESSIONS.pop(k) for k in keys]
    for sess in sessions:
        await sess.close()
    if sessions:
        log.infox("Ongebruikte lab-MCP-sessies opgeruimd", count=len(sessions))
    return len(sessions)


def status() -> list[Dict[str, Any]]:
    now = time.monotonic()
    return [{"server_id": k[0], "container": k[1][:12], "alive": s.alive,
             "idle_seconds": int(now - s.last_used), "command": s.command}
            for k, s in _SESSIONS.items()]
