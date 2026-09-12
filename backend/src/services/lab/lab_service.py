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
    hours = max(1, min(int(ttl_hours or 14), 24 * 14))
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



def _profielsleutel(waarde: Optional[str]) -> str:
    """Een onbekend profiel valt terug op 'generiek' in plaats van te falen:
    de guard hoort nooit uit te vallen op een typefout in een instelling."""
    from services.lab.profielen import PROFIELEN, STANDAARD

    sleutel = (waarde or "").strip().lower()
    return sleutel if sleutel in PROFIELEN else STANDAARD


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
            # De werkers volgen wat de daemon zegt; een container die weg is,
            # laat zijn werker als "error" achter zodat een start hem opnieuw
            # aanmaakt in plaats van eeuwig naar een dode id te wijzen.
            for w in self.ensure_workers(p):
                m_w = by_container.get(w.container_id) if w.container_id else None
                nieuw = ("running" if m_w and m_w["state"] == "running"
                         else ("stopped" if m_w else "error"))
                if nieuw != w.status:
                    w.status = nieuw
                    if nieuw == "error":
                        w.container_id = None
                        w.error = "Container niet meer gevonden na herstart van LabX"
                    w.updated_at = _now_iso()
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
        # ÓÓK de containers van extra werkers: die staan niet in Lab.container_id
        # (dat is werker 1), en zonder deze regel ziet de opruiming ze als
        # weeskinderen en gooit ze weg zodra ze even uit staan.
        from models.lab_worker import LabWorker
        db_container_ids = {c for (c,) in self.db.query(Lab.container_id).all() if c}
        db_container_ids |= {c for (c,) in self.db.query(LabWorker.container_id).all() if c}
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
            srv.location = "lab"
            srv.server_type = "stdio"
            srv.is_enabled = True
            # Het pakket beheert het commando: het weet welke vlaggen bij deze
            # installatie horen (waar het profiel staat, of hij zichtbaar moet
            # draaien) en dat verandert mee met het pakket. Een eigen variant
            # maak je als aparte server, niet door deze rij te verbouwen — dan
            # zou de volgende inrichtronde hem stilletjes terugzetten.
            if (srv.stdio_command or "").strip() != command:
                log.infox("Commando van lab-MCP-server bijgewerkt door het pakket",
                          server=slug, commando=command)
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

        # Inrichten gebeurt PER WERKER: /workspace is gedeeld, maar wat je
        # installeert zit in de containerlaag en dus in één container. Een lab
        # met drie werkers waarvan er één Playwright heeft, is een lab dat soms
        # werkt en soms niet — het onaangenaamste soort storing.
        werkers = [w for w in self.ensure_workers(p) if w.container_id]
        if not werkers:
            werkers = []
        entries: List[Dict[str, Any]] = []
        failed = 0
        for step in steps:
            for w in werkers:
                entry = await self._run_provision_step(w.container_id, step, force=force)
                if len(werkers) > 1:
                    entry["key"] = f"{entry['key']}@w{w.index}"
                    entry["label"] = f"{entry['label']} (werker {w.index})"
                if entry["status"] == "error":
                    failed += 1
                entries.append(entry)
            entry = entries[-1] if entries else {"status": "skipped"}
            # "skipped" telt hier net zo goed als "ok": de software staat er, en
            # de koppeling kan ontbreken (nieuw lab, of een lab van vóór deze
            # functie). Registreren is idempotent.
            cfg = step.get("mcp_server")
            gelukt_overal = all(e["status"] in ("ok", "skipped")
                                for e in entries[-len(werkers):]) if werkers else False
            if cfg and gelukt_overal:
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
        for w in werkers:
            eigen = [e for e in entries if str(e.get("key", "")).endswith(f"@w{w.index}")] \
                if len(werkers) > 1 else entries
            was_bezig = w.provision_status == "pending"
            w.provision_status = "error" if any(e["status"] == "error" for e in eigen) else "ok"
            w.provision_log = eigen
            if was_bezig:
                # De ongebruikt-klok begint NU, niet bij het aanmaken. Inrichten
                # duurt voor een lab met Playwright een halfuur, en al die tijd
                # doet de werker per definitie geen werk — met de oude telling
                # was hij dus "30 minuten ongebruikt" op het moment dat hij
                # eindelijk bruikbaar werd, en ruimde de reaper hem meteen op.
                # Daarna zette de autoscaler er weer een bij: een halfuur
                # downloaden per ronde, eindeloos.
                w.last_used_at = _now_iso()
            w.updated_at = _now_iso()
        p.updated_at = _now_iso()
        self.db.commit()
        log.infox("Lab ingericht", lab_id=p.id, werkers=len(werkers),
                  stappen=len(entries), mislukt=failed)
        # Een extra werker die zijn pakketten niet kreeg, krijgt geen werk meer
        # en wordt zo opgeruimd. Zonder melding merk je daar niets van: je ziet
        # alleen dat er minder parallel loopt dan je dacht.
        kapot = [w.index for w in werkers
                 if w.index > 1 and w.provision_status not in ("ok", "skipped")]
        if kapot:
            from services.notify.notify_service import meld
            namen = ", ".join(str(x) for x in kapot)
            meld("storing", f"Lab '{p.name}': werker {namen} kon niet ingericht worden",
                 (f"Werker {namen} mist pakketten en krijgt daarom geen werk; hij wordt "
                  f"opgeruimd en bij de volgende keer opnieuw geprobeerd. Daardoor draait "
                  f"er minder parallel dan ingesteld.\n\nMislukte stappen:\n"
                  + "\n".join(f"- {e.get('label') or e.get('key')}: "
                               f"{str(e.get('output'))[:200]}"
                               for e in entries if e.get("status") == "error")),
                 {"lab_id": p.id, "lab_name": p.name})
        return {"ok": failed == 0, "status": p.provision_status, "steps": entries}

    # ── werkers (de containers van dit lab) ──────────────────────────────────

    def workers(self, lab_id: str) -> List[Any]:
        from models.lab_worker import LabWorker
        return (self.db.query(LabWorker).filter(LabWorker.lab_id == lab_id)
                .order_by(LabWorker.index.asc()).all())

    def worker(self, worker_id: int) -> Any:
        from models.lab_worker import LabWorker
        w = self.db.get(LabWorker, worker_id)
        if w is None:
            raise HTTPException(status_code=404, detail="Werker niet gevonden")
        return w

    def _worker_alias(self, p: Lab, index: int) -> str:
        """Werker 1 houdt de naam die het lab altijd had — een bestaande
        verwijzing (DNS-alias, browserproxy, gepubliceerde poort) blijft
        daarmee gewoon kloppen."""
        basis = p.network_alias or f"labx-lab-{p.id[:12]}"
        return basis if index <= 1 else f"{basis}-w{index}"

    def ensure_workers(self, p: Lab) -> List[Any]:
        """Zorg dat er werker-rijen zijn, ook voor een lab van vóór deze
        functie: de bestaande container wordt werker 1. Zonder deze inhaalslag
        zou een bestaand lab ineens nul werkers hebben en nergens meer kunnen
        draaien."""
        from models.lab_worker import LabWorker
        rijen = self.workers(p.id)
        if not rijen:
            now = _now_iso()
            w = LabWorker(lab_id=p.id, index=1, container_id=p.container_id,
                          network_alias=p.network_alias or f"labx-lab-{p.id[:12]}",
                          status=p.status if p.status in ("running", "stopped", "error") else "stopped",
                          provision_status=p.provision_status,
                          provision_log=list(p.provision_log or []),
                          created_at=now, updated_at=now)
            self.db.add(w)
            self.db.commit()
            rijen = [w]
        return rijen

    def _spiegel_werker1(self, p: Lab) -> None:
        """`Lab.container_id`/`network_alias` blijven meelopen met werker 1.
        Alles wat met "het lab" praat (terminal, bestanden, browserproxy)
        gebruikt die velden, en hoeft van werkers niets te weten."""
        eerste = next((w for w in self.workers(p.id) if w.index == 1), None)
        if eerste is None:
            return
        p.container_id = eerste.container_id
        p.network_alias = eerste.network_alias

    async def _start_worker_container(self, p: Lab, w: Any) -> None:
        """Eén werker-container (op)starten. Alle werkers draaien hetzelfde
        image en delen hetzelfde volume — dat is wat ze werkers van hetzelfde
        lab maakt en geen losse labs."""
        # Poorten alleen op werker 1: twee containers kunnen niet dezelfde
        # hostpoort publiceren, en "de poort van dit lab" hoort bij één plek.
        poorten = (p.ports or []) if w.index <= 1 else []
        container_id = await self.runtime.run_container(
            name=w.network_alias, image=p.image, volume=p.volume_name,
            cpu_limit=p.cpu_limit, mem_limit_mb=p.mem_limit_mb,
            allow_network=p.allow_network, ports=poorten,
            expires_at=p.expires_at,
        )
        w.container_id = container_id
        w.status = "running"
        w.error = None
        w.updated_at = _now_iso()
        self.db.commit()

    # ── inloggen met je eigen browser (tunnel) ───────────────────────────────

    # De poort waarop een interactieve Microsoft-login in het lab zijn redirect
    # verwacht. Vast, want een willekeurige poort valt niet door te sturen —
    # dat is de hele reden dat de fab-CLI hierop gepatcht wordt.
    AUTH_PORT = 8400

    async def tunnel_info(self, lab_id: str, *, port: Optional[int] = None,
                          worker_id: Optional[int] = None) -> Dict[str, Any]:
        """Alles wat je nodig hebt om `localhost:<poort>` op JOUW machine in dit
        lab te laten uitkomen.

        Waarom dat nodig is: een interactieve Microsoft-login stuurt de browser
        naar `http://localhost:<poort>`, en dat is de localhost van de machine
        waar je klikt — niet die van de server. De listener zit in de
        labcontainer. Iets moet die twee verbinden, en dat iets moet op jouw
        machine draaien; LabX kan het voorbereiden maar niet zelf starten.

        Geen gepubliceerde poort nodig: de server bereikt de container
        rechtstreeks op zijn IP in het labnetwerk."""
        p = self.get(lab_id)
        poort = int(port or self.AUTH_PORT)
        cid = self.container_for(p, worker_id)
        if p.status != "running" or not cid:
            raise HTTPException(status_code=409, detail="Lab draait niet (start hem eerst)")
        ip = await self.runtime.container_ip(cid)
        if not ip:
            raise HTTPException(status_code=502, detail="Kon het IP van de labcontainer niet bepalen")
        return {"lab_id": p.id, "lab_name": p.name, "port": poort,
                "container_ip": ip, "container": cid[:12],
                "network": settings.LAB_NETWORK}

    @staticmethod
    def tunnel_script(*, lab_name: str, ssh_target: str, ip: str, port: int) -> str:
        """Een klein script dat de tunnel opzet en openhoudt. Bewust `ssh` en
        geen eigen programmaatje: dat zit al op elke Mac en Linux-machine, het
        heeft geen installatie nodig, en het is te lezen wat het doet."""
        return f"""#!/bin/sh
# LabX — tunnel naar lab '{lab_name}'
#
# Zet localhost:{port} op DEZE machine door naar de labcontainer op de server.
# Nodig voor een interactieve Microsoft-login vanuit het lab: die stuurt je
# browser naar http://localhost:{port}, en dat is de localhost van de machine
# waar je klikt.
#
# Laat dit venster openstaan zolang je inlogt. Stoppen: Ctrl-C.
set -e
echo "Tunnel: localhost:{port} -> {ip}:{port} (via {ssh_target})"
echo "Laat dit venster open tijdens het inloggen. Stoppen met Ctrl-C."
exec ssh -N \\
    -o ServerAliveInterval=30 \\
    -o ExitOnForwardFailure=yes \\
    -L {port}:{ip}:{port} \\
    {ssh_target}
"""

    # ── autoscaler ───────────────────────────────────────────────────────────
    #
    # Een lab houdt altijd `min_workers` containers aan (standaard één: die
    # draagt de identiteit van het lab en gaat pas uit als het hele lab door
    # zijn TTL heen valt). Staat er werk te wachten en is er geen werker vrij,
    # dan komt er eentje bij tot aan `max_workers` — het plafond dat de mens
    # zet. Doet een extra werker een tijd niets, dan gaat hij weer weg.
    #
    # Waarom een nieuwe werker niet meteen gebruikt wordt: hij moet eerst
    # ingericht worden (pakketten zitten in de containerlaag, niet in het
    # gedeelde /workspace). Een planning die in een half opgebouwde container
    # begint, vindt zijn browser niet — dus wacht hij liever twintig seconden.

    async def ensure_extra_worker(self, lab_id: str) -> Optional[Any]:
        """Er is werk en geen vrije werker: probeer er een bij te zetten.

        Geeft de nieuwe werker terug, of None als het plafond bereikt is (of
        het lab niet draait). De werker is bij terugkeer nog aan het inrichten;
        claimen kan pas als dat klaar is."""
        from models.lab_worker import LabWorker

        p = self.get(lab_id)
        if p.status != "running":
            return None
        huidig = self.workers(p.id)
        plafond = max(int(getattr(p, "min_workers", 1) or 1),
                      int(getattr(p, "max_workers", 1) or 1))
        if len(huidig) >= plafond:
            return None
        now = _now_iso()
        index = max((w.index for w in huidig), default=0) + 1
        w = LabWorker(lab_id=p.id, index=index, network_alias=self._worker_alias(p, index),
                      status="creating", provision_status="pending", provision_log=[],
                      last_used_at=now, created_at=now, updated_at=now)
        self.db.add(w)
        self.db.commit()
        try:
            await self._start_worker_container(p, w)
        except Exception as exc:  # noqa: BLE001
            w.status = "error"
            w.error = str(exc)[:2000]
            w.provision_status = None
            w.updated_at = _now_iso()
            self.db.commit()
            log.warningx("Bijschalen mislukt", lab_id=p.id, werker=index, error=str(exc)[:200])
            return None
        p.worker_count = len(self.workers(p.id))
        p.updated_at = _now_iso()
        self.db.commit()
        log.infox("Werker bijgezet", lab_id=p.id, werker=index, plafond=plafond)
        if p.allow_network:
            provision_in_background(p.id)
        else:
            w.provision_status = "skipped"
            w.updated_at = _now_iso()
            self.db.commit()
        return w

    def claimbare_werkers(self, p: Lab) -> List[Any]:
        """Werkers waar nu werk op mag starten.

        Een EXTRA werker doet pas mee als zijn inrichting GESLAAGD is. Niet
        alleen "niet meer bezig": een werker waarvan pakketten mislukten mist
        precies het gereedschap waarvoor hij bestaat, en een agent die daarop
        landt meldt dat hij zijn tools kwijt is — zonder dat ergens staat
        waarom. Zo'n werker blijft staan (de fout is te zien bij het lab) maar
        krijgt geen werk.

        Werker 1 doet altijd mee, ook als hij nog aan het inrichten is of iets
        misging: dat is de container van het lab zelf, en zo werkte het altijd
        al. Hem uitsluiten zou elk vers lab minutenlang blokkeren voor werk dat
        prima kan beginnen, en een lab met één mislukt pakket helemaal
        onbruikbaar maken."""
        return [w for w in self.ensure_workers(p)
                if w.status == "running" and w.container_id
                and (w.index == 1 or w.provision_status in ("ok", "skipped"))]

    async def reap_idle_workers(self, idle_minutes: int = 30) -> int:
        """Extra werkers opruimen die een tijd niets deden.

        Nooit onder `min_workers`, nooit werker 1, en nooit een werker waar
        werk op draait. Een lab dat een piek had, zakt zo vanzelf terug naar
        zijn ondergrens in plaats van containers te blijven aanhouden."""
        from datetime import timedelta
        from models.lab_worker import LabWorker

        grens = (datetime.now(timezone.utc) - timedelta(minutes=max(5, idle_minutes))).isoformat()
        opgeruimd = 0
        for p in self.db.query(Lab).filter(Lab.status == "running").all():
            werkers = self.workers(p.id)
            ondergrens = max(1, int(getattr(p, "min_workers", 1) or 1))
            if len(werkers) <= ondergrens:
                continue
            bezet = self._bezette_werkers(p.id)
            for w in sorted(werkers, key=lambda x: x.index, reverse=True):
                if len(self.workers(p.id)) <= ondergrens:
                    break
                if w.index <= 1 or w.id in bezet:
                    continue
                if w.provision_status == "pending":
                    # Nog aan het inrichten. Die is per definitie ongebruikt —
                    # hem daarom opruimen betekent het halve uur downloaden
                    # weggooien en meteen opnieuw beginnen.
                    continue
                if w.provision_status not in ("ok", "skipped"):
                    # Mislukt ingericht: hij krijgt geen werk meer (zie
                    # claimbare_werkers) maar telt wél mee voor het plafond, en
                    # blokkeert daarmee het bijzetten van een werker die het
                    # wél doet. Meteen weg, niet pas na het stille halfuur —
                    # de volgende keer dat er werk is, probeert de autoscaler
                    # het gewoon opnieuw.
                    log.warningx("Werker met mislukte inrichting opgeruimd",
                                 lab_id=p.id, werker=w.index)
                    await self._verwijder_werker(p, w)
                    opgeruimd += 1
                    continue
                if (w.last_used_at or w.created_at) > grens:
                    continue
                await self._verwijder_werker(p, w)
                opgeruimd += 1
                log.infox("Ongebruikte werker opgeruimd", lab_id=p.id, werker=w.index)
            p.worker_count = len(self.workers(p.id))
            p.updated_at = _now_iso()
            self.db.commit()
        return opgeruimd

    async def _verwijder_werker(self, p: Lab, w) -> None:
        """Een werker weghalen: eerst zijn MCP-sessies netjes sluiten (anders
        blijft er een proces hangen dat in een verdwenen container praat), dan
        de container, dan de rij."""
        await self._close_lab_mcp_sessions(w.container_id)
        if w.container_id:
            try:
                await self.runtime.remove(w.container_id, timeout=120)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Werker opruimen mislukt", lab_id=p.id,
                             werker=w.index, error=str(exc)[:200])
        self.db.delete(w)
        self.db.commit()

    def touch_worker(self, worker_id: Optional[int]) -> None:
        """Deze werker is in gebruik — houdt hem uit handen van de opruimer."""
        if not worker_id:
            return
        from models.lab_worker import LabWorker
        w = self.db.get(LabWorker, int(worker_id))
        if w is not None:
            w.last_used_at = _now_iso()
            self.db.commit()

    async def scale(self, lab_id: str, *, min_workers: Optional[int] = None,
                    max_workers: Optional[int] = None,
                    count: Optional[int] = None) -> Dict[str, Any]:
        """De grenzen van de autoscaler zetten, en meteen naar de ondergrens
        toewerken.

        `count` is de kortere weg voor "ik wil er nu zoveel": dat zet de
        ondergrens. Het plafond blijft van de mens — daar mag ook de agent
        niet overheen."""
        p = self.get(lab_id)
        self.ensure_workers(p)
        if count is not None:
            min_workers = int(count)
        onder = max(1, min(int(min_workers if min_workers is not None
                               else getattr(p, "min_workers", 1) or 1), 8))
        boven = max(onder, min(int(max_workers if max_workers is not None
                                   else getattr(p, "max_workers", 1) or 1), 8))
        p.min_workers, p.max_workers = onder, boven
        p.updated_at = _now_iso()
        self.db.commit()

        toegevoegd = []
        while len(self.workers(p.id)) < onder:
            w = await self.ensure_extra_worker(p.id)
            if w is None:
                break
            toegevoegd.append(w.index)
        verwijderd = []
        if len(self.workers(p.id)) > boven:
            bezet = self._bezette_werkers(p.id)
            for w in sorted(self.workers(p.id), key=lambda x: x.index, reverse=True):
                if len(self.workers(p.id)) <= boven:
                    break
                if w.index <= 1 or w.id in bezet:
                    continue
                await self._close_lab_mcp_sessions(w.container_id)
                if w.container_id:
                    try:
                        await self.runtime.remove(w.container_id, timeout=120)
                    except Exception as exc:  # noqa: BLE001
                        log.warningx("Werker verwijderen mislukt", lab_id=p.id,
                                     werker=w.index, error=str(exc)[:200])
                verwijderd.append(w.index)
                self.db.delete(w)
                self.db.commit()
        p.worker_count = len(self.workers(p.id))
        p.updated_at = _now_iso()
        self._spiegel_werker1(p)
        self.db.commit()
        return {"ok": True, "workers": p.worker_count, "min": onder, "max": boven,
                "toegevoegd": toegevoegd, "verwijderd": verwijderd}

    def _bezette_werkers(self, lab_id: str) -> set:
        """Werkers waar nu een planning-ticket op draait."""
        from models.board import Board
        from models.plan import TicketPlan, TicketPlanItem
        borden = [b.id for b in self.db.query(Board).filter(Board.lab_id == lab_id).all()]
        if not borden:
            return set()
        rijen = (self.db.query(TicketPlanItem.worker_id)
                 .join(TicketPlan, TicketPlan.id == TicketPlanItem.plan_id)
                 .filter(TicketPlan.board_id.in_(borden),
                         TicketPlanItem.state == "running",
                         TicketPlanItem.worker_id.isnot(None)).all())
        return {r[0] for r in rijen}

    def container_for(self, p: Lab, worker_id: Optional[int] = None) -> Optional[str]:
        """De container waarin een handeling hoort te landen.

        Zonder werker: die van het lab zelf (werker 1) — dat is waar de
        terminal, de bestandsbrowser en een gewone chat thuishoren. Mét
        werker: precies die container, want een agent-run die op werker 3 is
        gestart moet daar blijven; anders schrijft hij in het bestandssysteem
        van een run waar hij niets mee te maken heeft."""
        if worker_id:
            from models.lab_worker import LabWorker
            w = self.db.get(LabWorker, int(worker_id))
            if w is not None and w.lab_id == p.id and w.container_id:
                return w.container_id
        return p.container_id

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
        min_workers: int = 1,
        max_workers: int = 1,
        security_profile: Optional[str] = None,
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
        ttl_hours = max(1, min(int(ttl_hours or 14), 24 * 14))
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
            min_workers=max(1, min(int(min_workers or 1), 8)),
            max_workers=max(1, min(int(max_workers or 1), 8)),
            worker_count=1,
            # Wat dit lab over zijn eigen wereld weet; bepaalt welke guard-regels
            # zinnig zijn. Zie services/lab/profielen.py.
            security_profile=_profielsleutel(security_profile),
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
            # Werker 1 is deze container; extra werkers komen er via scale bij.
            self.ensure_workers(p)
            eerste = self.workers(p.id)[0]
            eerste.container_id = container_id
            eerste.status = "running"
            eerste.updated_at = _now_iso()
            self.db.commit()
            if int(getattr(p, "min_workers", 1) or 1) > 1:
                await self.scale(p.id, min_workers=int(p.min_workers),
                                 max_workers=int(p.max_workers))
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
        try:
            await self.runtime.start(p.container_id)
        except Exception as exc:  # noqa: BLE001
            raise self._container_weg_of_fout(p, exc)
        # De overige werkers erbij: die horen bij hetzelfde lab en moeten dus
        # met hem mee aan. Eentje die niet wil starten is geen reden om het
        # hele lab te weigeren — dat wordt op de werker zelf gemeld.
        for w in self.ensure_workers(p):
            if w.index == 1:
                w.container_id = p.container_id
                w.status = "running"
                w.updated_at = _now_iso()
                continue
            try:
                if w.container_id:
                    await self.runtime.start(w.container_id)
                else:
                    await self._start_worker_container(p, w)
                w.status = "running"
                w.error = None
            except Exception as exc:  # noqa: BLE001
                w.status = "error"
                w.error = str(exc)[:2000]
                log.warningx("Werker starten mislukt", lab_id=p.id, werker=w.index,
                             error=str(exc)[:200])
            w.updated_at = _now_iso()
        self.db.commit()
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

    async def _remove_before_rebuild(self, p: Lab, old_container: str,
                                     naam: Optional[str] = None) -> None:
        """De oude container weg krijgen VOORDAT er een nieuwe met dezelfde naam
        start.

        Dit ging eerder mis en legde een lab helemaal plat. `docker rm -f`
        kreeg een time-out na 60s op een container waarvan de proces-tabel vol
        zat; de code logde dat als waarschuwing en ging door, waarna `docker
        run` met diezelfde naam gegarandeerd afketste op "container name is
        already in use". Het lab bleef achter op `error` met een container-id
        dat intussen verdween — en elke volgende actie liep op een 500. Een
        tweede poging deed precies hetzelfde ("removal is already in
        progress").

        Twee dingen zijn daarom veranderd: het verwijderen krijgt ruim de tijd,
        en daarna wordt gecontroleerd dat zowel het id als de NAAM echt weg
        zijn bij de daemon. Lukt dat niet, dan stopt het opbouwen hier met een
        melding waar je iets mee kunt — in plaats van een fout te veroorzaken
        die daarna niemand meer kan plaatsen."""
        try:
            await self.runtime.remove(old_container, timeout=300)
        except Exception as exc:  # noqa: BLE001 — kan al bezig zijn; hieronder wachten we het af
            log.warningx("Oude container verwijderen gaf een fout", lab_id=p.id,
                         error=str(exc)[:200])
        weg = await self.runtime.wait_until_gone(old_container,
                                                 naam or p.network_alias or "",
                                                 timeout=300)
        if not weg:
            raise HTTPException(
                status_code=409,
                detail=("De oude container van dit lab is nog niet opgeruimd (dat kan even duren "
                        "als de proces-tabel vol zat). Het lab is niet aangeraakt — probeer het "
                        "over een minuut opnieuw."))

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
        await self._close_lab_mcp_sessions(old_container)
        try:
            if old_container:
                await self._remove_before_rebuild(p, old_container)
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
        except HTTPException as exc:
            # Afgebroken vóór we iets sloopten (de oude container is nog niet
            # opgeruimd): het lab stond en staat er nog, dus terug naar de
            # vorige toestand in plaats van het als kapot markeren.
            p.status = "stopped" if old_container else "error"
            p.error = str(exc.detail)[:1900]
            p.updated_at = _now_iso()
            self.db.commit()
            raise
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
        # Werker 1 is de zojuist gemaakte container; de rest wordt op hetzelfde
        # image opnieuw opgebouwd, want een lab met werkers op verschillende
        # images is geen lab meer maar een verzameling.
        for w in self.ensure_workers(p):
            if w.index == 1:
                w.container_id = container_id
                w.status = "running"
                w.error = None
                w.updated_at = _now_iso()
                continue
            await self._close_lab_mcp_sessions(w.container_id)
            try:
                if w.container_id:
                    await self._remove_before_rebuild(p, w.container_id, w.network_alias)
                await self._start_worker_container(p, w)
            except Exception as exc:  # noqa: BLE001
                w.status = "error"
                w.error = str(exc)[:2000]
                w.updated_at = _now_iso()
                log.warningx("Werker opnieuw opbouwen mislukt", lab_id=p.id,
                             werker=w.index, error=str(exc)[:200])
        self.db.commit()
        p.provision_status = "pending" if p.allow_network else "skipped"
        p.provision_log = []
        p.expires_at = _expiry_from_now(p.ttl_hours)
        p.updated_at = p.last_used_at = _now_iso()
        self.db.commit()
        log.infox("Lab opnieuw opgebouwd", lab_id=p.id, image=target)
        await self._sync_azure_profile_into_lab(p)
        return self._to_dict(p)

    def _container_weg_of_fout(self, p: Lab, exc: BaseException) -> HTTPException:
        """Een container die niet meer bestaat is een TOESTAND, geen crash.

        Zonder dit kwam er een onafgevangen RuntimeError uit `docker start` —
        en dus een kale 500 op elk shell-commando, waar niemand (de agent al
        helemaal niet) uit kan opmaken wat er aan de hand is of wat te doen.
        Nu staat het op het lab én in het antwoord: de container is weg, bouw
        het lab opnieuw op."""
        tekst = str(exc)
        verdwenen = ("No such container" in tekst
                     or "marked for removal" in tekst
                     or "is not running" in tekst and "removal" in tekst)
        if verdwenen:
            p.status = "error"
            p.error = ("De container van dit lab bestaat niet meer (mogelijk halverwege een "
                       "eerdere herbouw verdwenen). /workspace staat op een eigen volume en is "
                       "er nog: bouw het lab opnieuw op om verder te kunnen.")
            p.updated_at = _now_iso()
            self.db.commit()
            log.warningx("Lab-container bestaat niet meer", lab_id=p.id, error=tekst[:200])
            # Een lab dat weg is, is niet iets wat je pas wilt merken als je
            # toevallig het scherm opent: elke agent-run erop loopt tot die tijd
            # stuk. Melden gebeurt alleen HIER, in de overgang naar 'error' —
            # niet bij elke aanroep die daarna faalt.
            from services.notify.notify_service import meld
            meld("storing", f"Lab '{p.name}' is weg",
                 (p.error or "De container bestaat niet meer.")
                 + "\n\nAlles op /workspace staat er nog; het lab moet opnieuw opgebouwd worden.",
                 {"lab_id": p.id, "lab_name": p.name})
            return HTTPException(status_code=409, detail=p.error)
        return HTTPException(status_code=502, detail=f"Lab starten mislukt: {tekst[:500]}")

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
                              security_profile: Optional[str] = None,
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
        if security_profile is not None:
            p.security_profile = _profielsleutel(security_profile)
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

    async def _close_lab_mcp_sessions(self, container_id: Optional[str]) -> None:
        """Blijvende MCP-processen in deze container afsluiten. Zonder dit
        blijft er een `docker exec` hangen dat naar een container wijst die zo
        meteen niet meer bestaat, en zou de eerstvolgende aanroep na een
        herstart op een dood proces landen."""
        if not container_id:
            return
        try:
            from services.mcp.lab_session_pool import close_for_container
            await close_for_container(container_id)
        except Exception as exc:  # noqa: BLE001 — opruimen mag nooit de actie blokkeren
            log.warningx("Lab-MCP-sessies sluiten mislukt", error=str(exc)[:200])

    async def stop(self, lab_id: str) -> Dict[str, Any]:
        p = self.get(lab_id)
        for w in self.ensure_workers(p):
            if not w.container_id:
                continue
            await self._close_lab_mcp_sessions(w.container_id)
            if w.index > 1:
                try:
                    await self.runtime.stop(w.container_id)
                except Exception as exc:  # noqa: BLE001
                    log.warningx("Werker stoppen mislukt", lab_id=p.id, werker=w.index,
                                 error=str(exc)[:200])
            w.status = "stopped"
            w.updated_at = _now_iso()
        self.db.commit()
        if p.container_id and p.status == "running":
            await self.runtime.stop(p.container_id)
        p.status = "stopped"
        p.updated_at = _now_iso()
        self.db.commit()
        return self._to_dict(p)

    async def delete(self, lab_id: str) -> Dict[str, Any]:
        p = self.get(lab_id)
        # Eerst de extra werkers; het volume gaat pas mee met werker 1, want
        # dat delen ze allemaal.
        for w in self.ensure_workers(p):
            await self._close_lab_mcp_sessions(w.container_id)
            if w.index > 1 and w.container_id:
                try:
                    await self.runtime.remove(w.container_id, timeout=120)
                except Exception as exc:  # noqa: BLE001
                    log.warningx("Werker opruimen mislukt", lab_id=p.id, werker=w.index,
                                 error=str(exc)[:200])
            self.db.delete(w)
        self.db.commit()
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
                    await self.stop(p.id)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Lab stoppen bij expiry mislukt", lab_id=p.id, error=str(exc))
            p.status = "expired"
            p.updated_at = _now_iso()
        if due:
            self.db.commit()
            log.infox("Labs verlopen", count=len(due))
        return len(due)

    # ── execution & files ─────────────────────────────────────────────────────

    def _require_running(self, p: Lab, worker_id: Optional[int] = None) -> str:
        if p.status != "running" or not p.container_id:
            raise HTTPException(status_code=409, detail="Lab draait niet (start hem eerst)")
        cid = self.container_for(p, worker_id)
        if not cid:
            raise HTTPException(status_code=409,
                                detail="De werker van dit lab heeft geen container (start het lab opnieuw)")
        return cid

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

    async def exec_command(self, lab_id: str, command: str, *, timeout: float = 120.0,
                           worker_id: Optional[int] = None) -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p, worker_id)
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

        # `{{secret:naam}}` omzetten naar een omgevingsvariabele die IN de
        # container wordt ingelezen. Het commando dat we hier bewaren en
        # uitvoeren bevat dus nooit de waarde zelf — zie services/lab/secrets.py.
        from services.lab.secrets import SecretService

        geheimen = SecretService(self.db)
        uitvoerbaar, gebruikt, onbekend = await geheimen.bereid_voor(
            lab_id, command, runtime=self.runtime, container_id=cid)
        if onbekend:
            raise HTTPException(
                status_code=400,
                detail=(f"Onbekend geheim: {', '.join(onbekend)}. Zet hem eerst met "
                        f"`lab__secret_put`, of kijk met `lab__secret_list` welke er zijn."))

        result = await self.runtime.exec(cid, ["sh", "-lc", wrapper, uitvoerbaar],
                                         timeout=max(5.0, min(timeout, 600.0)))
        # En de andere kant op: een geheim dat tóch in de uitvoer belandt
        # (geëchood, in een header, in een foutmelding) gaat er hier uit.
        if result.get("output"):
            result["output"] = geheimen.maskeer_in(lab_id, result["output"])
        self._touch(p)
        self.touch_worker(worker_id)
        uit = self._duid_proces_tabel(result)
        if gebruikt:
            uit["secrets_used"] = gebruikt
        return uit

    @staticmethod
    def _duid_proces_tabel(result: Dict[str, Any]) -> Dict[str, Any]:
        """`fork: Resource temporarily unavailable` betekent iets heel anders dan
        het lijkt, en dat is een dure verwarring gebleken.

        Het is niet "geen geheugen" en niet "de server is stuk": het is de
        PID-limiet van deze container (512), volgelopen door processen die er
        nog staan. Een agent die dat niet weet, ziet alleen dat commando's
        mislukken en grijpt naar het zwaarste middel — een herbouw — wat het
        alleen maar erger maakte. Eén regel uitleg bij de fout scheelt dat hele
        pad; opruimen kan gewoon, en het lab hoeft er niet voor om."""
        uitvoer = result.get("output") or ""
        if "Resource temporarily unavailable" not in uitvoer and "fork:" not in uitvoer:
            return result
        result["output"] = uitvoer + (
            "\n\n[LabX] Dit is de proces-limiet van dit lab (512 processen), niet het "
            "geheugen en niet een fout van LabX. Meestal staan er eigen achtergrondprocessen "
            "open: kijk met `ps -eo pid,ppid,etime,args --sort=-etime | head -30` en ruim ze op "
            "met `pkill -f <patroon>`. Een lab hoeft hier niet voor herbouwd te worden — en "
            "herbouwen tijdens een volle proces-tabel duurt juist lang, omdat de container "
            "dan traag afsterft."
        )
        return result

    async def list_files(self, lab_id: str, path: str = "/workspace", *,
                    worker_id: Optional[int] = None) -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p, worker_id)
        target = self._safe_path(path)
        # Grootte erbij: zonder dat is een geüpload bestand niet te
        # controleren — je ziet de naam staan maar niet of er iets in zit.
        #
        # `--time-style=long-iso` maakt de datum twee vaste velden, waardoor er
        # precies zeven velden vóór de naam staan en een naam met spaties er
        # heel uitkomt. Dat is GNU-gedrag; een lab-image is instelbaar, dus als
        # die vlag niet bestaat vallen we terug op de kale lijst zonder
        # groottes. Liever een bestandsbrowser zonder cijfers dan geen.
        #
        # `-A` laat . en .. weg, `-p` zet een / achter mappen, `-L` volgt links
        # (anders is de grootte die van de link), `--` beschermt tegen een pad
        # dat met - begint.
        result = await self.runtime.exec(
            cid, ["sh", "-c", 'ls -lApL --time-style=long-iso -- "$1"', "sh", target],
            timeout=20)
        met_groottes = result["exit_code"] == 0
        if not met_groottes:
            result = await self.runtime.exec(
                cid, ["sh", "-c", 'ls -ApL -- "$1"', "sh", target], timeout=20)
            if result["exit_code"] != 0:
                raise HTTPException(status_code=404, detail=result["output"][:300])

        entries = []
        for line in result["output"].splitlines():
            regel = line.rstrip()
            if not regel or regel.startswith("total "):
                continue
            naam, grootte = regel.strip(), None
            if met_groottes:
                velden = regel.split(None, 7)
                if len(velden) == 8 and velden[4].isdigit():
                    naam = velden[7]
                    grootte = int(velden[4])
                else:
                    continue  # geen regel die we begrijpen; niet gokken
            is_map = naam.endswith("/")
            entries.append({"name": naam.rstrip("/"), "is_dir": is_map,
                            "bytes": None if is_map else grootte})
        return {"path": target, "entries": entries}

    async def read_file(self, lab_id: str, path: str, *,
                    worker_id: Optional[int] = None) -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p, worker_id)
        target = self._safe_path(path)
        result = await self.runtime.exec(cid, ["head", "-c", "200000", "--", target], timeout=30)
        if result["exit_code"] != 0:
            raise HTTPException(status_code=404, detail=result["output"][:300])
        return {"path": target, "content": result["output"], "truncated": result["truncated"]}

    async def write_file(self, lab_id: str, path: str, content: str, *,
                    worker_id: Optional[int] = None) -> Dict[str, Any]:
        p = self.get(lab_id)
        cid = self._require_running(p, worker_id)
        target = self._safe_path(path)
        parent = target.rsplit("/", 1)[0] or "/workspace"
        # Pad als ARGUMENT, niet in de tekst van het commando: een naam met een
        # aanhalingsteken erin zou anders uit de quotes breken en de rest als
        # commando uitvoeren. Dat pad komt van een client, dus dat kan niet.
        result = await self.runtime.exec(
            cid, ["sh", "-c", 'mkdir -p "$1" && cat > "$2"', "sh", parent, target],
            stdin=(content or "").encode("utf-8"), timeout=30)
        if result["exit_code"] != 0:
            raise HTTPException(status_code=500, detail=result["output"][:300])
        self._touch(p)
        return {"ok": True, "path": target, "bytes": len((content or "").encode("utf-8"))}

    async def upload_files(self, lab_id: str, bestanden: List[Dict[str, Any]], *,
                           directory: str = "/workspace",
                           worker_id: Optional[int] = None) -> Dict[str, Any]:
        """Bestanden van buiten in het lab zetten.

        Binair, dus niet via `write_file`: die neemt een str en zou een PNG of
        een xlsx onderweg stukmaken. De bytes gaan rechtstreeks door de stdin
        van `cat`, en het doelpad staat als argument in het commando zodat een
        rare bestandsnaam er niets mee kan.

        Wat er al staat wordt niet overschreven maar krijgt er een volgnummer
        naast: twee keer `export.csv` uploaden zijn meestal twee verschillende
        exports, en stil de vorige wissen is de vervelendste manier om daar
        achter te komen.
        """
        from services.lab.uploads import (MAX_BESTAND_BYTES, MAX_TOTAAL_BYTES,
                                          unieke_naam, veilige_naam)

        p = self.get(lab_id)
        cid = self._require_running(p, worker_id)
        doelmap = self._safe_path(directory or "/workspace")

        totaal = sum(len(b.get("data") or b"") for b in bestanden)
        if totaal > MAX_TOTAAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Samen te groot ({totaal // (1024*1024)} MB); "
                       f"maximaal {MAX_TOTAAL_BYTES // (1024*1024)} MB per keer.")

        mk = await self.runtime.exec(cid, ["sh", "-c", 'mkdir -p "$1"', "sh", doelmap],
                                     timeout=20)
        if mk["exit_code"] != 0:
            raise HTTPException(status_code=500, detail=mk["output"][:300])

        bezet = [e["name"] for e in (await self.list_files(
            lab_id, doelmap, worker_id=worker_id))["entries"]]
        geschreven: List[Dict[str, Any]] = []
        overgeslagen: List[Dict[str, str]] = []

        for bestand in bestanden:
            data: bytes = bestand.get("data") or b""
            naam = veilige_naam(str(bestand.get("filename") or ""))
            if not data:
                overgeslagen.append({"name": naam, "reden": "leeg bestand"})
                continue
            if len(data) > MAX_BESTAND_BYTES:
                overgeslagen.append({
                    "name": naam,
                    "reden": f"te groot ({len(data) // (1024*1024)} MB, "
                             f"maximaal {MAX_BESTAND_BYTES // (1024*1024)} MB)"})
                continue
            naam = unieke_naam(naam, bezet)
            doel = f"{doelmap}/{naam}"
            res = await self.runtime.exec(
                cid, ["sh", "-c", 'cat > "$1"', "sh", doel],
                stdin=data, timeout=120)
            if res["exit_code"] != 0:
                overgeslagen.append({"name": naam, "reden": res["output"][:200]})
                continue
            bezet.append(naam)
            geschreven.append({"name": naam, "path": doel, "bytes": len(data)})

        self._touch(p)
        self.touch_worker(worker_id)
        if not geschreven and overgeslagen:
            raise HTTPException(
                status_code=400,
                detail="; ".join(f"{o['name']}: {o['reden']}" for o in overgeslagen)[:500])
        return {"dir": doelmap, "files": geschreven, "skipped": overgeslagen}

    # ── de zichtbare browser van een lab ────────────────────────────────────
    #
    # Het pakket `browser-vnc` zet een X-scherm, een vensterbeheerder en een
    # VNC-brug klaar. Wat het NIET doet is een browser starten: die kwam pas in
    # beeld zodra de agent zijn eerste `browser_*`-tool aanriep. Wie het tabblad
    # opende om zelf ergens in te loggen — en dat is waar dit pakket voor is —
    # keek dus naar een leeg bureaublad, zonder aanwijzing wat eraan te doen.
    #
    # De browser starten gebeurt via dezelfde MCP-sessie die de agent gebruikt,
    # en niet met een eigen `chromium &`. Dat is geen omweg: twee Chromiums op
    # hetzelfde `--user-data-dir` weigert Chrome ("profile appears to be in
    # use"), dus een eigen exemplaar zou de agent later precies blokkeren.
    # Via de sessiepool is het één browser die jullie allebei gebruiken — en
    # dat is ook de bedoeling: jij logt in, de agent werkt verder in die sessie.

    BROWSER_EXTRA = "browser-vnc"
    BROWSER_MCP_SLUG = "playwright-lab"

    async def browser_status(self, lab_id: str) -> Dict[str, Any]:
        """Draait er een browser, en zo nee: kan er een gestart worden?"""
        p = self.get(lab_id)
        heeft_pakket = self.BROWSER_EXTRA in [str(x) for x in (p.extras or [])]
        uit: Dict[str, Any] = {
            "pakket": heeft_pakket,
            "lab_draait": p.status == "running",
            "vnc": False,
            "browser_draait": False,
            "server": None,
        }
        if not (heeft_pakket and p.status == "running"):
            return uit
        server = self._browser_server()
        uit["server"] = server.slug if server is not None else None
        try:
            cid = self._require_running(p)
        except HTTPException:
            return uit
        # De [c]-truc: zonder de blokhaken vindt grep zijn eigen commandoregel
        # en meldt hij altijd dat er een browser draait.
        res = await self.runtime.exec(cid, ["sh", "-c",
            "curl -sf -o /dev/null http://127.0.0.1:6080/vnc.html && echo VNC; "
            "ps -eo args 2>/dev/null | grep -qE '[c]hrome|[c]hromium' && echo BROWSER; "
            "true"], timeout=20)
        uitvoer = res.get("output") or ""
        uit["vnc"] = "VNC" in uitvoer
        uit["browser_draait"] = "BROWSER" in uitvoer
        return uit

    def _browser_server(self):
        """De MCP-server die de ZICHTBARE browser draait. Op slug en niet op
        naam: de naam is voor mensen en mag wijzigen."""
        from models.mcp_server import MCPServer
        return (self.db.query(MCPServer)
                .filter(MCPServer.slug == self.BROWSER_MCP_SLUG,
                        MCPServer.location == "lab",
                        MCPServer.is_enabled == True)  # noqa: E712
                .first())

    async def start_browser(self, lab_id: str, *, url: Optional[str] = None) -> Dict[str, Any]:
        """Start een browser in het lab (of stuur een al draaiende naar `url`).

        Draait er al een, dan navigeert deze aanroep hem gewoon — dat is precies
        wat je wilt als je het tabblad opnieuw opent: geen tweede venster, wel
        de pagina die je vroeg.
        """
        p = self.get(lab_id)
        if self.BROWSER_EXTRA not in [str(x) for x in (p.extras or [])]:
            raise HTTPException(
                status_code=409,
                detail="Dit lab heeft het pakket 'Zelf inloggen in de browser van het lab' "
                       "niet aan staan — vink het aan bij Inrichting.")
        cid = self._require_running(p)
        server = self._browser_server()
        if server is None:
            raise HTTPException(
                status_code=409,
                detail=("De MCP-server voor de zichtbare browser bestaat niet of staat uit. "
                        "Richt het lab opnieuw in (Inrichting → Uitvoeren); het pakket zet "
                        "hem dan terug."))

        from services.mcp import mcp_client
        doel = (url or "").strip() or "about:blank"
        try:
            await mcp_client.call_tool(server, "browser_navigate", {"url": doel},
                                       lab_container_id=cid, db=self.db, lab_id=lab_id)
        except Exception as exc:  # noqa: BLE001 — een MCP-fout is hier een toestand
            raise HTTPException(
                status_code=502,
                detail=f"De browser kon niet gestart worden: {str(exc)[:400]}")
        self._touch(p)
        return {"ok": True, "url": doel, **(await self.browser_status(lab_id))}

    async def stop_browser(self, lab_id: str) -> Dict[str, Any]:
        """De browser (en zijn MCP-proces) afsluiten. Het profiel op /workspace
        blijft staan, dus een login die je net gedaan hebt gaat niet verloren —
        dit is de knop voor 'hij hangt, begin opnieuw'."""
        p = self.get(lab_id)
        cid = self._require_running(p)
        from services.mcp.lab_session_pool import close_for_container
        gesloten = await close_for_container(cid)
        # Wat de sessiepool niet opruimt (een browser die zijn ouder overleefde)
        # alsnog: anders blijft het profiel op slot en start de volgende niet.
        await self.runtime.exec(cid, ["sh", "-c",
            "pkill -f '[c]hrome' >/dev/null 2>&1; pkill -f '[c]hromium' >/dev/null 2>&1; true"],
            timeout=20)
        return {"ok": True, "sessies_gesloten": gesloten}

    async def read_lab_file_raw(self, lab_id: str, path: str, *,
                                worker_id: Optional[int] = None,
                                max_bytes: int = 4_000_000) -> Optional[str]:
        """Een bestand volledig uit het lab halen.

        Niet via read_file: die kapt af (`head -c 200000`, en de exec-uitvoer
        zelf op 60k tekens). Voor een tokencache is dat funest — je krijgt dan
        een half JSON-bestand dat er op het oog uitziet als een sessie en het
        nergens doet. Vandaar base64 met een ruime grens: geen afkapping, geen
        gedoe met tekensets, en een lengte die we kunnen controleren."""
        import base64 as _b64
        p = self.get(lab_id)
        cid = self._require_running(p, worker_id)
        veilig = self._safe_path(path)
        res = await self.runtime.exec(
            cid, ["sh", "-c", f"test -f '{veilig}' && base64 -w0 '{veilig}' || true"],
            timeout=60, max_chars=int(max_bytes * 1.4))
        data = (res.get("output") or "").strip()
        if not data or res.get("truncated"):
            return None
        try:
            return _b64.b64decode(data).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None

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
            "worker_count": int(getattr(p, "worker_count", 1) or 1),
            "min_workers": int(getattr(p, "min_workers", 1) or 1),
            "max_workers": int(getattr(p, "max_workers", 1) or 1),
            "security_profile": getattr(p, "security_profile", None) or "generiek",
            "workers": [{"id": w.id, "index": w.index, "status": w.status,
                         "container_id": (w.container_id or "")[:12] or None,
                         "network_alias": w.network_alias,
                         "provision_status": w.provision_status,
                         "last_used_at": w.last_used_at, "error": w.error}
                        for w in self.workers(p.id)],
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


# Labs waarvoor tijdens een lopende inrichtronde opnieuw om inrichten is
# gevraagd. Zie _na_inrichten.
_PROVISION_NOGMAALS: set = set()


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
        # Niet zomaar weggooien: een werker die tijdens een lopende ronde wordt
        # bijgezet staat niet in de lijst die díé ronde afwerkt, en bleef dus
        # voorgoed op "pending" staan. Hij is dan niet claimbaar, dus de
        # autoscaler leek op te schalen terwijl er niets bij kwam.
        _PROVISION_NOGMAALS.add(lab_id)
        log.infox("Inrichten loopt al; ronde erachteraan ingepland", lab_id=lab_id)
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # geen draaiende loop (script/test) — dan niet
        log.warningx("Inrichten niet gestart: geen event loop", lab_id=lab_id)
        return False
    _PROVISION_NOGMAALS.discard(lab_id)
    task = loop.create_task(_provision_worker(lab_id, force=force))
    _PROVISION_TASKS[lab_id] = task
    task.add_done_callback(lambda _t: _na_inrichten(lab_id))
    return True


def _na_inrichten(lab_id: str) -> None:
    """Is er tijdens deze ronde nog om inrichten gevraagd, doe dan meteen een
    tweede. Zonder dit blijft een werker die halverwege bijkwam ongeprovisioneerd
    achter — en die wordt nooit claimbaar, dus het opschalen levert niets op."""
    _PROVISION_TASKS.pop(lab_id, None)
    if lab_id in _PROVISION_NOGMAALS:
        _PROVISION_NOGMAALS.discard(lab_id)
        log.infox("Nog een inrichtronde: er kwam onderweg een werker bij", lab_id=lab_id)
        provision_in_background(lab_id)


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
