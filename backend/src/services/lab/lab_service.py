"""
services/lab/lab_service.py

Lifecycle + execution of labs. Ported from
ND3X-public/src/services/playground/playground_service.py, single-tenant
(no project/org scoping), plus:
- reconcile_on_start(): the docker daemon outlives the LabX process (it's a
  sibling, not a child), so after a backend restart we must reconcile DB rows
  against `docker ps -a --filter label=labx.managed` instead of trusting the
  DB alone (ND3X's expire_due() only ever looks at the DB and drifts).
- repos accept only {url, name?, token?} — there's no repository registry in
  LabX (POC scope), so registry-lookup is dropped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from config import settings
from models.lab import Lab
from services.lab.docker_runtime import DockerRuntime

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry_from_now(ttl_hours: int | None) -> str:
    """ttl_hours telt vanaf NU, niet vanaf het aanmaken: de TTL is 'zo lang
    ongebruikt', niet 'zo oud'."""
    hours = max(1, min(int(ttl_hours or 24), 24 * 14))
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def default_image() -> str:
    return settings.LAB_DEFAULT_IMAGE


IMAGE_PRESETS: List[Dict[str, str]] = [
    {"key": "python", "label": "Python (nieuwste 3.x)", "image": "python:3-bookworm",
     "description": "Python + pip — algemeen ontwikkelwerk, data en scripts. git + Azure CLI standaard."},
    {"key": "node", "label": "Node.js (LTS)", "image": "node:lts-bookworm",
     "description": "Node + npm — front-end en JavaScript/TypeScript-projecten. git + Azure CLI standaard."},
    {"key": "java", "label": "Java (Temurin, nieuwste)", "image": "eclipse-temurin:latest",
     "description": "OpenJDK (Eclipse Temurin) — Java/JVM-projecten. git + Azure CLI standaard."},
    {"key": "fabric", "label": "Fabric / Azure (Python)", "image": "python:3-bookworm",
     "description": "Python-basis voor Fabric/OneLake-werk met git + Azure CLI standaard."},
    {"key": "debian", "label": "Kaal Debian (stable)", "image": "debian:stable-slim",
     "description": "Minimale basis; git + Azure CLI worden standaard geprovisioneerd."},
]


class LabService:
    def __init__(self, db: Session, runtime: Optional[DockerRuntime] = None) -> None:
        self.db = db
        self.runtime = runtime or DockerRuntime()

    # ── queries ───────────────────────────────────────────────────────────────

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self.db.query(Lab).order_by(Lab.created_at.desc()).all()
        return [self._to_dict(p) for p in rows]

    def get(self, lab_id: str) -> Lab:
        p = self.db.get(Lab, lab_id)
        if not p:
            raise HTTPException(status_code=404, detail="Lab not found")
        return p

    async def docker_status(self) -> Dict[str, Any]:
        return await self.runtime.diagnose()

    # ── reconcile (sibling daemon outlives the backend process) ─────────────

    async def reconcile_on_start(self) -> int:
        """Called once at backend startup: match DB rows against the daemon's
        actual state (labels survive a LabX restart because the daemon isn't
        restarted with it). Fixes drift both ways: a lab the DB thinks is
        running but whose container is gone -> error; a labelled container the
        DB no longer knows about -> removed (orphan cleanup)."""
        try:
            managed = await self.runtime.list_managed()
        except Exception as exc:  # noqa: BLE001 — daemon not reachable yet, skip
            log.warningx("Reconcile overgeslagen (docker niet bereikbaar)", error=str(exc)[:200])
            return 0
        by_container = {m["id"]: m for m in managed}
        known_container_ids = set()
        fixed = 0
        for p in self.db.query(Lab).filter(Lab.status.in_(("running", "creating"))).all():
            known_container_ids.add(p.container_id)
            m = by_container.get(p.container_id) if p.container_id else None
            if not m:
                p.status = "error"
                p.error = "Container niet meer gevonden na herstart van LabX"
                p.updated_at = _now_iso()
                fixed += 1
                continue
            live_status = "running" if m["state"] == "running" else "stopped"
            if live_status != p.status:
                p.status = live_status
                p.updated_at = _now_iso()
                fixed += 1
        if fixed:
            self.db.commit()
        # Orphans: labelled containers with no matching DB row at all. A
        # RUNNING orphan is left alone and only logged — id-matching bugs
        # (like the truncated-vs-full-id one this reconcile shipped with
        # once already) turn "no matching row" into a false positive for
        # every live lab, and auto-deleting a running container on that
        # false signal is exactly how that bug destroyed a real lab. Only
        # stopped/exited orphans (genuinely abandoned — e.g. a Lab row whose
        # own delete-cleanup failed) are safe to remove automatically.
        db_container_ids = {c for (c,) in self.db.query(Lab.container_id).all() if c}
        for m in managed:
            if m["id"] in db_container_ids:
                continue
            if m["state"] == "running":
                log.warningx("Orphan-container draait nog — NIET automatisch verwijderd, controleer handmatig",
                             container=m["id"][:12], name=m["name"])
                continue
            try:
                await self.runtime.remove(m["id"])
                log.infox("Orphan lab-container opgeruimd", container=m["id"][:12], name=m["name"])
            except Exception as exc:  # noqa: BLE001 — best-effort
                log.warningx("Orphan-cleanup mislukt", container=m["id"][:12], error=str(exc)[:200])
        return fixed

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _resolve_repo_specs(self, repos: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        for entry in (repos or []):
            url = (entry.get("url") or "").strip()
            if not url:
                continue
            specs.append({
                "name": (entry.get("name") or url.rstrip("/").split("/")[-1]).removesuffix(".git"),
                "url": url,
                "token": (entry.get("token") or "").strip() or None,
                "source": "url",
            })
        return specs

    async def _clone_into_volume(self, p: Lab, spec: Dict[str, Any]) -> None:
        import base64 as _b64
        env = {"REPO_URL": spec["url"], "DEST": spec["name"]}
        if spec.get("token"):
            env["GIT_AUTH"] = _b64.b64encode(f"x-access-token:{spec['token']}".encode()).decode()
        cmd = ["sh", "-c",
               'git ${GIT_AUTH:+-c "http.extraHeader=AUTHORIZATION: basic $GIT_AUTH"} '
               'clone --depth 1 "$REPO_URL" "$DEST"']
        await self.runtime.run_ephemeral(image=p.image, volume=p.volume_name, cmd=cmd, env=env)

    async def _provision_base_tools(self, container_id: str) -> None:
        """Dev-workstation basics in every network-enabled lab. Best-effort:
        each step fails silently (logged) so an offline/other-distro image
        doesn't fail lab creation. Idempotent (command -v guards), so this
        also runs on every lab START — an older lab picks up newly added
        base tools on its next start without being recreated.

        This is a floor, not a ceiling: the lab is a sandbox, and the agent
        is explicitly allowed to apt/pip/npm-install anything else it needs
        (see AGENT_PREAMBLE in chat_agent.py) — the base set just saves it
        the round-trips for the things practically every task touches
        (curl for REST APIs, jq for their JSON, git, unzip)."""
        steps = [
            ("git", "command -v git >/dev/null 2>&1 || "
                    "(apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git)"),
            ("bash+curl+jq+unzip",
             "(command -v bash >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && "
             "command -v jq >/dev/null 2>&1 && command -v unzip >/dev/null 2>&1) || "
             "(apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
             "bash curl jq unzip ca-certificates)"),
        ]
        if settings.LAB_PROVISION_AZ:
            steps.append((
                "az",
                "command -v az >/dev/null 2>&1 || ("
                "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates && "
                "curl -sL https://aka.ms/InstallAzureCLIDeb | bash)"))
        for tool, script in steps:
            try:
                res = await self.runtime.exec(container_id, ["sh", "-c", script], timeout=600)
                if res.get("exit_code") == 0:
                    log.infox("Lab-provisioning ok", tool=tool, container=container_id[:12])
                else:
                    log.warningx("Lab-provisioning niet gelukt (overgeslagen)",
                                 tool=tool, exit_code=res.get("exit_code"))
            except Exception as exc:  # noqa: BLE001 — never fail create over this
                log.warningx("Lab-provisioning fout (overgeslagen)", tool=tool, error=str(exc))

    async def create(
        self,
        *,
        name: str,
        image: Optional[str] = None,
        repos: Optional[List[Dict[str, Any]]] = None,
        cpu_limit: float = 1.0,
        mem_limit_mb: int = 2048,
        allow_network: bool = True,
        ttl_hours: int = 24,
        ports: Optional[List[int]] = None,
        data_guard: bool = True,
        llm_guard: bool = True,
        allowed_mcp: Optional[List[str]] = None,
        allowed_tools: Optional[List[str]] = None,
        allowed_skills: Optional[List[str]] = None,
        environment: Optional[str] = None,
    ) -> Dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Naam is verplicht")
        diag = await self.runtime.diagnose()
        if not diag["cli_present"] or not diag["daemon_up"]:
            raise HTTPException(
                status_code=503,
                detail=f"Docker is niet beschikbaar: {diag.get('hint') or 'onbekende oorzaak'}",
            )
        if environment:
            preset = next((p for p in IMAGE_PRESETS if p["key"] == environment), None)
            if not preset:
                raise HTTPException(status_code=400, detail=f"Onbekende omgeving '{environment}'")
            image = preset["image"]
        specs = self._resolve_repo_specs(repos)

        lid = str(uuid4())
        now = _now_iso()
        ttl_hours = max(1, min(int(ttl_hours or 24), 24 * 14))
        expires = _expiry_from_now(ttl_hours)
        p = Lab(
            id=lid, name=name[:255],
            status="creating", image=(image or default_image())[:255],
            volume_name=f"labx_lab_{lid[:12]}",
            network_alias=f"labx-lab-{lid[:12]}",
            cpu_limit=float(cpu_limit or 1.0), mem_limit_mb=int(mem_limit_mb or 2048),
            allow_network=bool(allow_network), ttl_hours=ttl_hours, expires_at=expires,
            repos=[{"name": s["name"], "url": s["url"], "source": s["source"],
                    "authenticated": bool(s.get("token"))} for s in specs],
            ports=[int(x) for x in (ports or [])][:8],
            data_guard=bool(data_guard),
            llm_guard=bool(llm_guard),
            allowed_mcp=[str(x) for x in (allowed_mcp or [])],
            allowed_tools=[str(x) for x in (allowed_tools or [])],
            allowed_skills=[str(x) for x in (allowed_skills or [])],
            created_at=now, updated_at=now,
        )
        self.db.add(p)
        self.db.commit()

        try:
            if settings.LAB_PULL_ON_CREATE:
                try:
                    await self.runtime.pull(p.image)
                except Exception as exc:  # noqa: BLE001 — offline? local image will do
                    log.warningx("Image-pull overgeslagen", image=p.image, error=str(exc)[:200])
            await self.runtime.create_volume(p.volume_name)
            for spec in specs:
                await self._clone_into_volume(p, spec)
            container_id = await self.runtime.run_container(
                name=p.network_alias, image=p.image, volume=p.volume_name,
                cpu_limit=p.cpu_limit, mem_limit_mb=p.mem_limit_mb,
                allow_network=p.allow_network, ports=p.ports or [],
                expires_at=p.expires_at,
            )
            p.container_id = container_id
            p.status = "running"
            if p.allow_network:
                await self._provision_base_tools(container_id)
            await self._sync_azure_profile_into_lab(p)
            if p.llm_guard:
                try:
                    from services.lab.data_guard_llm import ensure_guard_model
                    await ensure_guard_model()
                except Exception as exc:  # noqa: BLE001
                    log.warningx("Guard-model ensure overgeslagen", error=str(exc)[:200])
        except Exception as exc:  # noqa: BLE001 — record the failure on the row
            p.status = "error"
            p.error = str(exc)[:2000]
            log.warningx("Lab aanmaken mislukt", lab_id=lid, error=str(exc))
        p.updated_at = _now_iso()
        self.db.commit()
        return self._to_dict(p)

    async def ensure_running(self, lab_id: str) -> Dict[str, Any]:
        """Zorg dat dit lab draait, en start het anders.

        Een lab dat uit staat is geen fout maar een toestand: het gaat vanzelf
        uit als er een tijd niet mee gewerkt is, en dan wil je dat de eerste die
        het weer nodig heeft het gewoon aanzet. Zonder dit strandt een agent op
        "lab draait niet" bij een handeling die hij prima had kunnen doen.
        """
        p = self.get(lab_id)
        if p.status == "running" and p.container_id:
            return {"ok": True, "started": False, "status": p.status}
        if not p.container_id:
            raise HTTPException(status_code=409, detail=(
                f"Lab '{p.name}' heeft geen container (status {p.status}) — "
                "hij moet opnieuw aangemaakt worden."))
        was = p.status
        await self.start(lab_id)
        log.infox("Lab automatisch gestart", lab_id=lab_id, vorige_status=was)
        return {"ok": True, "started": True, "status": self.get(lab_id).status}

    async def start(self, lab_id: str) -> Dict[str, Any]:
        p = self.get(lab_id)
        if p.status == "running":
            return self._to_dict(p)
        if not p.container_id:
            raise HTTPException(status_code=409, detail="Lab heeft geen container (status error?)")
        await self.runtime.start(p.container_id)
        p.status = "running"
        p.updated_at = p.last_used_at = _now_iso()
        # Starten is gebruik: een verlopen lab dat je weer aanzet moet niet bij
        # de eerstvolgende reaper-tick meteen weer omvallen.
        p.expires_at = _expiry_from_now(p.ttl_hours)
        self.db.commit()
        if p.allow_network:
            # Idempotent (command -v guards): near-instant when everything is
            # already there, and an older lab picks up newly added base tools.
            try:
                await self._provision_base_tools(p.container_id)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Lab-provisioning bij start overgeslagen", error=str(exc)[:200])
        await self._sync_azure_profile_into_lab(p)
        return self._to_dict(p)

    async def _sync_azure_profile_into_lab(self, p: Lab) -> None:
        """If the lab has an assigned Azure profile (msal_bundle), push its
        az-session files into the container so in-lab `az account
        get-access-token` works — that's what lets the agent call Fabric/
        Azure REST APIs from inside the sandbox with curl, not only through
        the host-side MCP servers. Best-effort: a failed sync must never
        block a lab start."""
        if not p.azure_profile_id:
            return
        try:
            from models.azure_profile import AzureProfile
            from services.azure.azure_profile_service import AzureProfileService
            svc = AzureProfileService(self.db)
            profile = self.db.get(AzureProfile, p.azure_profile_id)
            if profile is None or profile.kind != "msal_bundle":
                return
            res = await svc.sync(p.azure_profile_id, target="lab", lab_id=p.id)
            log.infox("Azure-profiel in lab gesynct", lab_id=p.id, ok=bool(res.get("ok")))
        except Exception as exc:  # noqa: BLE001
            log.warningx("Azure-profiel sync naar lab overgeslagen", lab_id=p.id, error=str(exc)[:200])

    async def update_settings(self, lab_id: str, *,
                              data_guard: Optional[bool] = None,
                              llm_guard: Optional[bool] = None,
                              allowed_mcp: Optional[List[str]] = None,
                              allowed_tools: Optional[List[str]] = None,
                              allowed_skills: Optional[List[str]] = None,
                              azure_profile_id: Any = "__unset__") -> Dict[str, Any]:
        p = self.get(lab_id)
        if data_guard is not None:
            p.data_guard = bool(data_guard)
        if llm_guard is not None:
            p.llm_guard = bool(llm_guard)
        if allowed_mcp is not None:
            p.allowed_mcp = [str(x) for x in allowed_mcp]
        if allowed_tools is not None:
            p.allowed_tools = [str(x) for x in allowed_tools]
        if allowed_skills is not None:
            p.allowed_skills = [str(x) for x in allowed_skills]
        profile_changed = False
        if azure_profile_id != "__unset__":
            profile_changed = p.azure_profile_id != azure_profile_id
            p.azure_profile_id = azure_profile_id
        p.updated_at = _now_iso()
        self.db.commit()
        if profile_changed and p.azure_profile_id and p.status == "running":
            await self._sync_azure_profile_into_lab(p)
        if llm_guard:
            try:
                from services.lab.data_guard_llm import ensure_guard_model
                await ensure_guard_model()
            except Exception as exc:  # noqa: BLE001
                log.warningx("Guard-model ensure overgeslagen (update)", error=str(exc)[:200])
        return self._to_dict(p)

    async def stop(self, lab_id: str) -> Dict[str, Any]:
        p = self.get(lab_id)
        if p.container_id and p.status == "running":
            await self.runtime.stop(p.container_id)
        p.status = "stopped"
        p.updated_at = _now_iso()
        self.db.commit()
        return self._to_dict(p)

    async def delete(self, lab_id: str) -> Dict[str, Any]:
        p = self.get(lab_id)
        for op, ref in (("container", p.container_id), ("volume", p.volume_name)):
            if not ref:
                continue
            try:
                if op == "container":
                    await self.runtime.remove(ref)
                else:
                    await self.runtime.remove_volume(ref)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                log.warningx("Lab-cleanup deels mislukt", lab_id=p.id, resource=op, error=str(exc))
        from models.thread import Thread
        # A thread's lab_id is NOT NULL by design (chat requires a lab) — so
        # deleting a bound lab cascades to its threads rather than orphaning them.
        self.db.query(Thread).filter(Thread.lab_id == p.id).delete(synchronize_session=False)
        self.db.delete(p)
        self.db.commit()
        return {"ok": True}

    async def expire_due(self) -> int:
        now = _now_iso()
        due = (self.db.query(Lab)
               .filter(Lab.expires_at.isnot(None),
                       Lab.expires_at < now,
                       Lab.status.in_(("running", "stopped", "creating")))
               .all())
        for p in due:
            try:
                if p.container_id and p.status == "running":
                    await self.runtime.stop(p.container_id)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Lab stoppen bij expiry mislukt", lab_id=p.id, error=str(exc))
            p.status = "expired"
            p.updated_at = _now_iso()
        if due:
            self.db.commit()
            log.infox("Labs verlopen", count=len(due))
        return len(due)

    # ── execution & files ─────────────────────────────────────────────────────

    def _require_running(self, p: Lab) -> str:
        if p.status != "running" or not p.container_id:
            raise HTTPException(status_code=409, detail="Lab draait niet (start hem eerst)")
        return p.container_id

    def _touch(self, p: Lab) -> None:
        """Gebruik schuift de vervaltijd vooruit.

        De TTL was een harde leeftijdsgrens: expires_at werd bij het AANMAKEN
        gezet en daarna nooit meer aangeraakt, dus elk lab ging precies
        ttl_hours na zijn geboorte op "expired" — hoe intensief je het ook
        gebruikte. Erger nog: startte je het daarna weer, dan zette de reaper
        het binnen een tick opnieuw uit, want expires_at lag nog steeds in het
        verleden. Een lab opruimen dat je niet meer gebruikt is de bedoeling;
        een lab opruimen dat je wél gebruikt niet."""
        p.last_used_at = _now_iso()
        p.expires_at = _expiry_from_now(p.ttl_hours)
        self.db.commit()

    def mark_used(self, lab_id: str) -> None:
        """Gebruik dat niet via exec of bestanden loopt — een chatbeurt of een
        achtergrondrun in dit lab — telt net zo goed mee. Best-effort."""
        p = self.db.get(Lab, lab_id)
        if p is not None:
            self._touch(p)

    @staticmethod
    def _safe_path(path: str) -> str:
        clean = "/" + (path or "").strip().lstrip("/")
        candidate = clean if clean.startswith("/workspace") else f"/workspace{clean}"
        if ".." in candidate.split("/"):
            raise HTTPException(status_code=400, detail="Ongeldig pad")
        return candidate.rstrip("/") or "/workspace"

    async def exec_command(self, lab_id: str, command: str, *, timeout: float = 120.0) -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p)
        if not (command or "").strip():
            raise HTTPException(status_code=400, detail="Leeg commando")
        # The exec tool is documented as BASH ("Voer een bash-commando uit").
        # Running it under plain `sh` made that a lie with real consequences:
        # a `[[ ]]` condition crashed mid-script and dumped an intermediate
        # variable (a live OAuth bearer token) into the tool output. Prefer
        # bash when the image has it (provisioned as a base tool), fall back
        # to sh only when it genuinely doesn't ($0 carries the command).
        wrapper = ('if command -v bash >/dev/null 2>&1; then exec bash -c "$0"; '
                  'else exec sh -c "$0"; fi')
        result = await self.runtime.exec(cid, ["sh", "-lc", wrapper, command],
                                         timeout=max(5.0, min(timeout, 600.0)))
        self._touch(p)
        return result

    async def list_files(self, lab_id: str, path: str = "/workspace") -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p)
        target = self._safe_path(path)
        result = await self.runtime.exec(cid, ["ls", "-Ap", "--", target], timeout=20)
        if result["exit_code"] != 0:
            raise HTTPException(status_code=404, detail=result["output"][:300])
        entries = []
        for line in result["output"].splitlines():
            n = line.strip()
            if not n:
                continue
            entries.append({"name": n.rstrip("/"), "is_dir": n.endswith("/")})
        return {"path": target, "entries": entries}

    async def read_file(self, lab_id: str, path: str) -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p)
        target = self._safe_path(path)
        result = await self.runtime.exec(cid, ["head", "-c", "200000", "--", target], timeout=30)
        if result["exit_code"] != 0:
            raise HTTPException(status_code=404, detail=result["output"][:300])
        return {"path": target, "content": result["output"], "truncated": result["truncated"]}

    async def write_file(self, lab_id: str, path: str, content: str) -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p)
        target = self._safe_path(path)
        parent = target.rsplit("/", 1)[0] or "/workspace"
        result = await self.runtime.exec(
            cid, ["sh", "-c", f"mkdir -p '{parent}' && cat > '{target}'"],
            stdin=(content or "").encode("utf-8"), timeout=30)
        if result["exit_code"] != 0:
            raise HTTPException(status_code=500, detail=result["output"][:300])
        self._touch(p)
        return {"ok": True, "path": target, "bytes": len((content or "").encode("utf-8"))}

    async def az_login(self, lab_id: str, *, az_dir: str = "/root/.azure",
                       files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p)
        payload: Dict[str, str] = {}
        if files:
            for fname in ("msal_token_cache.json", "azureProfile.json", "service_principal_entries.json"):
                if files.get(fname):
                    payload[fname] = files[fname]
        if not payload.get("msal_token_cache.json") or not payload.get("azureProfile.json"):
            raise HTTPException(
                status_code=400,
                detail="Geen az-sessie meegegeven: sync eerst een Azure-profiel naar dit lab.")
        safe_dir = az_dir if az_dir.startswith("/") and ".." not in az_dir.split("/") else "/root/.azure"
        await self.runtime.exec(cid, ["sh", "-c", f"mkdir -p '{safe_dir}'"], timeout=15)
        written = []
        for fname, content in payload.items():
            res = await self.runtime.exec(
                cid, ["sh", "-c", f"cat > '{safe_dir}/{fname}'"],
                stdin=content.encode("utf-8"), timeout=20)
            if res["exit_code"] == 0:
                written.append(fname)
        self._touch(p)
        return {"ok": bool(written), "az_dir": safe_dir, "written": written}

    async def publish(self, lab_id: str, *, repo_name: str, branch: Optional[str] = None,
                      message: Optional[str] = None, token: Optional[str] = None,
                      remote_url: Optional[str] = None) -> Dict[str, Any]:
        import base64 as _b64
        import re as _re
        p = self.get(lab_id)
        name = (repo_name or "").strip().strip("/")
        if not name or not _re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise HTTPException(status_code=400, detail="Ongeldige repo-naam")
        env: Dict[str, str] = {"DEST": name}
        if branch and branch.strip():
            if not _re.fullmatch(r"[A-Za-z0-9._/-]+", branch.strip()):
                raise HTTPException(status_code=400, detail="Ongeldige branchnaam")
            env["BRANCH"] = branch.strip()
        if message and message.strip():
            env["COMMIT_MSG"] = message.strip()[:500]
        if remote_url and remote_url.strip():
            env["PUSH_URL"] = remote_url.strip()
        if token:
            env["GIT_AUTH"] = _b64.b64encode(f"x-access-token:{token}".encode()).decode()
        script = (
            'set -e; cd "/workspace/$DEST"; '
            'git config user.email "lab@labx.local"; '
            'git config user.name "LabX"; '
            'if [ -n "$COMMIT_MSG" ]; then git add -A; '
            '  git diff --cached --quiet || git commit -m "$COMMIT_MSG"; fi; '
            'git ${GIT_AUTH:+-c "http.extraHeader=AUTHORIZATION: basic $GIT_AUTH"} '
            'push "${PUSH_URL:-origin}" "HEAD:${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"'
        )
        try:
            out = await self.runtime.run_ephemeral(
                image=p.image, volume=p.volume_name, cmd=["sh", "-c", script], env=env, timeout=300)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Push mislukt: {str(exc)[:500]}")
        self._touch(p)
        return {"ok": True, "repo": name, "branch": env.get("BRANCH"), "output": (out or "")[:2000]}

    # ── serialization ─────────────────────────────────────────────────────────

    async def detail(self, lab_id: str) -> Dict[str, Any]:
        p = self.get(lab_id)
        out = self._to_dict(p)
        if p.status == "running" and p.container_id and (p.ports or []):
            try:
                out["port_map"] = await self.runtime.port_map(p.container_id)
            except Exception:  # noqa: BLE001
                out["port_map"] = {}
        return out

    def _to_dict(self, p: Lab) -> Dict[str, Any]:
        return {
            "id": p.id, "name": p.name,
            "status": p.status, "image": p.image,
            "network_alias": p.network_alias,
            "cpu_limit": p.cpu_limit, "mem_limit_mb": p.mem_limit_mb,
            "allow_network": bool(p.allow_network),
            "ttl_hours": p.ttl_hours, "expires_at": p.expires_at,
            "repos": p.repos or [], "ports": p.ports or [],
            "data_guard": bool(getattr(p, "data_guard", True)),
            "llm_guard": bool(getattr(p, "llm_guard", True)),
            "allowed_mcp": list(getattr(p, "allowed_mcp", None) or []),
            "allowed_tools": list(getattr(p, "allowed_tools", None) or []),
            "allowed_skills": list(getattr(p, "allowed_skills", None) or []),
            "azure_profile_id": p.azure_profile_id,
            "error": p.error,
            "created_at": p.created_at, "updated_at": p.updated_at,
            "last_used_at": p.last_used_at,
        }
