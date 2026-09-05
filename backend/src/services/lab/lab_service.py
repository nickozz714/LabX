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

# Dev-workstation-basis die in ELK lab met netwerk komt, ongeacht wat er verder
# is aangevinkt: het spul waar praktisch elke taak tegenaan loopt (curl voor
# REST, jq voor de JSON, git, unzip). Zelfde vorm als een lab-extra — een
# check-commando dat "staat er al" betekent, plus een installatie — zodat één
# runner beide afhandelt en alles idempotent blijft.
_BASE_STEPS: List[Dict[str, Any]] = [
    {"key": "git", "label": "git",
     "check": "command -v git >/dev/null 2>&1",
     "script": "apt-get update -qq && "
               "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git",
     "timeout_s": 600},
    {"key": "base-tools", "label": "bash, curl, jq, unzip",
     "check": "command -v bash >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 && "
              "command -v jq >/dev/null 2>&1 && command -v unzip >/dev/null 2>&1",
     "script": "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
               "bash curl jq unzip ca-certificates",
     "timeout_s": 600},
]

_AZ_STEP: Dict[str, Any] = {
    "key": "az", "label": "Azure CLI",
    "check": "command -v az >/dev/null 2>&1",
    "script": "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
              "curl ca-certificates && curl -sL https://aka.ms/InstallAzureCLIDeb | bash",
    "timeout_s": 900,
}


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

    # ── inrichten: basis + aangevinkte extra's + eigen setup-script ─────────

    def _extra_steps(self, p: Lab) -> List[Dict[str, Any]]:
        """De aangevinkte extra's als stappen, met hun `requires` ervóór.

        De volgorde is die van de catalogus, met afhankelijkheden ervoor:
        Playwright voor Node heeft Node nodig, en die moet er dus eerst zijn —
        ook als het lab alleen Playwright heeft aangevinkt."""
        keys = [str(k) for k in (getattr(p, "extras", None) or [])]
        if not keys:
            return []
        from models.lab_extra import LabExtra
        rows = self.db.query(LabExtra).filter(LabExtra.is_enabled.is_(True)).all()
        by_key = {r.key: r for r in rows}

        # Eerst de hele verzameling uitklappen (aangevinkt + alles waar dat op
        # leunt), en die pas dáárna op catalogusvolgorde zetten. Andersom gaat
        # het mis: een meegetrokken pakket zou dan pas aan de beurt zijn op de
        # plek van degene die het nodig had, en zo belandde "echte Chrome" vóór
        # de Playwright-installatie waar het zijn kanaal aan toevoegt.
        closure: Dict[str, Any] = {}

        def expand(key: str, chain: frozenset) -> None:
            row = by_key.get(key)
            if row is None or key in closure or key in chain:
                return
            closure[key] = row
            for dep in (row.requires or []):
                expand(str(dep), chain | {key})

        for key in keys:
            expand(key, frozenset())

        ordered: List[Any] = []
        taken: set[str] = set()

        def add(row: Any, chain: frozenset) -> None:
            """Catalogusvolgorde, behalve waar `requires` iets anders zegt: een
            eigen pakket met een verkeerde sorteervolgorde mag zijn eigen
            afhankelijkheid niet inhalen. `chain` vangt een kringetje af."""
            if row.key in taken or row.key in chain:
                return
            for dep in (row.requires or []):
                dep_row = closure.get(str(dep))
                if dep_row is not None:
                    add(dep_row, chain | {row.key})
            taken.add(row.key)
            ordered.append(row)

        for row in sorted(closure.values(), key=lambda r: (r.sort_order, r.id)):
            add(row, frozenset())

        steps: List[Dict[str, Any]] = [{
            "key": r.key, "label": r.label, "check": r.check_cmd,
            "script": r.install_script, "timeout_s": int(r.timeout_s or 900),
            "mcp_server": getattr(r, "mcp_server", None),
        } for r in ordered]
        # Een lab kan verwijzen naar een pakket dat sindsdien weg is of uit
        # staat. Dat stil overslaan is precies het soort verdwijnende
        # installatie waar dit scherm voor bestaat — dus benoemen.
        for key in keys:
            if key not in by_key:
                steps.append({"key": key, "label": key, "status": "skipped",
                              "output": "Dit pakket bestaat niet meer of staat uit."})
        return steps

    async def _run_provision_step(self, container_id: str, step: Dict[str, Any], *,
                                  force: bool) -> Dict[str, Any]:
        key = str(step.get("key"))
        label = str(step.get("label") or key)
        if step.get("status"):  # al beslist (onbekend pakket)
            return {"key": key, "label": label, "status": step["status"],
                    "exit_code": None, "output": step.get("output") or ""}
        check = step.get("check")
        if check and not force:
            try:
                res = await self.runtime.exec(container_id, ["sh", "-c", check], timeout=60)
                if res.get("exit_code") == 0:
                    return {"key": key, "label": label, "status": "skipped",
                            "exit_code": 0, "output": "Stond er al."}
            except Exception as exc:  # noqa: BLE001 — check mislukt = gewoon installeren
                log.warningx("Controle van lab-pakket mislukt", pakket=key, error=str(exc)[:200])
        timeout = int(step.get("timeout_s") or 900)
        try:
            res = await self.runtime.exec(
                container_id, ["sh", "-c", str(step.get("script") or "")], timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return {"key": key, "label": label, "status": "error",
                    "exit_code": None, "output": str(exc)[:2000]}
        ok = res.get("exit_code") == 0
        if not ok:
            log.warningx("Lab-pakket installeren mislukt", pakket=key,
                         exit_code=res.get("exit_code"), container=container_id[:12])
        return {"key": key, "label": label, "status": "ok" if ok else "error",
                "exit_code": res.get("exit_code"),
                # De staart is waar de fout staat; de kop is apt-ruis.
                "output": (res.get("output") or "")[-3000:]}

    async def _register_lab_mcp_server(self, p: Lab, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Een pakket dat een MCP-server meebrengt, koppelt zichzelf.

        Anders blijft er precies één handmatige stap over die niemand kan
        raden: de software staat in het lab, maar de agent ziet geen enkele
        tool — want de gateway leest uit Tool-rijen, en die vult alleen een
        sync. Erger nog, zolang de host-variant van dezelfde server op de
        allowlist staat pakt díe de aanroepen op, vanuit een container zonder
        browser: een foutmelding die zegt dat de browser ontbreekt terwijl hij
        aantoonbaar in het lab staat. Vandaar dat `replaces` hem hier weghaalt.

        Registreren, toestaan en synchroniseren horen bij elkaar; alle drie of
        geen van drieën."""
        from models.mcp_server import MCPServer
        from services.mcp.mcp_client import sync_tools

        slug = str(cfg.get("slug") or "").strip().lower()
        command = str(cfg.get("command") or "").strip()
        if not slug or not command:
            return {"status": "skipped", "output": "Geen slug/commando in de serverkoppeling."}
        now = _now_iso()
        srv = self.db.query(MCPServer).filter(MCPServer.slug == slug).one_or_none()
        if srv is None:
            srv = MCPServer(
                name=str(cfg.get("name") or slug)[:255], slug=slug,
                description=cfg.get("description"), server_type="stdio", location="lab",
                stdio_command=command, is_enabled=True, created_at=now, updated_at=now)
            self.db.add(srv)
        else:
            # Een bestaande rij is misschien door de gebruiker aangepast; alleen
            # bijwerken wat nodig is om hem te laten werken.
            srv.location = "lab"
            srv.server_type = "stdio"
            srv.is_enabled = True
            if not (srv.stdio_command or "").strip():
                srv.stdio_command = command
            srv.updated_at = now
        allowed = [str(x) for x in (p.allowed_mcp or [])]
        weg = {str(x).strip().lower() for x in (cfg.get("replaces") or [])}
        verwijderd = [x for x in allowed if x.strip().lower() in weg]
        allowed = [x for x in allowed if x.strip().lower() not in weg]
        if slug not in {x.strip().lower() for x in allowed}:
            allowed.append(slug)
        p.allowed_mcp = allowed
        self.db.commit()
        res = await sync_tools(srv, lab_container_id=p.container_id)
        note = f"Gekoppeld als '{slug}' en toegestaan in dit lab."
        if verwijderd:
            note += f" Van de allowlist gehaald omdat deze server hem vervangt: {', '.join(verwijderd)}."
        if res.get("ok"):
            return {"status": "ok", "output": f"{note} {res.get('tool_count', 0)} tools opgehaald."}
        return {"status": "error", "output": f"{note} Tools ophalen mislukt: {res.get('error')}"}

    async def provision(self, lab_id: str, *, force: bool = False) -> Dict[str, Any]:
        """Het lab inrichten: basisgereedschap, de aangevinkte extra's en het
        eigen setup-script.

        Idempotent — elke stap heeft een check-commando en wordt overgeslagen
        als hij al gedaan is. Daarom draait dit ook bij elke START van een lab:
        een bestaand lab pikt een net aangevinkt (of net toegevoegd) pakket op
        zonder opnieuw aangemaakt te hoeven worden, en kost het verder niets.
        `force=True` slaat de checks over — voor "opnieuw installeren" nadat er
        iets misging.

        Best-effort per stap: een pakket dat niet installeert (verkeerde
        distributie, netwerk weg, typefout in een eigen script) laat de rest
        gewoon doorgaan en landt als fout in provision_log. Inrichten mag nooit
        een lab kapotmaken dat verder prima werkt."""
        p = self.get(lab_id)
        if p.status != "running" or not p.container_id:
            return {"ok": False, "status": p.provision_status, "reason": "Lab draait niet"}
        if not p.allow_network:
            p.provision_status = "skipped"
            p.provision_log = [{
                "key": "network", "label": "Netwerktoegang", "status": "skipped",
                "output": "Dit lab heeft geen netwerk — er valt niets binnen te halen."}]
            p.updated_at = _now_iso()
            self.db.commit()
            return {"ok": True, "status": "skipped", "steps": list(p.provision_log)}

        steps: List[Dict[str, Any]] = list(_BASE_STEPS)
        if settings.LAB_PROVISION_AZ:
            steps.append(_AZ_STEP)
        steps += self._extra_steps(p)
        script = (p.setup_script or "").strip()
        if script:
            # Geen check: een eigen script hoort zelf idempotent te zijn, en wat
            # het klaar-zijn ervan betekent weet alleen de schrijver.
            steps.append({"key": "setup-script", "label": "Eigen setup-script",
                          "check": None, "script": script, "timeout_s": 1800})

        p.provision_status = "running"
        p.provision_log = []
        p.updated_at = _now_iso()
        self.db.commit()

        entries: List[Dict[str, Any]] = []
        failed = 0
        for step in steps:
            entry = await self._run_provision_step(p.container_id, step, force=force)
            if entry["status"] == "error":
                failed += 1
            entries.append(entry)
            # "skipped" telt hier net zo goed als "ok": de software staat er, en
            # de koppeling kan ontbreken (nieuw lab, of een lab van vóór deze
            # functie). Registreren is idempotent.
            cfg = step.get("mcp_server")
            if cfg and entry["status"] in ("ok", "skipped"):
                try:
                    res = await self._register_lab_mcp_server(p, cfg)
                except Exception as exc:  # noqa: BLE001
                    res = {"status": "error", "output": str(exc)[:1000]}
                if res["status"] == "error":
                    failed += 1
                entries.append({"key": f"{step['key']}:mcp", "label": "MCP-server koppelen",
                                "status": res["status"], "exit_code": None,
                                "output": res["output"]})
            # Na elke stap wegschrijven: het scherm volgt provision_log live, en
            # bij een herstart middenin is te zien hoe ver het gekomen was.
            p.provision_log = list(entries)
            p.updated_at = _now_iso()
            self.db.commit()
        p.provision_status = "error" if failed else "ok"
        p.updated_at = _now_iso()
        self.db.commit()
        log.infox("Lab ingericht", lab_id=p.id, stappen=len(entries), mislukt=failed)
        return {"ok": failed == 0, "status": p.provision_status, "steps": entries}

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
        extras: Optional[List[str]] = None,
        setup_script: Optional[str] = None,
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
        # Een expliciet image wint van een preset: het create-scherm biedt
        # "eigen image" naast de lijst, en dan is dát wat de gebruiker bedoelt.
        if environment and not (image or "").strip():
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
            extras=[str(x) for x in (extras or [])],
            setup_script=(setup_script or "").strip() or None,
            provision_status="pending" if allow_network else "skipped",
            provision_log=[],
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
        # Het inrichten loopt erachteraan (zie provision_in_background): een
        # browser binnenhalen duurt minuten en dit antwoord mag daar niet op
        # wachten — het lab bestaat en draait al.
        if p.status == "running" and p.allow_network:
            provision_in_background(p.id)
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
            # Idempotent (elke stap heeft een check): bijna gratis als alles er
            # al staat, en een ouder lab pikt zo een net aangevinkt pakket op.
            # Op de achtergrond, want een agent die `ensure_running` aanroept
            # mag niet minuten stilstaan voor een installatie.
            provision_in_background(p.id)
        await self._sync_azure_profile_into_lab(p)
        return self._to_dict(p)

    async def rebuild(self, lab_id: str, *, image: Optional[str] = None,
                      pull: bool = True) -> Dict[str, Any]:
        """Het lab opnieuw opbouwen, eventueel op een ander image.

        Dit is de énige manier om het image van een BESTAAND lab te wijzigen of
        bij te werken: een container krijgt zijn image bij het aanmaken mee en
        houdt dat tot hij weg is. Dus: container weg, nieuwe container op
        hetzelfde volume.

        Wat blijft: /workspace (eigen volume), de naam, de instellingen, de
        allowlist, de poorten en de aangevinkte pakketten. Wat weg is: alles
        wat in de containerlaag zat — handmatig geïnstalleerde spullen buiten
        /workspace incluis. Precies daarom zijn die pakketten een lijst op het
        lab en geen apt-geschiedenis in iemands hoofd: na het opbouwen zet
        `provision` ze automatisch terug.

        Bij hetzelfde image betekent dit "haal de nieuwste versie van dit tag
        op" — dat is wat bijwerken hier ís."""
        p = self.get(lab_id)
        target = (image or p.image or default_image()).strip()[:255]
        if not target:
            raise HTTPException(status_code=400, detail="Geen image opgegeven")
        diag = await self.runtime.diagnose()
        if not diag["cli_present"] or not diag["daemon_up"]:
            raise HTTPException(status_code=503,
                                detail=f"Docker is niet beschikbaar: {diag.get('hint') or 'onbekende oorzaak'}")
        old_container = p.container_id
        p.status = "creating"
        p.error = None
        p.updated_at = _now_iso()
        self.db.commit()
        try:
            if old_container:
                # rm -f stopt hem ook; een mislukte verwijdering laat de naam
                # bezet en de volgende stap valt daar hoorbaar over.
                try:
                    await self.runtime.remove(old_container)
                except Exception as exc:  # noqa: BLE001
                    log.warningx("Oude container verwijderen mislukt", lab_id=p.id,
                                 error=str(exc)[:200])
            if pull:
                try:
                    await self.runtime.pull(target)
                except Exception as exc:  # noqa: BLE001 — offline? lokaal image doet het ook
                    log.warningx("Image-pull overgeslagen", image=target, error=str(exc)[:200])
            if not p.volume_name:
                p.volume_name = f"labx_lab_{p.id[:12]}"
            if not p.network_alias:
                p.network_alias = f"labx-lab-{p.id[:12]}"
            await self.runtime.create_volume(p.volume_name)  # bestaat al = niets aan de hand
            container_id = await self.runtime.run_container(
                name=p.network_alias, image=target, volume=p.volume_name,
                cpu_limit=p.cpu_limit, mem_limit_mb=p.mem_limit_mb,
                allow_network=p.allow_network, ports=p.ports or [],
                expires_at=p.expires_at,
            )
        except Exception as exc:  # noqa: BLE001
            p.status = "error"
            p.error = f"Opnieuw opbouwen mislukt: {str(exc)[:1900]}"
            p.updated_at = _now_iso()
            self.db.commit()
            log.warningx("Lab opnieuw opbouwen mislukt", lab_id=p.id, error=str(exc)[:300])
            raise HTTPException(status_code=502, detail=p.error)
        p.container_id = container_id
        p.image = target
        p.status = "running"
        p.provision_status = "pending" if p.allow_network else "skipped"
        p.provision_log = []
        p.expires_at = _expiry_from_now(p.ttl_hours)
        p.updated_at = p.last_used_at = _now_iso()
        self.db.commit()
        log.infox("Lab opnieuw opgebouwd", lab_id=p.id, image=target)
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
                              extras: Optional[List[str]] = None,
                              setup_script: Any = "__unset__",
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
        inrichting_changed = False
        if extras is not None:
            new_extras = [str(x) for x in extras]
            inrichting_changed = new_extras != list(p.extras or [])
            p.extras = new_extras
        if setup_script != "__unset__":
            new_script = (setup_script or "").strip() or None
            inrichting_changed = inrichting_changed or new_script != (p.setup_script or None)
            p.setup_script = new_script
        profile_changed = False
        if azure_profile_id != "__unset__":
            profile_changed = p.azure_profile_id != azure_profile_id
            p.azure_profile_id = azure_profile_id
        p.updated_at = _now_iso()
        self.db.commit()
        if profile_changed and p.azure_profile_id and p.status == "running":
            await self._sync_azure_profile_into_lab(p)
        # Een pakket erbij vinken betekent: installeer het ook echt, nu, in dit
        # lab — niet pas bij de volgende start.
        if inrichting_changed and p.status == "running" and p.allow_network:
            p.provision_status = "pending"
            self.db.commit()
            provision_in_background(p.id)
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
            "extras": list(getattr(p, "extras", None) or []),
            "setup_script": getattr(p, "setup_script", None),
            "provision_status": getattr(p, "provision_status", None),
            "provision_log": list(getattr(p, "provision_log", None) or []),
            "error": p.error,
            "created_at": p.created_at, "updated_at": p.updated_at,
            "last_used_at": p.last_used_at,
        }


# ── inrichten op de achtergrond ─────────────────────────────────────────────
# Eén taak per lab, met de taak-referentie vastgehouden zodat de garbage
# collector hem niet halverwege opruimt (zelfde patroon als
# services/agent/background_runs.py).
_PROVISION_TASKS: Dict[str, Any] = {}


def provision_in_background(lab_id: str, *, force: bool = False) -> bool:
    """Inrichten kan minuten duren — Playwright haalt een browser van honderden
    megabytes binnen — en een verzoek dat daarop wacht laat het scherm net zo
    lang hangen (en loopt tegen de time-out van elke proxy ertussen aan). Dus:
    het lab is meteen klaar, het inrichten loopt erachteraan, en de UI volgt
    `provision_status` / `provision_log`.

    Draait er al een ronde voor dit lab, dan doet een tweede aanvraag niets —
    twee keer tegelijk apt draaien in dezelfde container loopt vast op elkaars
    lock. Geeft terug OF er iets is ingepland, zodat de aanroeper de status niet
    op "bezig" zet voor werk dat nooit begint."""
    import asyncio

    running = _PROVISION_TASKS.get(lab_id)
    if running is not None and not running.done():
        log.infox("Inrichten loopt al", lab_id=lab_id)
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # geen draaiende loop (script/test) — dan niet
        log.warningx("Inrichten niet gestart: geen event loop", lab_id=lab_id)
        return False
    task = loop.create_task(_provision_worker(lab_id, force=force))
    _PROVISION_TASKS[lab_id] = task
    task.add_done_callback(lambda _t: _PROVISION_TASKS.pop(lab_id, None))
    return True


def rebuild_in_background(lab_id: str, *, image: Optional[str] = None,
                          pull: bool = True) -> bool:
    """Opnieuw opbouwen op de achtergrond, om dezelfde reden als het inrichten:
    een image ophalen kan gigabytes zijn, en zowel het scherm als een tool-call
    van de agent heeft een kortere adem dan dat. Volgt dezelfde ene-taak-per-lab
    regel — opbouwen terwijl er nog geïnstalleerd wordt zou de installatie in
    een container schrijven die net verdwijnt."""
    import asyncio

    running = _PROVISION_TASKS.get(lab_id)
    if running is not None and not running.done():
        log.infox("Er loopt al werk voor dit lab", lab_id=lab_id)
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warningx("Opnieuw opbouwen niet gestart: geen event loop", lab_id=lab_id)
        return False
    task = loop.create_task(_rebuild_worker(lab_id, image=image, pull=pull))
    _PROVISION_TASKS[lab_id] = task
    task.add_done_callback(lambda _t: _PROVISION_TASKS.pop(lab_id, None))
    return True


async def _rebuild_worker(lab_id: str, *, image: Optional[str], pull: bool) -> None:
    from db.database import SessionLocal

    db = SessionLocal()
    try:
        svc = LabService(db)
        await svc.rebuild(lab_id, image=image, pull=pull)
        # Meteen doorpakken in dezelfde taak: een verse container is leeg, en
        # zonder dit zou het lab draaien zonder één van zijn pakketten.
        await svc.provision(lab_id)
    except Exception as exc:  # noqa: BLE001
        log.warningx("Opnieuw opbouwen mislukt", lab_id=lab_id, error=str(exc)[:300])
    finally:
        db.close()


async def _provision_worker(lab_id: str, *, force: bool) -> None:
    from db.database import SessionLocal

    db = SessionLocal()
    try:
        await LabService(db).provision(lab_id, force=force)
    except Exception as exc:  # noqa: BLE001 — een achtergrondtaak heeft geen aanroeper
        log.warningx("Inrichten mislukt", lab_id=lab_id, error=str(exc)[:300])
        try:
            p = db.get(Lab, lab_id)
            if p is not None:
                p.provision_status = "error"
                p.provision_log = list(p.provision_log or []) + [{
                    "key": "provisioning", "label": "Inrichten", "status": "error",
                    "output": str(exc)[:2000]}]
                p.updated_at = _now_iso()
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
