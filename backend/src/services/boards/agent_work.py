"""
services/boards/agent_work.py

"Werk dat de AI kan oppakken en het ticket kan aanvullen."

Een agent-run op een ticket is een gewone LabX-achtergrondtaak — zelfde
runtime, zelfde lab, zelfde guard, zelfde live-eventstroom — met drie dingen
eromheen:

1. **Een eigen thread per ticket**, zodat het gesprek over LAB-12 herleesbaar
   blijft en een tweede run op hetzelfde ticket de context van de eerste ziet.
2. **Een prompt die het ticket beschrijft** én de agent vertelt WAAR hij wat
   neerzet. Die scheiding is hard: de omschrijving is de OPDRACHT, opmerkingen
   zijn het WERKLOGBOEK, en acceptatiecriteria zijn het MEETLINT. Zonder die
   regel schrijft een agent zijn bevindingen zowel in een opmerking als onder
   de omschrijving, en is na twee runs niet meer te zien wat er oorspronkelijk
   gevraagd werd.
3. **Een afloop-hook** die het eindantwoord als opmerking op het ticket zet en
   de kolom verschuift. De hook is de vangnet-kant: ook een agent die vergeet
   `board__comment_ticket` aan te roepen laat zo een leesbaar spoor achter.

De hook overleeft een backend-herstart niet (het proces is weg); daarvoor is
`reconcile_on_start` — tickets die "running" claimen zonder levende run worden
teruggezet, net als bij achtergrondtaken zelf.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.board import Board, Ticket
from models.thread import Thread
from services.boards.board_service import BoardService

log = get_logger(__name__)

_MAX_ANSWER_IN_COMMENT = 8000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Hoe lang een opmerking mag zijn. Eén getal voor lezen én schrijven: de agent
# krijgt te horen dat hij hieronder moet blijven, en precies zoveel krijgt hij
# ook terug te lezen. Zou het schrijfadvies ruimer zijn dan de leesgrens, dan
# bouwt hij netjes een verslag dat hij later zelf niet meer compleet ziet.
MAX_COMMENT_CHARS = 3000
# Oudere opmerkingen blijven korter: die zijn context, geen werkinstructie.
OUDERE_COMMENT_CHARS = 1500
# Zoveel van de jongste opmerkingen komen onverkort door.
RECENTE_OPMERKINGEN = 3


def _kort(tekst: Optional[str], grens: int) -> str:
    """Inkorten met een spoor. Stil afkappen is het probleem, niet het
    afkappen zelf: dan denkt de lezer dat hij alles heeft."""
    body = (tekst or "").strip()
    if len(body) <= grens:
        return body
    return body[:grens] + f"\n  […afgekapt, {len(body) - grens} tekens]"


def _ticket_prompt(board: Board, ticket: Ticket, comments: List[Any],
                   extra_instruction: Optional[str] = None) -> str:
    lines = [
        f"Je pakt ticket {ticket.key} op van het board '{board.name}'.",
        "",
        f"## {ticket.key} — {ticket.title}",
        f"Status: {ticket.status} | Prioriteit: {ticket.priority}"
        + (f" | Toegewezen aan: {ticket.assignee}" if ticket.assignee else ""),
    ]
    if ticket.labels:
        lines.append(f"Labels: {', '.join(str(x) for x in ticket.labels)}")
    if ticket.external_key:
        lines.append(f"Externe bron: {ticket.external_provider} {ticket.external_key}"
                     + (f" ({ticket.external_url})" if ticket.external_url else ""))
    lines.append("")
    lines.append("### Omschrijving (de opdracht)")
    lines.append((ticket.description or "").strip() or "(geen omschrijving)")

    criteria = (ticket.acceptance_criteria or "").strip()
    lines.append("")
    lines.append("### Acceptatiecriteria")
    lines.append(criteria or "(nog niet ingevuld)")

    visible = [c for c in comments if c.kind == "comment"]
    if visible:
        lines.append("")
        lines.append("### Eerdere opmerkingen (het werklogboek)")
        # De JONGSTE opmerkingen onverkort, oudere ingekort. Een run die eerder
        # werk voortzet leunt op zijn laatste eigen notitie, en juist daar stond
        # de staart — "wat er nog moet gebeuren" — buiten de oude grens van 1500
        # tekens. Wie met de inleiding van zijn eigen aantekeningen begint en
        # zonder de conclusie, doet het werk over of slaat het over.
        recent = visible[-RECENTE_OPMERKINGEN:]
        for c in visible[-15:]:
            grens = MAX_COMMENT_CHARS if c in recent else OUDERE_COMMENT_CHARS
            lines.append(f"- [{c.author}] {_kort(c.body, grens)}")

    if (board.agent_instruction or "").strip():
        lines.append("")
        lines.append("### Vaste werkafspraken op dit board")
        lines.append(board.agent_instruction.strip())

    if (extra_instruction or "").strip():
        lines.append("")
        lines.append("### Extra instructie voor deze run")
        lines.append(extra_instruction.strip())

    lines += [
        "",
        "### Wat er van je verwacht wordt",
        "Voer het werk daadwerkelijk uit in het lab (shell, bestanden, MCP-tools) —"
        " niet alleen beschrijven wat je zou doen.",
        "",
        "**Waar je wat neerzet — houd dit strikt gescheiden:**",
        f"- **Je bevindingen, voortgang, resultaten en vragen** gaan als OPMERKING op"
        f" {ticket.key}, met `board__comment_ticket`. Dat is het werklogboek en de"
        " enige plek waar verslag hoort.",
        "- **Moet je wachten op iets dat minuten duurt** (een pipeline, een"
        " notebook-run, een deploy)? Gebruik `board__wait_until` — alleen dit"
        " ticket wacht, je werker komt vrij, de planning gaat verder met het"
        " volgende ticket en dit ticket wordt op tijd opnieuw opgepakt. Blijf"
        " niet wachten of pollen in het lab: dat houdt een werker bezet,"
        " verbrandt je context en telt mee voor de tijdslimiet van deze run."
        " Zet wél eerst in een opmerking waar je gebleven bent; bij het"
        " hervatten is dat je enige context.",
        "- **Je bent mogelijk niet de enige.** Een planning kan meerdere tickets"
        " tegelijk draaien in dit lab: aparte containers, maar één /workspace,"
        " één az-sessie, één omgeving. Meld daarom met `board__claim` waar je"
        " aan gaat zitten vóórdat je het wijzigt — een pipeline, een tabel, een"
        " map. Krijg je te horen dat iemand anders hem heeft, ga er dan niet"
        " toch in: pak iets anders uit dit ticket op of wacht. Ben je klaar met"
        " een bron, geef hem vrij met `board__release`, dan kan een wachtend"
        " ticket door. Lezen mag altijd; claimen is voor wat je VERANDERT.",
        "- **Zet nooit werk op de achtergrond en meld dan dat je klaar bent.**"
        " Deze run is één aanroep: zodra jij je eindantwoord geeft, valt het"
        " proces om. Een subagent met `run_in_background` sterft daar mee, een"
        " `nohup`-commando in het lab ook, en het bericht dat je belooft komt"
        " nooit. Dat is geen theorie — precies zo is het opruimwerk van een"
        " eerder ticket verdampt terwijl het ticket er afgehandeld uitzag. Doe"
        " het werk in deze run, of zet met `board__wait_until` de planning stil"
        " tot het af is. Een subagent op de VOORGROND (gewoon `Agent` zonder"
        " achtergrondvlag) mag wel: daar wacht je op.",
        f"- **Houd een opmerking onder de {MAX_COMMENT_CHARS} tekens.** Zoveel wordt er"
        " later ook teruggelezen — wie hier overheen schrijft, ziet zijn eigen staart"
        " niet terug bij een volgende run, en dat is nu juist het deel met wat er nog"
        " moet gebeuren. Heb je meer te melden: splits het in meerdere opmerkingen,"
        " elk met een eigen kop, en zet het belangrijkste vooraan.",
        "- **De omschrijving is de OPDRACHT, geen verslag.** Laat hem met rust, tenzij"
        " de opdracht zelf onduidelijk of onvolledig blijkt; dan scherp je hem aan met"
        " `board__update_ticket(description=...)`. Plak er NOOIT je bevindingen,"
        " statusupdates of een 'Bevindingen'-kopje onder — die staan al in je"
        " opmerking en horen daar niet dubbel.",
        "- **Acceptatiecriteria** zijn het meetlint: toets je werk eraan voor je"
        " afrondt en benoem per criterium of eraan voldaan is. Staan ze er niet, stel"
        " ze dan zelf op en zet ze met `board__update_ticket(acceptance_criteria=...)`"
        " als korte, toetsbare lijst (Markdown) — dat is een aanscherping van de"
        " opdracht, geen verslag.",
        "",
        "**Eén ticket, één opdracht.** Komt er onderweg ander werk voorbij — een"
        " onderhoudstaak uit de hive (een Pollen), een openstaand klusje in een"
        " ander systeem, iets dat je toevallig kapot ziet — dan is dat niet van"
        f" jou. Je mag de hive raadplegen om {ticket.key} beter te doen, maar het"
        " aannemen van hive-onderhoud hoort niet bij deze run: het kost de tijd"
        " en de context die voor dit ticket bedoeld waren. Zie je iets dat"
        " aandacht verdient, noem het in je eindantwoord en laat het liggen.",
        "",
        "Kom je er niet uit of ontbreekt informatie? Zet dat als opmerking op het"
        " ticket en zeg het expliciet in je eindantwoord — een half afgemaakt ticket"
        " zonder uitleg is het slechtste resultaat.",
        "Sluit af met een kort, concreet eindantwoord: wat je hebt gedaan, of de"
        " acceptatiecriteria gehaald zijn, en wat de volgende stap is. Dat antwoord"
        " wordt automatisch als opmerking op het ticket gezet, dus schrijf het voor"
        " iemand die alleen het ticket leest.",
    ]
    return "\n".join(lines)


# Kolommen waar een ticket nog "niet begonnen" is. Staat het daar en gaat de
# agent ermee aan de slag, dan hoort het bord dat te laten zien.
def _niet_begonnen_kolommen(board: Board) -> set:
    uit = {str(board.agent_column or "")} - {""}
    # De eerste kolom van een bord is per conventie de instroom ("Te doen").
    eerste = (board.columns or [{}])[0].get("key")
    if eerste:
        uit.add(str(eerste))
    return uit


def _zet_op_bezig(db: Session, svc: BoardService, board: Board, ticket: Ticket) -> None:
    """Het ticket naar de werk-kolom zodra de agent begint.

    Waarom dit nodig was: de enige verplaatsing die LabX deed, gebeurde aan het
    EIND van een run en alleen vanuit de oppak-kolom. Een ticket dat je zelf in
    "Te doen" had staan en met een planning liet oppakken, bleef daar dus staan
    terwijl er een uur aan gewerkt werd — en bleef er ook na afloop staan. Het
    bord zag er onaangeroerd uit terwijl het werk allang liep.

    Alleen vanuit een kolom waar het ticket nog niet begonnen was: heeft iemand
    (of de agent zelf) het bewust ergens anders neergezet, dan is dat een
    uitspraak die we niet overschrijven.
    """
    doel = str(getattr(board, "agent_busy_column", None) or "").strip()
    if not doel or ticket.status == doel:
        return
    if ticket.status not in _niet_begonnen_kolommen(board):
        return
    if doel not in {str(c.get("key")) for c in (board.columns or [])}:
        return
    try:
        svc.move_ticket(ticket.id, doel, author="agent")
    except HTTPException as exc:
        log.warningx("Ticket op 'bezig' zetten mislukt", ticket=ticket.key,
                     error=str(exc.detail)[:200])


def _thread_for_ticket(db: Session, board: Board, ticket: Ticket) -> Thread:
    if ticket.agent_thread_id:
        existing = db.get(Thread, ticket.agent_thread_id)
        if existing is not None:
            return existing
    now = _now_iso()
    t = Thread(id=str(uuid4()), title=f"{ticket.key} — {ticket.title}"[:255],
               lab_id=board.lab_id, source="board", created_at=now, updated_at=now)
    db.add(t)
    db.commit()
    ticket.agent_thread_id = t.id
    db.commit()
    return t


async def start_ticket_run(db: Session, ticket_id: int, *,
                     extra_instruction: Optional[str] = None,
                     trigger: str = "handmatig",
                     lab_worker_id: Optional[int] = None) -> Dict[str, Any]:
    """Zet de agent op één ticket. Geeft de run-info terug; de run zelf loopt
    non-blocking door (zelfde patroon als een achtergrondtaak in de chat)."""
    from models.lab import Lab
    from services.agent import background_runs

    svc = BoardService(db)
    ticket = svc.get_ticket(ticket_id)
    board = svc.get_board(ticket.board_id)

    if not board.lab_id:
        raise HTTPException(status_code=409,
                            detail="Dit board heeft geen gekoppeld lab — de agent heeft een lab nodig om in te werken")
    lab = db.get(Lab, board.lab_id)
    if not lab:
        raise HTTPException(status_code=409, detail="Het lab van dit board bestaat niet meer")
    if lab.status != "running":
        # Een lab dat uit staat is geen reden om te weigeren: aanzetten is
        # precies wat degene die op "agent" drukt bedoelde. Lukt dát niet, dan
        # is er pas echt iets aan de hand.
        from services.lab.lab_service import LabService
        await LabService(db).ensure_running(lab.id)
    if ticket.agent_state == "running" and ticket.agent_run_id and \
            background_runs.is_active(ticket.agent_run_id):
        raise HTTPException(status_code=409, detail="De agent werkt al aan dit ticket")

    thread = _thread_for_ticket(db, board, ticket)
    prompt = _ticket_prompt(board, ticket, svc.list_comments(ticket.id), extra_instruction)
    _zet_op_bezig(db, svc, board, ticket)

    # Geen werker meegekregen (een handmatige start vanaf het ticket): zelf een
    # vrije zoeken. Zonder dit landde ELK handmatig gestart ticket in de
    # container van het lab zelf — drie tickets tegelijk starten gaf drie runs
    # in werker 1, die daar om dezelfde bestanden, processen en az-sessie
    # vochten, terwijl werker 2 en 3 niets deden. Alleen de planner koos een
    # werker; de knop op het ticket deed dat niet.
    if lab_worker_id is None:
        from services.lab.lab_service import LabService
        lab_svc = LabService(db)
        vrij = lab_svc.vrije_werker(lab)
        if vrij is not None:
            lab_worker_id = vrij.id
        else:
            # Niets vrij. Deze run gaat op werker 1 — je drukte bewust op
            # start en minutenlang wachten op een nieuwe container is geen
            # antwoord. Maar wél de autoscaler aantikken, zodat de VOLGENDE
            # start er een heeft. Zonder dit bleef een lab op één werker
            # hangen zolang je alleen met de hand startte: de autoscaler werd
            # uitsluitend door de planner aangeroepen, dus de werkers die
            # ervoor bestaan werden opgeruimd wegens inactiviteit en kwamen
            # nooit terug. De hostmeter beslist of het kan; zit de machine
            # vol, dan gebeurt er niets.
            try:
                await lab_svc.ensure_extra_worker(lab.id)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Bijschalen na handmatige start overgeslagen",
                             lab_id=lab.id, error=str(exc)[:200])

    run = background_runs.start(
        db, thread_id=thread.id, lab_id=board.lab_id,
        history=[{"role": "user", "content": prompt}], prompt=prompt,
        mode="background",
        # De werker waarin deze run moet werken. Zonder werker landt alles in
        # de container van het lab zelf — zoals altijd, toen er één was.
        lab_worker_id=lab_worker_id,
    )

    ticket.agent_state = "running"
    ticket.agent_run_id = run.id
    ticket.agent_last_error = None
    ticket.updated_at = _now_iso()
    db.commit()

    svc.add_comment(ticket.id, kind="activity", author="agent",
                    body=f"Agent gestart ({trigger}) — run {run.id[:8]}.")

    background_runs.on_finish(run.id, _make_finish_hook(ticket.id, started_at=_now_iso()))
    log.infox("Agent-run op ticket gestart", ticket=ticket.key, board=board.name,
              run_id=run.id, trigger=trigger)
    return {"run_id": run.id, "thread_id": thread.id, "ticket_id": ticket.id,
            "ticket_key": ticket.key, "status": "running",
            "lab_worker_id": lab_worker_id}


def _agent_commented_since(db: Session, ticket_id: int, since: str) -> bool:
    """Heeft de agent tijdens deze run zélf al verslag gedaan?"""
    from models.board import TicketComment
    return (db.query(TicketComment)
            .filter(TicketComment.ticket_id == ticket_id,
                    TicketComment.kind == "comment",
                    TicketComment.author == "agent",
                    TicketComment.created_at >= since)
            .first()) is not None


def _make_finish_hook(ticket_id: int, *, started_at: str):
    def _hook(db: Session, run) -> None:
        svc = BoardService(db)
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return
        board = db.get(Board, ticket.board_id)
        status = getattr(run, "status", None) or "failed"

        # Werk dat op de achtergrond is gezet, is met deze run meegestorven.
        # Dat moet vóór de afhandeling bekend zijn: een run die zijn opdracht
        # aan een achtergrond-subagent gaf, is niet klaar — hij is gestopt.
        from services.agent.claude_cli_provider import achtergrond_subagents
        verloren = achtergrond_subagents(getattr(run, "steps", None))
        if verloren and status == "completed":
            status = "onafgemaakt"

        if status == "completed":
            answer = (getattr(run, "answer", None) or "").strip()
            ticket.agent_state = "done"
            ticket.agent_last_error = None
            # Het eindantwoord is een VANGNET, geen tweede verslag: heeft de
            # agent tijdens de run zelf al een opmerking geplaatst, dan staat
            # het er al en levert dit alleen dubbele tekst op.
            if answer and not _agent_commented_since(db, ticket.id, started_at):
                svc.add_comment(ticket.id, kind="comment", author="agent",
                                body=answer[:_MAX_ANSWER_IN_COMMENT])
            # Verplaats alleen vanuit een kolom waar WIJ het ticket hebben
            # neergezet (de oppak-kolom, of de werk-kolom van het starten). Een
            # agent die het zelf ergens anders heeft neergezet — review,
            # blocked, klaar — weet beter dan deze hook.
            done_col = (board.agent_done_column if board else None)
            vanzelf = {str(board.agent_column or ""),
                       str(getattr(board, "agent_busy_column", None) or "")} - {""} if board else set()
            if done_col and board and ticket.status in vanzelf:
                try:
                    svc.move_ticket(ticket.id, done_col, author="agent")
                except HTTPException as exc:
                    log.warningx("Ticket verplaatsen na agent-run mislukt",
                                 ticket=ticket.key, error=str(exc.detail))
        elif status == "limited":
            # Geen fout: de dienst laat ons even niet werken. Het ticket blijft
            # staan waar het staat en gaat terug in de wachtstand, zodat het
            # vanzelf opnieuw opgepakt wordt — niet naar de mislukt-hoek, want
            # daar gaat iemand naar kijken terwijl er niets te repareren is.
            hervat = getattr(run, "resume_at", None)
            ticket.agent_state = "queued"
            ticket.agent_last_error = None
            svc.add_comment(
                ticket.id, kind="activity", author="agent",
                body=("Gepauzeerd op een gebruikslimiet"
                      + (f"; gaat automatisch verder om {str(hervat)[11:16]} UTC."
                         if hervat else ".")
                      + " Er is niets misgegaan en er is niets verloren: bij het hervatten "
                        "leest de agent zijn eigen opmerkingen terug."))
        elif status == "onafgemaakt":
            # Het eindantwoord van zo'n run klinkt als een afronding ("loopt nog
            # op de achtergrond, je hoort ervan"). Dat bericht komt nooit. Het
            # ticket blijft daarom in zijn kolom staan en gaat op `failed`, zodat
            # het opnieuw opgepakt kan worden in plaats van klaar te lijken.
            ticket.agent_state = "failed"
            ticket.agent_last_error = (
                "Run zette werk op de achtergrond (" + "; ".join(verloren)
                + ") en eindigde; die subagents zijn met het proces gestopt.")[:2000]
            antwoord = (getattr(run, "answer", None) or "").strip()
            svc.add_comment(
                ticket.id, kind="activity", author="agent",
                body=("Deze run is NIET afgerond. Het werk is gedelegeerd aan een "
                      "subagent op de achtergrond (" + "; ".join(verloren) + "), en "
                      "die stopt zodra de run zijn antwoord geeft — een headless run "
                      "kent geen 'later'. Wat die subagent zou doen, is dus niet "
                      "gebeurd; pak het ticket opnieuw op."
                      + (f"\n\nWat de run zelf meldde:\n{antwoord[:1500]}" if antwoord else "")))
        else:
            ticket.agent_state = "failed"
            ticket.agent_last_error = (getattr(run, "error", None)
                                       or f"Run eindigde als '{status}'")[:2000]
            svc.add_comment(ticket.id, kind="activity", author="agent",
                            body=f"Agent-run {status}: {ticket.agent_last_error[:500]}")
        # De CLI-sessie van deze run overnemen op de thread van het ticket, zodat
        # je er in de chat in KUNT doorpraten: een volgende beurt hervat dan
        # precies deze sessie, met alles wat de agent onderweg gezien heeft.
        #
        # Een achtergrondrun deelt bewust nooit een lopende sessie (het
        # sessiebestand van de CLI heeft geen bescherming tegen twee
        # schrijvers). Ná afloop is dat bezwaar weg: er schrijft niemand meer
        # in. Draait er op dit moment tóch een gesprek in deze thread, dan
        # blijven we eraf — die beurt heeft zijn eigen sessie en die mag niet
        # onder zijn handen verwisseld worden.
        sessie = getattr(run, "cli_session_id", None)
        if sessie and ticket.agent_thread_id:
            from models.thread import Thread as _Thread
            from services.agent.background_runs import active_foreground_run
            thread = db.get(_Thread, ticket.agent_thread_id)
            if thread is not None and not active_foreground_run(db, thread.id):
                thread.cli_session_id = sessie
                thread.updated_at = _now_iso()

        # Zag deze run eigen CLI-tools die er niet horen? Dan hoort dat op het
        # ticket, niet alleen in een logregel die niemand leest. Dit is hoe een
        # `ScheduleWakeup` zich verraadt: de agent denkt dat hij later terugkomt
        # en de run eindigt gewoon.
        from services.agent.claude_cli_provider import onverwachte_native_tools
        vreemd = onverwachte_native_tools(getattr(run, "steps", None))
        if vreemd:
            svc.add_comment(ticket.id, kind="activity", author="agent",
                            body=(f"Let op: deze run gebruikte CLI-tools die LabX niet ondersteunt "
                                  f"({', '.join(vreemd)}). Die doen hier niets — werk dat daarvan "
                                  f"afhing is dus niet gebeurd."))
        ticket.updated_at = _now_iso()
        db.commit()

        # Naar buiten melden. Dit is bewust het LAATSTE wat de hook doet: alles
        # wat het ticket aangaat is dan al vastgelegd, dus een melding kan nooit
        # de reden zijn dat er iets niet bijgewerkt is.
        _meld_over_ticket(ticket, board, run, status)
    return _hook


def _meld_over_ticket(ticket: Ticket, board: Optional[Board], run, status: str) -> None:
    """Eén melding per afgelopen agent-run, met de samenvatting erin.

    De DRIE afloopsoorten krijgen elk een eigen gebeurtenis, want je wilt ze
    verschillend kunnen aanzetten: een run die goed ging is een fijn bericht,
    een run die je aandacht nodig heeft is er een waar je iets mee moet.
    """
    from services.notify.notify_service import meld

    context = {
        "thread_id": ticket.agent_thread_id,
        "ticket_id": ticket.id,
        "ticket_key": ticket.key,
        "board_id": ticket.board_id,
        "board_name": board.name if board else None,
        "run_id": getattr(run, "id", None),
        "lab_id": board.lab_id if board else None,
    }
    samenvatting = (getattr(run, "answer", None) or "").strip()
    if status == "limited":
        hervat = str(getattr(run, "resume_at", None) or "")
        meld("storing", f"{ticket.key} gepauzeerd — gebruikslimiet"[:200],
             (f"Het werk aan {ticket.key} ligt stil tot "
              f"{hervat[11:16] + ' UTC' if hervat else 'de limiet opengaat'} en gaat daarna "
              f"vanzelf verder. Er is niets misgegaan."), context)
        return
    if status == "completed":
        meld("run_klaar", f"{ticket.key} klaar — {ticket.title}"[:200],
             samenvatting or "De agent is klaar maar liet geen samenvatting achter.",
             context)
    elif status == "onafgemaakt":
        meld("aandacht_nodig", f"{ticket.key} NIET afgerond — {ticket.title}"[:200],
             (ticket.agent_last_error or "De run stopte zonder het werk af te maken.")
             + (f"\n\nWat de run meldde:\n{samenvatting}" if samenvatting else ""),
             context)
    else:
        meld("run_mislukt", f"{ticket.key} mislukt — {ticket.title}"[:200],
             ticket.agent_last_error or f"Run eindigde als '{status}'.", context)


def reconcile_on_start(db: Session) -> int:
    """Na een herstart zijn alle in-flight runs weg (background_runs zet ze op
    'interrupted'), maar het ticket claimt nog 'running'. Zonder deze opruiming
    kan zo'n ticket nooit meer opgepakt worden."""
    rows = db.query(Ticket).filter(Ticket.agent_state == "running").all()
    for t in rows:
        t.agent_state = "failed"
        t.agent_last_error = "Backend herstart tijdens de agent-run"
        t.updated_at = _now_iso()
    if rows:
        db.commit()
    return len(rows)


async def pick_up_column(db: Session, board_id: int, *, column: Optional[str] = None,
                         max_tickets: int = 1, trigger: str = "schedule") -> List[Dict[str, Any]]:
    """Pak werk uit een kolom op — als PLANNING, niet als losse runs.

    Dit startte vroeger N agent-runs achter elkaar, die vervolgens tegelijk in
    hetzelfde lab aan het werk gingen. Dat is precies wat je niet wilt: ze
    delen één container, één bestandssysteem en één `az`-sessie, en de
    volgorde die op het bord zichtbaar was zei niets meer over de volgorde
    waarin het werk gebeurde. Nu gaat de hele kolom als één geordende planning
    naar binnen die zichzelf ticket voor ticket afwerkt — te volgen, te
    pauzeren en te herschikken, en met dezelfde afhankelijkheden als elders.

    `max_tickets=0` betekent: de hele kolom.
    """
    from services.boards.plan_service import PlanService

    svc = PlanService(db)
    limiet = int(max_tickets or 0) or None
    plan = svc.create_from_column(board_id, column=column, limit=limiet,
                                  name=f"Opgepakt ({trigger})")
    res = await svc.advance(plan.id)
    items = svc.items(plan.id)
    tickets = {t.id: t for t in db.query(Ticket).filter(
        Ticket.id.in_([i.ticket_id for i in items] or [0])).all()}
    return [{"plan_id": plan.id, "ticket_key": (tickets.get(i.ticket_id).key
                                                if tickets.get(i.ticket_id) else None),
             "status": i.state, "run_id": i.run_id,
             "error": i.error or (res.get("fout") if i.state == "failed" else None)}
            for i in items]
