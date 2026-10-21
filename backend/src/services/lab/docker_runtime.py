"""
services/lab/docker_runtime.py

Thin, dependency-free wrapper around the `docker` CLI for labs. Ported from
ND3X-public/src/services/playground/docker_runtime.py, with the corrections
the sibling-container validation surfaced (see the LabX plan, Fase 1):

- LabX itself runs in a container with /var/run/docker.sock mounted, so
  every container this wraps is a SIBLING of the backend, not a child of it.
  Named volumes and `docker exec` are daemon-side concepts and work exactly
  as in ND3X. What does NOT carry over is host bind-mounts (a path on the
  left of `-v host:container` resolves on the DAEMON's host, not inside the
  LabX container) — so this runtime never does bind-mounts, only named
  volumes, matching ND3X's own "no host-mounts" rule.
- Labs join a dedicated user-defined bridge network (LABX_LAB_NETWORK) that
  the backend is also on, so the backend can reach a published lab port by
  container DNS name (`lab.network_alias`) instead of ND3X's
  `-p 127.0.0.1:0:<port>` (which binds the HOST's loopback — unreachable
  from inside the LabX container). `-p 127.0.0.1:0:<port>` is kept ONLY for
  browser access from the docker host itself.
- Every exec is wrapped in `timeout -k 2 <n>s` INSIDE the container: ND3X's
  timeout only kills the docker CLI client on the backend side, leaving a
  runaway process alive in the lab.
- `DOCKER_HOST` is read from settings and passed to every subprocess's env,
  so a docker-socket-proxy is a pure config change later, not a code change.

Safety rules live HERE, not scattered through the service:
- no host-mounts (only the named volume on /workspace),
- resource caps (--cpus/--memory/--pids-limit) and no-new-privileges always on,
- network is either the shared lab bridge (internal, if allow_network=False)
  or the shared lab bridge (default route via the host, if True) — never
  `--network none` (that would also cut LabX off from an allow_network=False
  lab, since `port_map`/exec/terminal still need to reach it).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from component_logging import get_logger
from config import settings

log = get_logger(__name__)

_LABEL = "labx.managed"
_LAB_LABEL = "labx.lab"
#: Modelcontainers (Ollama) van de pool. Een EIGEN label, zodat de
#: lab-opruiming ze met rust laat: die ruimt alles op wat labx.managed
#: draagt en geen lab-rij meer heeft, en een runner is geen lab.
_MODEL_LABEL = "labx.model=1"
#: Vaste diensten van een cluster. Weer een eigen label, en om dezelfde
#: reden: wat bij een cluster hoort mag niet opgeruimd worden door iets
#: dat over labs gaat.
_DIENST_LABEL = "labx.dienst=1"
_EXPIRES_LABEL = "labx.expires_at"
#: Het wegwerpcontainertje waarmee een volume in- en uitgepakt wordt. Klein,
#: overal te krijgen, en het enige dat het hoeft te kunnen is `tar`.
_HULP_IMAGE = "alpine:3"
_DEFAULT_EXEC_TIMEOUT_S = 120
_MAX_OUTPUT_CHARS = 60_000


def _docker_env(docker_host: Optional[str] = None) -> Dict[str, str]:
    env = dict(os.environ)
    doel = docker_host or settings.DOCKER_HOST
    if doel:
        env["DOCKER_HOST"] = doel
    return env


class DockerRuntime:
    """Container lifecycle + exec for labs, against the shared (sibling) daemon.

    `docker_host` kiest WELKE daemon. Leeg = die van deze machine (de socket
    uit de instellingen); een `ssh://gebruiker@pc` wijst naar een andere
    computer in het netwerk. De docker-CLI regelt die verbinding zelf -- geen
    open poort, geen eigen protocol, en de authenticatie is gewoon je
    SSH-sleutel.

    Alles erboven merkt er niets van: dezelfde methoden, dezelfde argumenten.
    Dat is met opzet, want een lab hoort niet te weten waar hij toevallig
    draait.
    """

    def __init__(self, docker_bin: Optional[str] = None,
                 docker_host: Optional[str] = None) -> None:
        self._bin = docker_bin or settings.DOCKER_BIN
        self._network = settings.LAB_NETWORK
        self._docker_host = docker_host

    async def _run_cli(
        self, *args: str, stdin: Optional[bytes] = None, timeout: float = 60.0,
    ) -> Tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            self._bin, *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_env(self._docker_host),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"docker {' '.join(args[:2])} time-out na {timeout}s")
        return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def _cli_ok(self, *args: str, timeout: float = 60.0, stdin: Optional[bytes] = None) -> str:
        code, out, err = await self._run_cli(*args, stdin=stdin, timeout=timeout)
        if code != 0:
            raise RuntimeError((err or out).strip()[:800] or f"docker {args[0]} faalde ({code})")
        return out.strip()

    # ── diagnostics (fix for "Docker niet beschikbaar" in a container) ───────

    def is_available(self) -> bool:
        return shutil.which(self._bin) is not None

    async def daemon_up(self) -> bool:
        """Praat deze runtime met een daemon die antwoordt?

        Ook op de UITVOER kijken en niet alleen op de exit-code. `docker info
        --format ...` geeft namelijk 0 terug terwijl het faalt:

            $ DOCKER_HOST=ssh://nick@bestaat-niet docker info --format '{{.ServerVersion}}'
            error during connect: ... exec: "ssh": executable file not found
            $ echo $?
            0

        Op alleen de code vertrouwen betekende dat de knop "verbinding testen"
        groen gaf voor een machine die er niet is -- en dat je dat pas merkt
        als er een lab op moet starten.
        """
        try:
            code, out, _ = await self._run_cli(
                "info", "--format", "{{.ServerVersion}}", timeout=15)
            return code == 0 and bool((out or "").strip())
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def in_container() -> bool:
        if os.path.exists("/.dockerenv"):
            return True
        try:
            with open("/proc/1/cgroup", "rt", encoding="utf-8") as fh:
                return "docker" in fh.read() or "kubepods" in fh.read()
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def socket_mounted() -> bool:
        return os.path.exists("/var/run/docker.sock")

    async def diagnose(self) -> Dict[str, Any]:
        cli_present = self.is_available()
        daemon_ok = await self.daemon_up() if cli_present else False
        in_container = self.in_container()
        socket_mounted = self.socket_mounted()
        hint = None
        if not cli_present:
            hint = "docker-CLI ontbreekt in dit image — installeer 'm in de Dockerfile."
        elif in_container and not socket_mounted and not daemon_ok:
            hint = (
                "LabX draait zelf in een container maar /var/run/docker.sock is niet "
                "gemount — mount de socket op de api-service in docker-compose.yml zodat "
                "labs als sibling-containers naast LabX kunnen starten."
            )
        elif not daemon_ok and self._docker_host:
            hint = (f"Geen contact met de Docker op {self._docker_host}. Staat die "
                    f"machine aan, draait Docker er, en kan deze server er met SSH bij "
                    f"(sleutel uitgewisseld, host bekend in known_hosts)?")
        elif not daemon_ok:
            hint = "docker info faalt — draait de daemon, en heeft dit proces toegang tot de socket?"
        return {
            "cli_present": cli_present,
            "daemon_up": daemon_ok,
            "in_container": in_container,
            "socket_mounted": socket_mounted,
            "docker_host": self._docker_host or settings.DOCKER_HOST,
            "network": self._network,
            "hint": hint,
        }

    # ── images / volumes / network ───────────────────────────────────────────

    async def pull(self, image: str, *, timeout: float = 600.0) -> None:
        await self._cli_ok("pull", image, timeout=timeout)

    async def local_images(self) -> List[str]:
        code, out, _ = await self._run_cli(
            "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", timeout=20)
        if code != 0:
            return []
        return sorted({l.strip() for l in out.splitlines() if l.strip() and "<none>" not in l})

    async def create_volume(self, name: str) -> None:
        await self._cli_ok("volume", "create", "--label", _LABEL, name)

    async def export_volume(self, volume: str, doel: str, *,
                            timeout: float = 3600.0) -> None:
        """Een volume als tar-bestand wegschrijven.

        Via een wegwerpcontainertje, want een volume is op afstand niet anders
        te bereiken: `docker cp` werkt op containers, niet op volumes. Dit is
        de enige manier die óók werkt als de daemon op een andere machine
        staat -- en dat is precies waarvoor het bestaat.

        De tar gaat rechtstreeks naar een BESTAND en niet door het geheugen:
        een /workspace met een node_modules erin is zo een paar gigabyte, en
        dat wil je niet in een Python-variabele hebben.
        """
        with open(doel, "wb") as f:
            proc = await asyncio.create_subprocess_exec(
                self._bin, "run", "--rm", "-v", f"{volume}:/van:ro",
                _HULP_IMAGE, "tar", "czf", "-", "-C", "/van", ".",
                stdout=f, stderr=asyncio.subprocess.PIPE,
                env=_docker_env(self._docker_host))
            _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Volume {volume} uitpakken mislukte: "
                f"{(err or b'').decode(errors='replace')[:400]}")

    async def import_volume(self, volume: str, bron: str, *,
                            timeout: float = 3600.0) -> None:
        """Een eerder weggeschreven tar terugzetten in een (nieuw) volume."""
        await self.create_volume(volume)
        with open(bron, "rb") as f:
            proc = await asyncio.create_subprocess_exec(
                self._bin, "run", "--rm", "-i", "-v", f"{volume}:/naar",
                _HULP_IMAGE, "tar", "xzf", "-", "-C", "/naar",
                stdin=f, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_env(self._docker_host))
            _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Volume {volume} terugzetten mislukte: "
                f"{(err or b'').decode(errors='replace')[:400]}")

    async def vul_volume(self, volume: str, bron_map: str, *,
                         timeout: float = 3600.0) -> None:
        """Een lokale MAP in een (nieuw) volume zetten.

        Hetzelfde trucje als `import_volume`, maar vanaf een map in plaats van
        een tar-bestand: tar aan deze kant inpakken en in een wegwerpcontainer
        weer uit. Dat is de enige weg die óók werkt als de daemon op een
        andere machine draait -- `docker cp` werkt op containers, niet op
        volumes, en een bind mount van een pad hier bestaat daar niet.
        """
        # Eerst naar een tar-BESTAND en dan pas naar binnen, niet via een pijp
        # tussen twee processen: `create_subprocess_exec` wil voor stdin een
        # echte filedescriptor, en de StreamReader van een ander proces is dat
        # niet ("'StreamReader' object has no attribute 'fileno'"). Een tijdelijk
        # bestand is bovendien hetzelfde pad als `import_volume` al liep.
        fd, tar = tempfile.mkstemp(prefix="labx-bundel-", suffix=".tar.gz")
        os.close(fd)
        try:
            pak_in = await asyncio.create_subprocess_exec(
                "tar", "czf", tar, "-C", bron_map, ".",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(pak_in.communicate(), timeout=timeout)
            if pak_in.returncode != 0:
                raise RuntimeError(
                    f"Bundel inpakken mislukte: "
                    f"{(err or b'').decode(errors='replace')[:400]}")
            await self.import_volume(volume, tar, timeout=timeout)
        finally:
            try:
                os.unlink(tar)
            except OSError:
                pass

    async def volume_bestaat(self, volume: str) -> bool:
        code, _, _ = await self._run_cli("volume", "inspect", volume, timeout=20)
        return code == 0

    async def remove_volume(self, name: str) -> None:
        await self._cli_ok("volume", "rm", "-f", name)

    async def ensure_network(self) -> None:
        """Idempotently ensure the shared lab bridge exists — the backend joins
        it too (via compose), so labs are reachable by container DNS name."""
        code, _, _ = await self._run_cli("network", "inspect", self._network, timeout=10)
        if code == 0:
            return
        try:
            await self._cli_ok("network", "create", "--label", _LABEL, self._network, timeout=20)
        except Exception as exc:  # noqa: BLE001 — a concurrent create is fine
            log.infox("Netwerk-create overgeslagen (bestaat waarschijnlijk al)", error=str(exc)[:200])

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def run_container(
        self,
        *,
        name: str,
        image: str,
        volume: str,
        cpu_limit: float,
        mem_limit_mb: int,
        allow_network: bool,
        ports: Optional[List[int]] = None,
        expires_at: Optional[str] = None,
        netwerk: Optional[str] = None,
        poort_map: Optional[Dict[int, int]] = None,
        omgeving: Optional[Dict[str, str]] = None,
    ) -> str:
        """Start the lab container: idle process, /workspace on the volume,
        caps + no-new-privileges, NO host-mounts, on the shared lab network with
        a DNS alias == its container name. Returns the container id."""
        # Een branch-lab hoort bij ZIJN cluster en niet bij het gedeelde
        # labnetwerk: daar moet het de vaste diensten op naam kunnen vinden,
        # en daar hoort niet elk ander lab bij te kunnen.
        net = netwerk or self._network
        if netwerk:
            await self.ensure_network_named(netwerk)
        else:
            await self.ensure_network()

        # Staat er al een container met deze naam, dan is dat ONZE container:
        # de naam is van het lab. Draait hij nog, dan is dat het lab zelf en
        # nemen we hem weer in gebruik in plaats van een tweede te maken.
        # Staat hij stil, dan mag hij weg.
        #
        # Zonder dit strandde elke start op "The container name is already in
        # use" zodra LabX het spoor van een container kwijt was -- en dat kan,
        # zie list_managed(). Een foutmelding over onze eigen administratie is
        # niets waar jij iets aan kunt doen.
        bestaand = await self._bestaande(name)
        if bestaand:
            if bestaand["state"] == "running":
                log.infox("Bestaande labcontainer weer in gebruik genomen",
                          naam=name, container=bestaand["id"][:12])
                return bestaand["id"]
            await self._run_cli("rm", "-f", name, timeout=120)

        args = [
            "run", "-d",
            "--name", name,
            "--label", _LABEL,
            "--label", f"{_LAB_LABEL}={name}",
            "--restart", "no",  # don't outlive the host past its TTL after a reboot
            "--cpus", str(cpu_limit),
            "--memory", f"{int(mem_limit_mb)}m",
            "--pids-limit", "512",
            # PID 1 in een lab is `sleep infinity`, en die ruimt geen
            # weeskinderen op. Zonder init-proces stapelen zombies zich op —
            # een browser laat er tientallen per sessie achter — tot de
            # pids-limit vol zit en er niets meer start.
            "--init",
            "--security-opt", "no-new-privileges",
            "--network", net,
            "--network-alias", name,
            "-v", f"{volume}:/workspace",
            "-w", "/workspace",
        ]
        for k, v in (omgeving or {}).items():
            args += ["-e", f"{k}={v}"]
        if expires_at:
            args += ["--label", f"{_EXPIRES_LABEL}={expires_at}"]
        # Een vast nummer als het gegeven is (een branch-lab hoort op een
        # adres te staan dat niet elke herstart verandert), anders laat de
        # daemon er een kiezen zoals altijd.
        for container_poort, machine_poort in (poort_map or {}).items():
            args += ["-p", f"127.0.0.1:{int(machine_poort)}:{int(container_poort)}"]
        for port in (ports or []):
            if poort_map and int(port) in {int(k) for k in poort_map}:
                continue
            args += ["-p", f"127.0.0.1:0:{int(port)}"]
        args += [image, "sleep", "infinity"]
        return await self._cli_ok(*args, timeout=180)

    async def container_ip(self, container_id: str) -> Optional[str]:
        """Het IP van deze container op het lab-netwerk. De host kan daar
        rechtstreeks bij (de bridge is routeerbaar), en dat is precies wat een
        `ssh -L` nodig heeft — zonder de poort te publiceren en dus zonder de
        container opnieuw op te bouwen."""
        code, out, _ = await self._run_cli(
            "inspect", "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
            container_id, timeout=15)
        if code != 0:
            return None
        ips = [x for x in out.split() if x]
        return ips[0] if ips else None

    async def verwijder_netwerk(self, naam: str) -> None:
        """Een netwerk opruimen. Bestaat het niet (meer), dan is dat geen fout.

        Docker weigert een netwerk met aangesloten containers; die horen dus
        eerst weg. Mislukt het toch, dan is dat hier geen reden om de rest van
        het opruimen te staken -- een achtergebleven netwerk is hinderlijk, een
        halve verwijdering is erger.
        """
        code, _, err = await self._run_cli("network", "rm", naam, timeout=30)
        if code != 0 and "not found" not in (err or "").lower():
            raise RuntimeError((err or "").strip()[:300] or f"netwerk {naam} niet te verwijderen")

    async def ensure_network_named(self, naam: str) -> None:
        """Een netwerk met een EIGEN naam, voor een cluster.

        `ensure_network` gaat over het gedeelde labnetwerk; een cluster heeft
        zijn eigen, zodat zijn vaste diensten en branch-labs elkaar op naam
        vinden zonder dat elk ander lab daarbij kan.
        """
        code, _, _ = await self._run_cli("network", "inspect", naam, timeout=10)
        if code != 0:
            await self._cli_ok("network", "create", "--label", _LABEL, naam,
                               timeout=20)

    async def run_dienst_container(self, *, naam: str, image: str, netwerk: str,
                                   volume: str,
                                   container_naam: Optional[str] = None,
                                   aliassen: Optional[List[str]] = None,
                                   poorten: Optional[Dict[int, int]] = None,
                                   omgeving: Optional[Dict[str, str]] = None,
                                   timeout: float = 600.0) -> str:
        """Een vaste dienst van een cluster starten (de Cosmos-emulator, ...).

        Geen lab-labels: de lab-opruiming mag hier niet aankomen. Wel
        `--restart unless-stopped`, want een vaste dienst hoort een herstart
        van die machine te overleven -- dat is het verschil met een lab, dat
        juist wegwerpbaar is.

        Poorten gaan naar 127.0.0.1 van de machine zelf. Binnen het cluster is
        dat niet nodig: daar praat alles op naam.

        **`naam` en `container_naam` zijn met opzet twee dingen.** `naam` is
        de DNS-naam op het clusternetwerk -- dat is wat een branch-lab typt
        (`cosmos:8081`), en die hoort dus per cluster hetzelfde te kunnen
        zijn. `container_naam` is hoe de container bij de daemon heet, en die
        is GLOBAAL: twee clusters met allebei een `cosmos` delen die ene naam.

        Dat liep echt mis en het liep stil mis: hieronder staat een
        `docker rm -f` op de naam, dus het starten van de emulator in het
        tweede cluster maakte die van het eerste dood en nam zijn plaats in.
        LabX bleef voor allebei "draait" melden. Vandaar: de alias per
        cluster, de containernaam uniek over alle clusters heen.
        """
        container_naam = container_naam or naam
        await self._cli_ok("volume", "create", volume, timeout=30)
        # Een naamgenoot opruimen, ook als WIJ zijn id niet kennen.
        #
        # Dat gebeurt makkelijker dan het lijkt: een `docker run` die de
        # container aanmaakt en daarna struikelt (op een poort, op het
        # netwerk) laat de naam bezet achter terwijl de rij in de database
        # nooit een container_id kreeg. De volgende poging strandt dan op
        # "The container name is already in use" -- een fout over onze eigen
        # rommel, waar de gebruiker niets aan kan doen.
        await self._run_cli("rm", "-f", container_naam, timeout=120)
        # EERST ophalen, apart. Doet `docker run` dat zelf, dan loopt de
        # voortgang van een image van anderhalve gigabyte door de uitvoer van
        # het starten heen -- en belandt die bij een time-out als
        # "foutmelding" in het scherm. Dat zag er zo uit:
        #
        #   fout: Unable to find image ... locally
        #         65899dddd4f4: Pulling fs layer
        #         ede28f09bd35: Pulling fs layer
        #
        # Niets daarvan is een fout, en niets ervan zegt wat er mis ging.
        await self._cli_ok("pull", image, timeout=max(timeout, 1800.0))
        args = [
            "run", "-d",
            "--name", container_naam,
            "--label", _DIENST_LABEL,
            "--restart", "unless-stopped",
            "--network", netwerk,
            # Meer dan één alias op het clusternetwerk.
            #
            # De dienst heet `ih-cosmos` om bij de daemon uniek te zijn, maar
            # repo's noemen hem vaak gewoon `cosmos` -- en soms met een
            # harde controle erop. Het seed-script van IntelligentHive
            # weigert te schrijven tenzij de host `localhost`, `127.0.0.1`,
            # `0.0.0.0` of `cosmos` heet; met alleen `ih-cosmos` deed het
            # niets, en dat zag eruit als een lege database zonder uitleg.
            #
            # Binnen één clusternetwerk kan dat veilig: elk cluster heeft zijn
            # eigen netwerk, dus twee keer `cosmos` botst niet.
            "--network-alias", naam,
            "-v", f"{volume}:/data",
        ]
        # {containerpoort: machinepoort}. Niet hetzelfde nummer aan beide
        # kanten: 8081 is op een gemiddelde machine allang bezet.
        for container_poort, machine_poort in (poorten or {}).items():
            args += ["-p", f"127.0.0.1:{int(machine_poort)}:{int(container_poort)}"]
        for k, v in (omgeving or {}).items():
            args += ["-e", f"{k}={v}"]
        for extra in (aliassen or []):
            if extra and extra != naam:
                args += ["--network-alias", extra]
        args += [image]
        return await self._cli_ok(*args, timeout=timeout)

    async def run_model_container(self, *, naam: str, image: str, volume: str,
                                  verre_poort: int, mem_limit_mb: int = 4096) -> str:
        """Een modelcontainer (Ollama) starten op deze daemon.

        Apart van `run_container`, want dit is geen lab: geen /workspace, geen
        TTL, geen labels waar de lab-opruiming op let. Wel dezelfde regel over
        poorten -- hij publiceert op 127.0.0.1 van ZIJN machine, zodat er
        niets op je LAN komt te staan; LabX legt er een SSH-tunnel naartoe.

        `--restart unless-stopped`: een modelcontainer hoort een herstart van
        die machine te overleven. Een lab niet (dat is wegwerpbaar), maar dit
        is gereedschap.
        """
        await self._cli_ok("volume", "create", volume, timeout=30)
        args = [
            "run", "-d",
            "--name", container_naam,
            "--label", _MODEL_LABEL,
            "--restart", "unless-stopped",
            "--memory", f"{int(mem_limit_mb)}m",
            "-p", f"127.0.0.1:{int(verre_poort)}:11434",
            "-v", f"{volume}:/root/.ollama",
            image,
        ]
        return await self._cli_ok(*args, timeout=300)

    async def list_model_containers(self) -> List[Dict[str, str]]:
        """Wat er op deze daemon aan modelcontainers staat, draaiend of niet."""
        code, out, _ = await self._run_cli(
            "ps", "-a", "--filter", f"label={_MODEL_LABEL}",
            "--format", "{{.ID}}\t{{.Names}}\t{{.State}}\t{{.Ports}}", timeout=30)
        if code != 0:
            return []
        rijen = []
        for regel in out.splitlines():
            deel = regel.split("\t")
            if len(deel) >= 3:
                rijen.append({"id": deel[0], "name": deel[1], "state": deel[2],
                              "ports": deel[3] if len(deel) > 3 else ""})
        return rijen

    async def architectuur(self) -> Optional[str]:
        """Waar deze daemon op draait: `x86_64`, `aarch64`, ...

        Nodig bij het verplaatsen. Een lab van een amd64-server naar een
        Apple Silicon-Mac tillen werkt voor het volume prima, maar niet elk
        pakket bestaat voor beide: Google Chrome heeft geen arm64-build voor
        Linux, en dat merk je anders pas als het inrichten halverwege stukloopt
        op een muur aan apt-uitvoer.
        """
        code, out, _ = await self._run_cli("info", "--format", "{{.Architecture}}",
                                           timeout=20)
        waarde = (out or "").strip()
        return waarde if code == 0 and waarde else None

    async def port_map(self, container_id: str) -> Dict[int, int]:
        """{container_port: host_port} for ports published for browser access
        from the docker host. NOT how the backend itself reaches a lab port —
        use the network_alias for that."""
        code, out, _ = await self._run_cli("port", container_id, timeout=15)
        mapping: Dict[int, int] = {}
        if code != 0:
            return mapping
        for line in out.splitlines():
            try:
                left, right = line.split("->")
                mapping[int(left.split("/")[0].strip())] = int(right.rsplit(":", 1)[1])
            except Exception:  # noqa: BLE001
                continue
        return mapping

    async def run_ephemeral(
        self,
        *,
        image: str,
        volume: Optional[str] = None,
        cmd: List[str],
        env: Optional[Dict[str, str]] = None,
        timeout: float = 300.0,
    ) -> str:
        """Short-lived helper container WITH network, on the same volume — for
        repo clones and the publish sidecar. Credentials never enter the agent
        container; the helper is removed immediately (--rm)."""
        await self.ensure_network()
        args = ["run", "--rm", "--label", _LABEL,
                "--security-opt", "no-new-privileges",
                "--network", self._network]
        # Zonder volume: een containertje dat alleen iets PROBEERT (mag deze
        # sleutel bij de repo?) hoeft nergens te schrijven, en een leeg volume
        # aanmaken om het daarna weg te gooien is werk voor niets.
        args += ["-v", f"{volume}:/workspace", "-w", "/workspace"] if volume else ["-w", "/tmp"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        args += [image, *cmd]
        return await self._cli_ok(*args, timeout=timeout)

    async def run_wegwerp(
        self,
        *,
        image: str,
        cmd: List[str],
        netwerk: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        volume: Optional[str] = None,
        volumes: Optional[Dict[str, str]] = None,
        timeout: float = 300.0,
        max_chars: int = _MAX_OUTPUT_CHARS,
    ) -> Dict[str, Any]:
        """Een wegwerpcontainer NAAST een vaste dienst, op diens netwerk.

        Twee verschillen met `run_ephemeral`, allebei nodig voor dienstacties:

        - **Het netwerk is te kiezen.** Een actie tegen de Cosmos-emulator
          moet `ih-cosmos` op naam kunnen vinden, en dat kan alleen op het
          netwerk van het cluster.
        - **Een exitcode is geen uitzondering.** De uitslag ÍS het antwoord:
          "leegmaken mislukte en dit zei hij" is wat je wilt zien, niet een
          stacktrace in de log.

        `volume` is de werkkopie en hangt op /workspace, schrijfbaar -- daar
        committeert het git-werk in. `volumes` hangt bundels erin, altijd
        alleen-lezen: een script dat een dump inleest hoort die dump niet te
        kunnen veranderen.
        """
        if netwerk is None:
            # Zonder netwerk van een cluster valt hij terug op het gedeelde
            # labnetwerk, en dat moet dan wel bestaan -- anders faalt `docker
            # run` op iets waar de aanroeper niets mee te maken heeft.
            await self.ensure_network()
        args = ["run", "--rm", "--label", _LABEL,
                "--security-opt", "no-new-privileges",
                "--network", netwerk or self._network]
        args += (["-v", f"{volume}:/workspace", "-w", "/workspace"]
                 if volume else ["-w", "/tmp"])
        for extra, pad in (volumes or {}).items():
            args += ["-v", f"{extra}:{pad}:ro"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        args += [image, *cmd]
        code, out, err = await self._run_cli(*args, timeout=timeout)
        samen = out if not err else (out + ("\n" if out else "") + err)
        afgekapt = len(samen) > max_chars
        if afgekapt:
            samen = samen[:max_chars] + "\n… [uitvoer afgekapt]"
        return {"exit_code": code, "output": samen, "truncated": afgekapt}

    async def start(self, container_id: str) -> None:
        await self._cli_ok("start", container_id)

    async def stop(self, container_id: str) -> None:
        await self._cli_ok("stop", "-t", "5", container_id)

    async def exists(self, name_or_id: str) -> bool:
        code, _, _ = await self._run_cli("inspect", "--format", "{{.Id}}", name_or_id, timeout=15)
        return code == 0

    async def wait_until_gone(self, *names: str, timeout: float = 180.0) -> bool:
        """Wacht tot deze containers écht weg zijn bij de daemon.

        `docker rm -f` is niet klaar als de CLI terugkeert: bij een container
        met een volgelopen proces-tabel duurt het opruimen daemon-zijdig door,
        en een client-side time-out maakt daar niets aan uit — die doodt alleen
        de CLI. Wie in die tussentijd een nieuwe container met dezelfde naam
        start, krijgt gegarandeerd een naamconflict. Vandaar: kijken tot het
        écht weg is, niet aannemen dat het klaar is."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if not any([await self.exists(n) for n in names if n]):
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(2)

    async def remove(self, container_id: str, *, timeout: float = 60.0) -> None:
        await self._cli_ok("rm", "-f", container_id, timeout=timeout)

    async def state(self, container_id: str) -> Optional[str]:
        code, out, _ = await self._run_cli(
            "inspect", "--format", "{{.State.Status}}", container_id, timeout=15)
        return out.strip() if code == 0 else None

    async def oom_gekild(self, container_id: str) -> bool:
        """Is deze container (of iets erin) door de OOM-killer geraakt?

        Dit is het verschil tussen "er ging iets mis in het script" en "de
        machine had het geheugen niet". Dat eerste los je op in de repo, dat
        tweede nooit -- en zonder dit onderscheid zie je alleen een lab dat
        op `running` staat en niets doet.
        """
        code, out, _ = await self._run_cli(
            "inspect", "--format", "{{.State.OOMKilled}}", container_id, timeout=15)
        return code == 0 and out.strip().lower() == "true"

    async def _bestaande(self, naam: str) -> Optional[Dict[str, str]]:
        """De container met deze naam, als hij er is. None als hij er niet is.

        Een fout bij het vragen telt als "ik weet het niet" en dus als None:
        dan loopt het op de normale weg verder en krijg je de echte
        foutmelding van `docker run` in plaats van een gok.
        """
        code, out, _ = await self._run_cli(
            "ps", "-a", "--no-trunc", "--filter", f"name=^{naam}$",
            "--format", "{{.ID}}\t{{.State}}", timeout=30)
        if code != 0 or not out.strip():
            return None
        deel = out.strip().splitlines()[0].split("\t")
        if len(deel) != 2:
            return None
        return {"id": deel[0], "state": deel[1]}

    async def list_managed(self) -> List[Dict[str, str]]:
        """All containers LabX has ever started, running or not — used to
        reconcile DB state with the daemon after a backend restart (the daemon
        keeps running independently of the LabX process).

        `--no-trunc` is load-bearing: `docker ps` truncates `{{.ID}}` to 12
        characters by default, while `run_container()` stores the FULL id
        `docker run` prints to stdout. Without it, every id lookup against
        `Lab.container_id` mismatches — reconcile marks every live lab
        "error" AND the orphan-cleanup below deletes its still-running
        container, mistaking it for an orphan."""
        # Ruim in de tijd. Dit wordt vooral gevraagd vlak na het opstarten van
        # de machine, en dan is de daemon bezig met tientallen containers
        # tegelijk: op 21-10-2026 haalde hij de 15 seconden niet en werd de
        # hele reconciliatie overgeslagen. Een trage lijst is geen fout.
        code, out, err = await self._run_cli(
            "ps", "-a", "--no-trunc", "--filter", f"label={_LABEL}",
            "--format", "{{.Names}}\t{{.ID}}\t{{.State}}", timeout=60)
        if code != 0:
            # OPSTAAN, niet een lege lijst teruggeven.
            #
            # Dit was `return []`, en dat is het gevaarlijkste antwoord dat
            # hier bestaat: de aanroeper is reconcile, en die leest "geen
            # containers" als "alle containers zijn weg". Gemeten op
            # 21-10-2026: een MacBook kwam uit slaap, ssh werkte al maar
            # Docker Desktop nog niet, `docker ps -a` gaf een fout -- en LabX
            # zette drie labs op gestopt en gooide hun container-id's weg
            # terwijl die containers gewoon draaiden. Daarna strandde elke
            # start op "The container name is already in use".
            #
            # "Ik kon het niet vragen" moet omhoog, zodat reconcile die
            # machine overslaat in plaats van hem leeg te verklaren.
            raise RuntimeError(
                (err or out).strip()[:300] or f"docker ps -a faalde ({code})")
        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                rows.append({"name": parts[0], "id": parts[1], "state": parts[2]})
        return rows

    async def stream_out(self, container_id: str, cmd: List[str], *,
                         workdir: str = "/workspace",
                         chunk: int = 256 * 1024):
        """Ruwe bytes uit een container, stuk voor stuk.

        `exec()` decodeert naar tekst en kapt af op een maximum — prima voor een
        commando-uitvoer, onbruikbaar voor een BESTAND. Een parquet of een zip
        overleeft die decodering niet, en 200 kB is geen download.

        Dit is een async generator: de bytes gaan rechtstreeks van de container
        naar de HTTP-respons zonder ooit volledig in het geheugen te staan. Een
        bestand van een gigabyte mag de backend niet omduwen.
        """
        proc = await asyncio.create_subprocess_exec(
            self._bin, "exec", "-w", workdir, container_id, *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=_docker_env(self._docker_host),
        )
        try:
            while True:
                brok = await proc.stdout.read(chunk)
                if not brok:
                    break
                yield brok
        finally:
            # Breekt de client de verbinding af, dan hoort het proces in de
            # container ook te stoppen — anders blijft er een `cat` op een groot
            # bestand staan die niemand meer leest.
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()

    async def exec(
        self,
        container_id: str,
        cmd: List[str],
        *,
        workdir: str = "/workspace",
        stdin: Optional[bytes] = None,
        timeout: float = _DEFAULT_EXEC_TIMEOUT_S,
        max_chars: int = _MAX_OUTPUT_CHARS,
    ) -> Dict[str, Any]:
        """Run a command in the container. Wrapped in an in-container `timeout`
        so a hung command doesn't outlive a client-side time-out — ND3X's
        version only kills the docker CLI, leaving the process running."""
        budget = max(1, int(timeout))
        wrapped = ["timeout", "-k", "2", str(budget), *cmd]
        args = ["exec", "-w", workdir]
        if stdin is not None:
            args.append("-i")
        args += [container_id, *wrapped]
        code, out, err = await self._run_cli(*args, stdin=stdin, timeout=timeout + 5)
        combined = out if not err else (out + ("\n" if out else "") + err)
        truncated = len(combined) > max_chars
        if truncated:
            combined = combined[:max_chars] + "\n… [output afgekapt]"
        return {"exit_code": code, "output": combined, "truncated": truncated}
