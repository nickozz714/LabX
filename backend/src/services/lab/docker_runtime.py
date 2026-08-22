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
from typing import Any, Dict, List, Optional, Tuple

from component_logging import get_logger
from config import settings

log = get_logger(__name__)

_LABEL = "labx.managed"
_LAB_LABEL = "labx.lab"
_EXPIRES_LABEL = "labx.expires_at"
_DEFAULT_EXEC_TIMEOUT_S = 120
_MAX_OUTPUT_CHARS = 60_000


def _docker_env() -> Dict[str, str]:
    env = dict(os.environ)
    if settings.DOCKER_HOST:
        env["DOCKER_HOST"] = settings.DOCKER_HOST
    return env


class DockerRuntime:
    """Container lifecycle + exec for labs, against the shared (sibling) daemon."""

    def __init__(self, docker_bin: Optional[str] = None) -> None:
        self._bin = docker_bin or settings.DOCKER_BIN
        self._network = settings.LAB_NETWORK

    async def _run_cli(
        self, *args: str, stdin: Optional[bytes] = None, timeout: float = 60.0,
    ) -> Tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            self._bin, *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_env(),
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
        try:
            code, _, _ = await self._run_cli("info", "--format", "{{.ServerVersion}}", timeout=10)
            return code == 0
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
        elif not daemon_ok:
            hint = "docker info faalt — draait de daemon, en heeft dit proces toegang tot de socket?"
        return {
            "cli_present": cli_present,
            "daemon_up": daemon_ok,
            "in_container": in_container,
            "socket_mounted": socket_mounted,
            "docker_host": settings.DOCKER_HOST,
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
    ) -> str:
        """Start the lab container: idle process, /workspace on the volume,
        caps + no-new-privileges, NO host-mounts, on the shared lab network with
        a DNS alias == its container name. Returns the container id."""
        await self.ensure_network()
        args = [
            "run", "-d",
            "--name", name,
            "--label", _LABEL,
            "--label", f"{_LAB_LABEL}={name}",
            "--restart", "no",  # don't outlive the host past its TTL after a reboot
            "--cpus", str(cpu_limit),
            "--memory", f"{int(mem_limit_mb)}m",
            "--pids-limit", "512",
            "--security-opt", "no-new-privileges",
            "--network", self._network,
            "--network-alias", name,
            "-v", f"{volume}:/workspace",
            "-w", "/workspace",
        ]
        if expires_at:
            args += ["--label", f"{_EXPIRES_LABEL}={expires_at}"]
        for port in (ports or []):
            args += ["-p", f"127.0.0.1:0:{int(port)}"]
        args += [image, "sleep", "infinity"]
        return await self._cli_ok(*args, timeout=180)

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
        volume: str,
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
                "--network", self._network,
                "-v", f"{volume}:/workspace", "-w", "/workspace"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        args += [image, *cmd]
        return await self._cli_ok(*args, timeout=timeout)

    async def start(self, container_id: str) -> None:
        await self._cli_ok("start", container_id)

    async def stop(self, container_id: str) -> None:
        await self._cli_ok("stop", "-t", "5", container_id)

    async def remove(self, container_id: str) -> None:
        await self._cli_ok("rm", "-f", container_id)

    async def state(self, container_id: str) -> Optional[str]:
        code, out, _ = await self._run_cli(
            "inspect", "--format", "{{.State.Status}}", container_id, timeout=15)
        return out.strip() if code == 0 else None

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
        code, out, _ = await self._run_cli(
            "ps", "-a", "--no-trunc", "--filter", f"label={_LABEL}",
            "--format", "{{.Names}}\t{{.ID}}\t{{.State}}", timeout=15)
        if code != 0:
            return []
        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                rows.append({"name": parts[0], "id": parts[1], "state": parts[2]})
        return rows

    async def exec(
        self,
        container_id: str,
        cmd: List[str],
        *,
        workdir: str = "/workspace",
        stdin: Optional[bytes] = None,
        timeout: float = _DEFAULT_EXEC_TIMEOUT_S,
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
        truncated = len(combined) > _MAX_OUTPUT_CHARS
        if truncated:
            combined = combined[:_MAX_OUTPUT_CHARS] + "\n… [output afgekapt]"
        return {"exit_code": code, "output": combined, "truncated": truncated}
