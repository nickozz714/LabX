"""routers/internal_router.py — loopback endpoint the MCP gateway subprocess
calls back into (see services/mcp/gateway.py's `_delegate_execute`). Not
reachable from outside: guarded by a per-process shared secret, not JWT."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy.orm import Session

from db.database import SessionLocal
from services.mcp.internal_auth import INTERNAL_MCP_TOKEN
from services.mcp.tool_execution_service import ToolExecutionService

router = APIRouter(prefix="/internal/mcp", tags=["internal"])


async def _task_start_background(db: Session, payload: Dict[str, Any],
                                 lab_id: Optional[str], args: Dict[str, Any]) -> Dict[str, Any]:
    """Model-initiated background task (the gateway's task__start_background
    builtin). The recursion guard is the hard backstop behind the gateway's
    enforcement-by-absence: a background run's gateway doesn't even register
    the task tools, but a hand-crafted call must ALSO be refused — unbounded
    task-spawning trees are the failure mode."""
    if payload.get("is_background"):
        return {"error": "Een achtergrondtaak mag zelf geen nieuwe achtergrondtaken starten."}
    thread_id = (payload.get("thread_id") or "").strip()
    prompt = str(args.get("prompt") or "").strip()
    if not lab_id or not thread_id:
        return {"error": "task__start_background vereist een lab- en threadgebonden chatsessie."}
    if not prompt:
        return {"error": "task__start_background vereist een niet-lege prompt."}
    from models.thread import Thread
    from routers.chat_router import _history_for_prompt
    from services.agent import background_runs
    t = db.get(Thread, thread_id)
    if not t:
        return {"error": f"Thread {thread_id} niet gevonden."}
    history = _history_for_prompt(db, thread_id)
    history.append({"role": "user", "content": prompt})
    # Een achtergrondtaak draait op het STANDAARDMODEL, niet op dat van het
    # lab. Een lab dat op Opus staat, staat daar voor het denkwerk in de chat en
    # aan de tickets; achtergrondwerk is bijna altijd volghouden en verzamelen —
    # een pipeline in de gaten houden, een lange scan uitzitten — en dat uren
    # lang op het duurste model laten lopen kost veel en levert niets. Koos de
    # gebruiker voor DEZE chat expliciet een model, dan telt die keuze wel: dat
    # is een bewuste handeling van vlak ervoor.
    from services.settings_service import get_settings
    run = background_runs.start(db, thread_id=thread_id, lab_id=lab_id,
                                history=history, prompt=prompt,
                                model=t.model or get_settings(db).default_model,
                                effort=t.effort)
    return {"result": (f"Achtergrondtaak gestart (id {run.id[:8]}) op model {run.model or 'standaard'}. "
                       f"De taak draait zelfstandig verder; controleer de voortgang later met "
                       f"task__check_background.")}


def _task_check_background(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
    thread_id = (payload.get("thread_id") or "").strip()
    if not thread_id:
        return {"error": "task__check_background vereist een threadgebonden chatsessie."}
    from models.background_run import BackgroundRun
    rows = (db.query(BackgroundRun)
            .filter(BackgroundRun.thread_id == thread_id)
            .order_by(BackgroundRun.created_at.desc()).limit(10).all())
    if not rows:
        return {"result": "Geen achtergrondtaken voor dit gesprek."}
    lines = []
    for r in rows:
        line = (f"- {r.id[:8]} [{r.status}] {len(r.steps or [])} stappen — "
                f"{(r.prompt or '')[:80]}")
        if r.status == "completed" and r.answer:
            line += f"\n  Resultaat: {r.answer[:600]}"
        elif r.error:
            line += f"\n  Fout: {r.error[:300]}"
        lines.append(line)
    return {"result": "\n".join(lines)}


def _board_tool(db: Session, tool_name: str, lab_id: Optional[str],
                args: Dict[str, Any], worker_id: Optional[int] = None) -> Dict[str, Any]:
    """De board__*-builtins uit de gateway. Het bord volgt uit het lab waar de
    run aan hangt — de agent kiest dus nooit zelf een ander bord."""
    from services.boards.board_service import BoardService
    if not lab_id:
        return {"error": "Board-tools vereisen een labgebonden run."}
    svc = BoardService(db)
    board = svc.board_for_lab(lab_id)
    if board is None:
        return {"error": "Aan dit lab hangt geen board."}

    def _resolve(key: str):
        ticket = svc.ticket_by_key(board.id, key)
        if ticket is None:
            raise ValueError(f"Ticket '{key}' bestaat niet op board '{board.name}'.")
        return ticket

    try:
        if tool_name == "board__list_tickets":
            limit = int(args.get("limit") or 50)
            rows = svc.list_tickets(board.id, status=args.get("status") or None, limit=limit)
            if not rows:
                return {"result": "Geen tickets gevonden."}
            lines = [f"Board '{board.name}' — {len(rows)} ticket(s):"]
            for t in rows:
                lines.append(f"- {t.key} [{t.status}] ({t.priority}) {t.title}"
                             + (f" — extern: {t.external_key}" if t.external_key else ""))
            return {"result": "\n".join(lines)}

        if tool_name == "board__get_ticket":
            t = _resolve(str(args.get("key") or ""))
            lines = [f"{t.key} — {t.title}",
                     f"Kolom: {t.status} | Prioriteit: {t.priority}"
                     + (f" | Toegewezen: {t.assignee}" if t.assignee else ""),
                     f"Labels: {', '.join(str(x) for x in (t.labels or [])) or '-'}"]
            if t.external_key:
                lines.append(f"Extern: {t.external_provider} {t.external_key} {t.external_url or ''}")
            lines += ["", "Omschrijving (de opdracht):", (t.description or "(leeg)")]
            lines += ["", "Acceptatiecriteria:", (t.acceptance_criteria or "(nog niet ingevuld)")]
            comments = svc.list_comments(t.id)
            if comments:
                lines += ["", "Opmerkingen:"]
                from services.boards.agent_work import MAX_COMMENT_CHARS, _kort
                for c in comments:
                    # Dezelfde grens als in de startprompt en als in het advies
                    # dat de agent bij het schrijven krijgt: één getal, zodat
                    # wat hij mag schrijven ook is wat hij terugleest.
                    lines.append(f"- [{c.kind}/{c.author}] {_kort(c.body, MAX_COMMENT_CHARS)}")
            return {"result": "\n".join(lines)}

        if tool_name == "board__create_ticket":
            t = svc.create_ticket(board.id, {
                "title": args.get("title"), "description": args.get("description"),
                "acceptance_criteria": args.get("acceptance_criteria"),
                "status": args.get("status"), "priority": args.get("priority"),
                "labels": args.get("labels"),
            }, author="agent")
            return {"result": f"Ticket {t.key} aangemaakt in kolom '{t.status}'."}

        if tool_name == "board__update_ticket":
            t = _resolve(str(args.get("key") or ""))
            payload = {k: v for k, v in args.items()
                       if k in ("title", "description", "acceptance_criteria", "status",
                                "priority", "assignee", "labels")
                       and v is not None}
            if not payload:
                return {"error": "Geef minstens één veld op om bij te werken."}
            updated = svc.update_ticket(t.id, payload, author="agent")
            return {"result": f"{updated.key} bijgewerkt ({', '.join(payload)}); "
                              f"kolom is nu '{updated.status}'."}

        if tool_name == "lab__secret_list":
            from services.lab.secrets import SecretService
            from services.secrets.vault import VaultService

            svc_s = SecretService(db)
            rijen = [{**svc_s.to_dict(r), "herkomst": "dit lab"}
                     for r in svc_s.lijst(str(lab_id))]
            # En de kluis die niet aan één lab hangt: dezelfde namen, dezelfde
            # schrijfwijze, en bruikbaar op méér plekken dan een commando.
            kluis = VaultService(db)
            bestaand = {r["name"] for r in rijen}
            for r in kluis.beschikbaar(str(lab_id) if lab_id else None):
                if r.name in bestaand:
                    continue        # het lab overschrijft de kluis
                rijen.append({**kluis.to_dict(r), "herkomst": "kluis"})
            if not rijen:
                return {"result": ("Er zijn nog geen geheimen. Zet er een met "
                                   "`lab__secret_put` (voor dit lab) of laat er een "
                                   "aanmaken bij Instellingen > Geheimen, en gebruik hem "
                                   "daarna als {{secret:naam}}.")}
            return {"result": {
                "geheimen": rijen,
                "hoe": ("Schrijf {{secret:naam}} waar de waarde zou staan — in een "
                        "commando én in het argument van elke andere tool. LabX vult hem "
                        "vlak voor de aanroep in; jij krijgt de waarde nooit te zien, en "
                        "hij belandt ook niet in het audit-spoor."),
            }}

        if tool_name == "lab__secret_put":
            from services.lab.secrets import SecretService
            try:
                rij = SecretService(db).zet(
                    str(lab_id), naam=str(args.get("name") or "").strip(),
                    waarde=(str(args.get("value") or "").strip() or None),
                    commando=(str(args.get("command") or "").strip() or None),
                    omschrijving=(str(args.get("description") or "").strip() or None),
                    ttl_minuten=(int(args.get("ttl_minutes")) if args.get("ttl_minutes") else None))
            except ValueError as exc:
                return {"error": str(exc)}
            return {"result": (
                f"Geheim '{rij.name}' opgeslagen. Gebruik hem als {{{{secret:{rij.name}}}}} "
                f"in je commando's; de waarde komt daarbij nooit in de tekst van het "
                f"commando terecht."
                + (f" Hij wordt elke {rij.ttl_minutes} minuten vers gemaakt."
                   if rij.kind == "commando" else ""))}

        if tool_name == "board__claim":
            from services.boards.plan_service import PlanService
            rauw = args.get("resources")
            lijst = [rauw] if isinstance(rauw, str) else list(rauw or [])
            return PlanService(db).claim(
                lab_id=str(lab_id), worker_id=worker_id,
                resources=[str(x) for x in lijst],
                reden=str(args.get("reason") or "").strip())

        if tool_name == "board__release":
            from services.boards.plan_service import PlanService
            rauw = args.get("resources")
            lijst = [rauw] if isinstance(rauw, str) else list(rauw or [])
            return PlanService(db).release(
                lab_id=str(lab_id), worker_id=worker_id,
                resources=[str(x) for x in lijst] or None)

        if tool_name == "board__wait_until":
            from services.boards.plan_service import PlanService
            return PlanService(db).wacht_tot(
                lab_id=str(lab_id), worker_id=worker_id,
                minuten=int(args.get("minutes") or 5),
                reden=str(args.get("reason") or "").strip() or "geen reden opgegeven")

        if tool_name == "board__comment_ticket":
            t = _resolve(str(args.get("key") or ""))
            body = str(args.get("body") or "").strip()
            if not body:
                return {"error": "body mag niet leeg zijn."}
            from services.boards.agent_work import MAX_COMMENT_CHARS
            svc.add_comment(t.id, body=body, author="agent")
            if len(body) > MAX_COMMENT_CHARS:
                # Wel plaatsen (weggooien van werk is erger), maar het eerlijk
                # zeggen: bij een volgende run leest hij alleen het begin terug.
                return {"result": (
                    f"Opmerking geplaatst op {t.key}, maar hij is {len(body)} tekens en "
                    f"daarmee langer dan de {MAX_COMMENT_CHARS} die later worden "
                    f"teruggelezen. De staart mis je dus bij een volgende run. Zet het "
                    f"belangrijkste (wat er nog moet gebeuren) in een korte tweede "
                    f"opmerking, of splits deze alsnog.")}
            return {"result": f"Opmerking geplaatst op {t.key}."}
    except ValueError as exc:
        return {"error": str(exc)}
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    return {"error": f"Onbekende board-tool: {tool_name}"}


def _root_error(exc: BaseException) -> BaseException:
    """The mcp SDK's client context managers wrap a tool failure in (nested)
    ExceptionGroups on exit, so str(exc) is the useless 'unhandled errors in
    a TaskGroup (1 sub-exception)' — the actual, actionable message (e.g. a
    pydantic validation error naming the wrong argument) is the innermost
    leaf. Unwrap to that leaf before showing anything to the agent."""
    seen = 0
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions and seen < 10:
        exc = exc.exceptions[0]
        seen += 1
    return exc


def _lab_packages(db: Session, lab_id: Optional[str]) -> Dict[str, Any]:
    """lab__packages: wat kan er in dit lab, wat staat aan, en hoe liep de
    laatste installatie. Als tekst, niet als JSON: het model moet hier een
    beslissing uit halen ("Playwright ontbreekt, dus die zet ik aan"), niet een
    structuur parseren."""
    from models.lab import Lab
    from models.lab_extra import LabExtra
    lab = db.get(Lab, lab_id) if lab_id else None
    if lab is None:
        return {"error": "lab__packages vereist een lab-gebonden chatsessie."}
    rows = (db.query(LabExtra).filter(LabExtra.is_enabled.is_(True))
            .order_by(LabExtra.sort_order, LabExtra.id).all())
    aan = list(lab.extras or [])
    lines = [f"Lab: {lab.name} (image {lab.image})",
             f"Inrichten: {lab.provision_status or 'onbekend'}",
             f"Aan in dit lab: {', '.join(aan) if aan else '(geen)'}",
             "",
             "Beschikbare pakketten:"]
    for r in rows:
        mark = "x" if r.key in aan else " "
        req = f" [vereist: {', '.join(r.requires)}]" if r.requires else ""
        lines.append(f"  [{mark}] {r.key} — {r.label}{req}")
        if r.description:
            lines.append(f"        {r.description}")
    if lab.setup_script:
        lines += ["", "Eigen setup-script:", *(f"  {ln}" for ln in lab.setup_script.splitlines()[:20])]
    log_rows = list(lab.provision_log or [])
    if log_rows:
        lines += ["", "Laatste ronde:"]
        for step in log_rows:
            lines.append(f"  {step.get('status'):>7}  {step.get('key')}")
            if step.get("status") == "error" and step.get("output"):
                tail = str(step["output"]).strip().splitlines()[-3:]
                lines += [f"          {ln}" for ln in tail]
    return {"result": "\n".join(lines)}


async def _lab_install_packages(db: Session, lab_id: Optional[str],
                                args: Dict[str, Any]) -> Dict[str, Any]:
    """lab__install_packages: zet pakketten aan voor DIT lab en installeer ze.

    Het verschil met gewoon `apt-get install` via de shell is niet het
    installeren maar het VASTLEGGEN: wat hier binnenkomt staat op het lab, komt
    terug na een herstart of een opnieuw opgebouwde container, en is zichtbaar
    voor wie later in dit lab werkt."""
    from models.lab import Lab
    from models.lab_extra import LabExtra
    from services.lab.lab_service import LabService
    lab = db.get(Lab, lab_id) if lab_id else None
    if lab is None:
        return {"error": "lab__install_packages vereist een lab-gebonden chatsessie."}
    wanted = [str(x).strip() for x in (args.get("packages") or []) if str(x).strip()]
    script = args.get("setup_script")
    if not wanted and script is None:
        return {"error": "Geef packages (sleutels uit lab__packages) en/of een setup_script."}
    known = {k for (k,) in db.query(LabExtra.key).filter(LabExtra.is_enabled.is_(True)).all()}
    unknown = [k for k in wanted if k not in known]
    if unknown:
        return {"error": f"Onbekende pakketten: {', '.join(unknown)}. "
                         f"Beschikbaar: {', '.join(sorted(known))}. "
                         f"Iets anders nodig? Zet het in setup_script."}
    if not lab.allow_network:
        return {"error": "Dit lab heeft geen netwerktoegang, dus er valt niets te installeren."}
    await LabService(db).ensure_running(str(lab_id))
    # Vastleggen wat er al aan stond VOORDAT update_settings dezelfde rij
    # bijwerkt — daarna is `lab.extras` de nieuwe lijst en zou elk pakket als
    # "stond er al" gemeld worden.
    eerder = list(lab.extras or [])
    merged = list(dict.fromkeys(eerder + wanted))
    await LabService(db).update_settings(
        str(lab_id), extras=merged,
        setup_script=script if script is not None else "__unset__")
    toegevoegd = [k for k in wanted if k not in eerder] or ["(stond al aan)"]
    return {"result": (f"Aangezet: {', '.join(toegevoegd)}. Het installeren loopt op de "
                       f"achtergrond en kan minuten duren (een browser is honderden MB\'s). "
                       f"Controleer met lab__packages; ga ondertussen gerust door met werk "
                       f"dat er niet op wacht.")}


async def _lab_scale(db: Session, lab_id: Optional[str],
                     args: Dict[str, Any]) -> Dict[str, Any]:
    """lab__scale_workers: de agent vraagt om meer handen.

    Het plafond blijft van de mens: een agent die het druk heeft mag niet
    ongelimiteerd containers op de machine zetten. Vraagt hij meer, dan krijgt
    hij wat mocht en hoort hij waar de grens ligt — dat is bruikbaarder dan een
    weigering waar hij niets mee kan."""
    from models.lab import Lab
    from services.lab.lab_service import LabService

    lab = db.get(Lab, lab_id) if lab_id else None
    if lab is None:
        return {"error": "lab__scale_workers vereist een lab-gebonden chatsessie."}
    gevraagd = max(1, int(args.get("count") or 1))
    plafond = max(1, int(getattr(lab, "max_workers", 1) or 1))
    res = await LabService(db).scale(str(lab_id), count=min(gevraagd, plafond))
    tekst = (f"Dit lab heeft nu {res['workers']} werker(s) "
             f"(ondergrens {res['min']}, plafond {res['max']}).")
    if gevraagd > plafond:
        tekst += (f" Je vroeg er {gevraagd}, maar het plafond staat op {plafond} — dat is een "
                  f"instelling van de beheerder, niet iets wat ik hier kan ophogen.")
    if res["toegevoegd"]:
        tekst += (" De nieuwe werker(s) worden nu ingericht en zijn over een paar minuten "
                  "bruikbaar; controleer met lab__packages.")
    return {"result": tekst}


async def _lab_rebuild(db: Session, lab_id: Optional[str],
                       args: Dict[str, Any]) -> Dict[str, Any]:
    """lab__rebuild: het lab opnieuw opbouwen, eventueel op een ander image."""
    from models.lab import Lab
    from services.lab.lab_service import rebuild_in_background
    lab = db.get(Lab, lab_id) if lab_id else None
    if lab is None:
        return {"error": "lab__rebuild vereist een lab-gebonden chatsessie."}
    image = str(args.get("image") or "").strip() or None
    if not rebuild_in_background(str(lab_id), image=image):
        return {"error": "Er loopt al werk voor dit lab (inrichten of opbouwen) — "
                         "controleer met lab__packages en probeer het daarna opnieuw."}
    doel = image or lab.image
    return {"result": (f"Bezig met opnieuw opbouwen op {doel}. /workspace blijft staan; alles "
                       f"daarbuiten wordt opnieuw gemaakt en de aangezette pakketten worden "
                       f"opnieuw geïnstalleerd. Dit duurt enkele minuten — controleer met "
                       f"lab__packages, en verwacht tot die tijd geen shell in dit lab.")}


async def _lab_resource(db: Session, tool_name: str, lab_id: Optional[str],
                        args: Dict[str, Any], worker_id: Optional[int],
                        thread_id: Optional[str]) -> Dict[str, Any]:
    """De claim-tools voor het fysieke spul in een container.

    De HOUDER is de sessie (de thread): die is stabiel over de beurten van één
    gesprek of ticketrun, en hij is ook waarop het vrijgeven na afloop van een
    beurt werkt (zie background_runs._geef_resources_vrij).
    """
    from models.lab import Lab
    from services.lab.resources import ResourceService

    if not lab_id:
        return {"error": "Deze tool werkt alleen in een chat die aan een lab hangt."}
    lab = db.get(Lab, str(lab_id))
    if lab is None:
        return {"error": "Het gekoppelde lab bestaat niet meer."}
    houder = str(thread_id or "").strip()
    if not houder:
        return {"error": "Deze beurt heeft geen sessie; claimen kan hier niet."}
    svc = ResourceService(db)
    label = _houder_label(db, houder)

    if tool_name == "lab__resource__status":
        beschikbaar = svc.voor_lab(lab)
        bezet = {c["resource"]: c for c in svc.actief(str(lab_id), worker_id=worker_id)}
        regels = []
        for r in beschikbaar:
            claim = bezet.get(r.key)
            if claim is None:
                regels.append(f"- {r.key} — vrij ({r.label})")
            elif claim["houder"] == houder:
                regels.append(f"- {r.key} — van jou, tot {claim['verloopt']}")
            else:
                regels.append(f"- {r.key} — bezet door {claim['houder_label'] or 'een ander'} "
                              f"sinds {claim['sinds']}")
        if not regels:
            return {"result": "In dit lab is niets ingesteld om te reserveren."}
        return {"result": "Te reserveren in dit lab:\n" + "\n".join(regels)}

    if tool_name == "lab__resource__release":
        naam = str(args.get("resource") or "").strip().lower()
        aantal = svc.release(lab_id=str(lab_id), houder=houder,
                             resource_keys=[naam] if naam else None)
        if not aantal:
            return {"result": "Je had hier niets gereserveerd."}
        return {"result": f"Vrijgegeven: {naam or 'alles wat je vasthield'} ({aantal})."}

    if tool_name == "lab__resource__claim":
        res = await svc.claim(
            lab=lab, worker_id=worker_id,
            resource_key=str(args.get("resource") or ""),
            houder=houder, houder_soort="sessie", houder_label=label,
            reden=str(args.get("reason") or "").strip() or None,
            wacht_seconden=int(args.get("wait_seconds") or 0))
        if res.get("onbekend"):
            return {"error": res.get("melding")}
        if res.get("ok"):
            deel = "had je al" if res.get("al_van_jou") else "is van jou"
            return {"result": (f"{res['resource']} {deel} tot {res.get('verloopt')}. "
                               f"Geef hem vrij met lab__resource__release zodra je klaar bent.")}
        gewacht = res.get("gewacht_seconden")
        staart = f" (na {gewacht}s wachten)" if gewacht else ""
        return {"result": (f"{res.get('resource')} is bezet door {res.get('bezet_door')} "
                           f"sinds {res.get('sinds')}{staart}. "
                           f"Doe zolang iets anders, of probeer het later opnieuw.")}
    return {"error": f"Onbekende resource-tool: {tool_name}"}


def _houder_label(db: Session, thread_id: str) -> str:
    """Hoe een mens (en een collega-agent) deze houder herkent: het ticket waar
    de sessie aan werkt, anders de titel van het gesprek."""
    from models.board import Ticket
    from models.thread import Thread

    t = db.get(Thread, thread_id)
    if t is None:
        return thread_id[:12]
    ticket = (db.query(Ticket).filter(Ticket.agent_thread_id == thread_id).first())
    if ticket is not None:
        return f"{ticket.key} ({ticket.title})"[:255]
    return (getattr(t, "title", None) or f"chat {thread_id[:8]}")[:255]


@router.post("/execute")
async def execute(payload: Dict[str, Any], x_labx_internal_token: Optional[str] = Header(default=None)):
    if not x_labx_internal_token or x_labx_internal_token != INTERNAL_MCP_TOKEN:
        raise HTTPException(status_code=403, detail="Ongeldig intern token")
    lab_id = payload.get("lab_id")
    args = payload.get("args") or {}
    # De werker waarin deze run werkt (zie gateway: LABX_GATEWAY_WORKER). Leeg
    # = het lab zelf, dus werker 1 — zo werkt een gewone chat en zo werkte
    # alles voordat labs meer dan één container konden hebben.
    worker_id = payload.get("worker_id")
    worker_id = int(worker_id) if worker_id else None
    db: Session = SessionLocal()
    try:
        svc = ToolExecutionService(db)
        if payload.get("tool_name") == "lab__shell_exec":
            if not lab_id:
                raise HTTPException(status_code=400, detail="lab_id is verplicht voor lab__shell_exec")
            # Een lab dat niet meer te starten is (container verdwenen) is een
            # toestand waar de agent zelf iets mee kan — mits hij hem te horen
            # krijgt. Zonder dit werd het een kale 500 waarop hij alleen kon
            # concluderen dat "de server stuk is", en dat is precies hoe een
            # ticket-run een uur lang bleef hangen.
            try:
                result = await svc.execute_builtin_shell(
                    lab_id=lab_id, command=str(args.get("command") or ""),
                    timeout=float(args.get("timeout") or 60), worker_id=worker_id,
                    intent=str(args.get("intent") or ""))
            except HTTPException as exc:
                return {"error": f"lab__shell_exec kan niet: {exc.detail}"}
            except RuntimeError as exc:
                return {"error": f"lab__shell_exec mislukt: {str(exc)[:600]}"}
            return {"result": result.get("output")}
        if payload.get("tool_name") == "lab__start":
            if not lab_id:
                raise HTTPException(status_code=400, detail="lab_id is verplicht voor lab__start")
            from services.lab.lab_service import LabService
            try:
                res = await LabService(db).ensure_running(lab_id)
            except HTTPException as exc:
                return {"error": f"lab__start kan niet: {exc.detail}"}
            except RuntimeError as exc:
                return {"error": f"lab__start mislukt: {str(exc)[:600]}"}
            return {"result": ("Lab gestart." if res.get("started") else "Lab draaide al.")}
        if payload.get("tool_name") == "lab__write_file":
            if not lab_id:
                raise HTTPException(status_code=400, detail="lab_id is verplicht voor lab__write_file")
            from services.lab.lab_service import LabService
            await LabService(db).ensure_running(lab_id)
            try:
                res = await LabService(db).write_file(
                    lab_id, str(args.get("path") or ""), str(args.get("content") or ""),
                    worker_id=worker_id)
            except HTTPException as exc:
                return {"error": f"lab__write_file mislukt: {exc.detail}"}
            return {"result": f"Geschreven: {res['path']} ({res['bytes']} bytes)"}
        if payload.get("tool_name") == "lab__packages":
            return _lab_packages(db, lab_id)
        if payload.get("tool_name") == "lab__install_packages":
            return await _lab_install_packages(db, lab_id, args)
        if payload.get("tool_name") == "lab__scale_workers":
            return await _lab_scale(db, lab_id, args)
        if payload.get("tool_name") == "lab__rebuild":
            return await _lab_rebuild(db, lab_id, args)
        if str(payload.get("tool_name") or "").startswith("lab__resource__"):
            return await _lab_resource(db, str(payload["tool_name"]), lab_id, args,
                                       worker_id, payload.get("thread_id"))
        if payload.get("tool_name") == "task__start_background":
            return await _task_start_background(db, payload, lab_id, args)
        if payload.get("tool_name") == "task__check_background":
            return _task_check_background(db, payload)
        if str(payload.get("tool_name") or "").startswith("board__"):
            return _board_tool(db, str(payload["tool_name"]), lab_id, args, worker_id)
        tool_id = payload.get("tool_id")
        if tool_id is None:
            raise HTTPException(status_code=400, detail="tool_id of tool_name is verplicht")
        try:
            result = await svc.execute_tool(int(tool_id), args, lab_id=lab_id,
                                            worker_id=worker_id,
                                            # De gateway stuurt zijn thread al mee; die is
                                            # stabiel over de beurten van één gesprek of
                                            # ticketrun, en dus precies de korrel waarop een
                                            # server als Nectar zijn sessie-baan wil hangen.
                                            sessie=(payload.get("thread_id") or "").strip() or None)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            # Return the tool's real error MESSAGE instead of a bare 500: the
            # agent must be able to read "skill_uid: Missing required
            # argument" and fix its own call — an opaque "500 Internal Server
            # Error" reads as a broken server and makes it give up entirely
            # (which is exactly what happened with Nectar's skill_get: the
            # agent guessed parameter name `uid`, the validation error naming
            # the right field never reached it, and it reported the server
            # as systemically down).
            return {"error": str(_root_error(exc))[:4000]}
        return {"result": result}
    finally:
        db.close()
