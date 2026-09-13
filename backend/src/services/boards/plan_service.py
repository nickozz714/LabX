"""
services/boards/plan_service.py

Het afwerken van een planning: één ticket tegelijk, in jouw volgorde, tot de
lijst op is of er iets tegenhoudt.

Drie regels bepalen het gedrag, en die zijn allemaal een keuze geweest:

- **Eén run tegelijk per WERKER.** Een lab heeft één of meer werkers
  (containers) die /workspace delen; elke lopende planning bezet er één. Twee
  runs in dezelfde container vechten om dezelfde bestanden, processen en
  `az`-sessie — zo liep de proces-tabel van een lab een keer vol en lag alles
  plat. Met drie werkers lopen er dus drie planningen naast elkaar, ook binnen
  hetzelfde bord; zijn ze allemaal bezet, dan wacht de volgende planning (hij
  pauzeert niet — dat is iets anders dan een probleem).
- **Een geblokkeerd ticket pauzeert de planning**, hij slaat het niet over.
  Doorgaan met de rest zou de volgorde die jij bedoelde stilzwijgend
  omgooien; nu staat er expliciet "wacht op SWI-3" en beslis jij.
- **Doorgaan gebeurt op het aflopen van een run**, niet op een klok. De
  afloop-hook van de achtergrondrun zet het volgende ticket in gang, zodat er
  geen seconde tussen zit. De scheduler-tick is er alleen voor planningen die
  op hun tijdstip wachten, en als vangnet na een herstart.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.board import Board, Ticket
from models.plan import PlanClaim, TicketPlan, TicketPlanItem

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── samenstellen ────────────────────────────────────────────────────────

    def get(self, plan_id: int) -> TicketPlan:
        plan = self.db.get(TicketPlan, plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Planning niet gevonden")
        return plan

    def items(self, plan_id: int) -> List[TicketPlanItem]:
        return (self.db.query(TicketPlanItem)
                .filter(TicketPlanItem.plan_id == plan_id)
                .order_by(TicketPlanItem.position.asc(), TicketPlanItem.id.asc()).all())

    def create(self, board_id: int, *, name: str, ticket_ids: List[int],
               start_at: Optional[str] = None, instruction: Optional[str] = None,
               source: str = "handmatig", start_now: bool = True,
               max_parallel: Optional[int] = None,
               workspace_mode: Optional[str] = None) -> TicketPlan:
        board = self.db.get(Board, board_id)
        if board is None:
            raise HTTPException(status_code=404, detail="Board niet gevonden")
        ids = [int(x) for x in ticket_ids]
        if not ids:
            raise HTTPException(status_code=400, detail="Een planning zonder tickets doet niets")
        geldig = {t.id: t for t in self.db.query(Ticket)
                  .filter(Ticket.board_id == board_id, Ticket.id.in_(ids)).all()}
        onbekend = [i for i in ids if i not in geldig]
        if onbekend:
            raise HTTPException(status_code=400,
                                detail=f"Deze tickets horen niet bij dit board: {onbekend}")
        # Een ticket dat al in een lopende planning staat, gaat er niet nog
        # eens in. Twee planningen die hetzelfde ticket oppakken laten de agent
        # hetzelfde werk twee keer doen — en dat werk verandert echte systemen,
        # dus dat is geen onschuldige dubbelloop.
        al_gepland = self._al_gepland(board_id, ids)
        overgeslagen = [geldig[i].key for i in ids if i in al_gepland]
        ids = [i for i in ids if i not in al_gepland]
        if not ids:
            raise HTTPException(
                status_code=409,
                detail=(f"Deze tickets staan al in een lopende planning: "
                        f"{', '.join(overgeslagen)}"))
        now = _now_iso()
        plan = TicketPlan(
            board_id=board_id, name=(name or "").strip() or f"Planning {now[:16]}",
            state="scheduled" if start_at else ("running" if start_now else "draft"),
            start_at=start_at, source=source,
            instruction=(instruction or "").strip() or None,
            # 0 of leeg = geen eigen plafond: dan bepalen de vrije werkers van
            # het lab de breedte. Eén knop om te begrijpen in plaats van twee.
            max_parallel=(int(max_parallel) if max_parallel else None),
            workspace_mode=("apart" if str(workspace_mode or "") == "apart" else "gedeeld"),
            created_at=now, updated_at=now)
        self.db.add(plan)
        self.db.flush()
        # De VOLGORDE van de meegegeven lijst is de bedoeling — niet de
        # bordvolgorde, niet de id-volgorde. Wie een selectie handmatig
        # ordent, verwacht precies die volgorde terug.
        for i, tid in enumerate(ids):
            self.db.add(TicketPlanItem(plan_id=plan.id, ticket_id=tid,
                                       position=float((i + 1) * 100), state="waiting"))
        if overgeslagen:
            plan.note = (f"Overgeslagen omdat ze al in een lopende planning staan: "
                         f"{', '.join(overgeslagen)}")
        self.db.commit()
        log.infox("Planning aangemaakt", plan=plan.id, board=board_id,
                  tickets=len(ids), overgeslagen=len(overgeslagen), state=plan.state)
        return plan

    def _al_gepland(self, board_id: int, ticket_ids: List[int]) -> set:
        """Ticket-id's die al in een planning zitten die nog wat kan gaan doen."""
        rijen = (self.db.query(TicketPlanItem.ticket_id)
                 .join(TicketPlan, TicketPlan.id == TicketPlanItem.plan_id)
                 .filter(TicketPlan.board_id == board_id,
                         TicketPlan.state.in_(("draft", "scheduled", "running", "paused")),
                         TicketPlanItem.state.in_(("waiting", "blocked", "running")),
                         TicketPlanItem.ticket_id.in_(ticket_ids)).all())
        return {r[0] for r in rijen}

    def create_from_column(self, board_id: int, *, column: Optional[str] = None,
                           name: Optional[str] = None, limit: Optional[int] = None,
                           instruction: Optional[str] = None,
                           start_at: Optional[str] = None) -> TicketPlan:
        """"Pak de hele kolom op" is geen apart mechanisme maar dezelfde
        planning, gevuld met de kolom in bordvolgorde — zodat je hem daarna
        net zo goed kunt volgen, pauzeren en herschikken."""
        from services.boards.board_service import BoardService
        board = self.db.get(Board, board_id)
        if board is None:
            raise HTTPException(status_code=404, detail="Board niet gevonden")
        col = column or board.agent_column
        if not col:
            raise HTTPException(status_code=400, detail="Dit board heeft geen agent-kolom ingesteld")
        tickets = BoardService(self.db).list_tickets(board_id, status=col)
        ids = [t.id for t in tickets]
        if limit:
            ids = ids[:max(1, int(limit))]
        if not ids:
            raise HTTPException(status_code=400, detail=f"Er staan geen tickets in kolom '{col}'")
        vrij = [i for i in ids if i not in self._al_gepland(board_id, ids)]
        if not vrij:
            raise HTTPException(
                status_code=409,
                detail=f"Alle tickets in kolom '{col}' staan al in een lopende planning")
        ids = vrij
        kolomnaam = next((c.get("name") for c in (board.columns or []) if c.get("key") == col), col)
        return self.create(board_id, name=name or f"Kolom '{kolomnaam}'", ticket_ids=ids,
                           instruction=instruction, source="kolom", start_at=start_at)

    # ── afhankelijkheden ────────────────────────────────────────────────────

    def _done_columns(self, board: Board) -> set:
        keys = {str(c.get("key")) for c in (board.columns or []) if c.get("is_done")}
        if board.agent_done_column:
            keys.add(str(board.agent_done_column))
        return keys

    def blokkades(self, ticket: Ticket) -> List[str]:
        """Welke afhankelijkheden van dit ticket nog niet klaar zijn.

        "Klaar" is wat het BORD daaronder verstaat: het ticket staat in een
        kolom die als klaar gemarkeerd is. Niet "de agent zei done" — een
        mens die het ticket terugsleept naar Review bedoelt dat het nog niet
        af is, en dat hoort te tellen."""
        keys = [str(k).strip() for k in (getattr(ticket, "depends_on", None) or []) if str(k).strip()]
        if not keys:
            return []
        board = self.db.get(Board, ticket.board_id)
        klaar_kolommen = self._done_columns(board) if board else set()
        open_keys: List[str] = []
        for key in keys:
            ander = (self.db.query(Ticket)
                     .filter(Ticket.board_id == ticket.board_id, Ticket.key == key).one_or_none())
            if ander is None:
                continue  # verwijderd ticket houdt niets meer tegen
            if ander.status not in klaar_kolommen:
                open_keys.append(key)
        return open_keys

    # ── uitvoeren ───────────────────────────────────────────────────────────

    def _claim_worker(self, plan: TicketPlan) -> Tuple[Optional[Any], Optional[int]]:
        """Een vrije werker van het lab pakken, of vertellen wie hem bezet houdt.

        Een lab heeft één of meer werkers (containers) die /workspace delen.
        Elke lopende planning bezet er één: twee runs in dezelfde container
        vechten om dezelfde bestanden, processen en `az`-sessie. Zijn er drie
        werkers, dan lopen er dus drie planningen naast elkaar — ook binnen
        hetzelfde bord."""
        from models.lab import Lab
        from services.lab.lab_service import LabService

        board = self.db.get(Board, plan.board_id)
        if board is None or not board.lab_id:
            return None, None
        lab_svc = LabService(self.db)
        lab = self.db.get(Lab, board.lab_id)
        if lab is None:
            return None, None
        werkers = lab_svc.claimbare_werkers(lab)
        if not werkers:
            if lab_svc.workers(lab.id):
                # Er zijn wél werkers, maar geen enkele is bruikbaar (ze worden
                # nog ingericht, of ze staan op error). Wachten dus — anders
                # zou de planning alsnog op de container van het lab landen en
                # zou de hele verdeling stilzwijgend instorten.
                return None, -1
            # Lab (nog) niet gestart: laat start_ticket_run hem aanzetten en
            # gebruik werker 1 — precies zoals het ging toen er één was.
            return None, None

        borden = [b.id for b in self.db.query(Board).filter(Board.lab_id == board.lab_id).all()]
        bezet_rijen = (self.db.query(TicketPlanItem.worker_id, TicketPlan.id)
                       .join(TicketPlan, TicketPlan.id == TicketPlanItem.plan_id)
                       .filter(TicketPlan.board_id.in_(borden),
                               TicketPlanItem.state == "running").all())
        bezet = {r[0] for r in bezet_rijen if r[0]}
        # En wat er BUITEN de planningen om draait. Een ticket dat met de hand
        # is gestart heeft geen planningsregel, dus die runs waren hier
        # onzichtbaar: een planning kon een werker pakken waar al iemand zat.
        # Sinds handmatige starts zelf een werker kiezen, is dat geen
        # theoretisch geval meer.
        bezet |= lab_svc.bezette_werkers(lab.id)
        # Een lopend item zonder werker (van vóór deze functie) bezet het lab
        # als geheel — anders zou hij naast zichzelf gaan draaien.
        if any(r[0] is None for r in bezet_rijen):
            return None, next((r[1] for r in bezet_rijen if r[0] is None), None)
        vrij = [w for w in werkers if w.id not in bezet]
        if vrij:
            lab_svc.touch_worker(vrij[0].id)
            return vrij[0], None
        # Niets vrij: laat de autoscaler er een bijzetten (tot het plafond dat
        # de mens heeft ingesteld). Die nieuwe werker moet eerst ingericht
        # worden, dus deze ronde wacht nog; de volgende tick pakt hem op. Dat
        # is beter dan een run beginnen in een half opgebouwde container waar
        # zijn pakketten nog niet in zitten.
        self._vraag_werker_bij(lab.id)
        return None, next((r[1] for r in bezet_rijen), None)

    def _vraag_werker_bij(self, lab_id: str) -> None:
        """Bijschalen op de achtergrond: een container starten en inrichten
        duurt te lang om een planning-tick op te laten wachten."""
        import asyncio
        if lab_id in _SCHAAL_BEZIG:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        _SCHAAL_BEZIG.add(lab_id)
        loop.create_task(_schaal_worker(lab_id))

    # ── naar buiten melden ──────────────────────────────────────────────────
    #
    # Een planning is precies het soort werk waarbij je niet voor het scherm
    # blijft zitten: je zet er acht tickets in en gaat wat anders doen. De twee
    # momenten die er dan toe doen zijn "alles is af" en "hij staat stil en
    # wacht op jou" — de rest is ruis.

    def _meld_planning(self, plan, items) -> None:
        from services.notify.notify_service import meld

        board = self.db.get(Board, plan.board_id)
        mislukt = [i for i in items if i.state == "failed"]
        klaar = [i for i in items if i.state == "done"]
        regels = [f"{len(klaar)} van de {len(items)} ticket(s) afgerond."]
        if mislukt:
            sleutels = []
            for i in mislukt:
                t = self.db.get(Ticket, i.ticket_id)
                sleutels.append(f"{t.key if t else i.ticket_id}: {(i.error or 'onbekend')[:120]}")
            regels.append("Niet gelukt:\n" + "\n".join(f"- {x}" for x in sleutels))
        meld("planning_klaar",
             f"Planning '{plan.name}' klaar" + (f" ({len(mislukt)} mislukt)" if mislukt else ""),
             "\n\n".join(regels),
             {"board_id": plan.board_id, "board_name": board.name if board else None,
              "plan_id": plan.id, "lab_id": board.lab_id if board else None})

    def _meld_pauze(self, plan, ticket) -> None:
        """Een gepauzeerde planning is het geval waar terugpraten het meest
        oplevert: er wacht werk op een beslissing van jou, en zolang die niet
        komt gebeurt er niets. Het gesprek van het ticket gaat mee in de
        context, zodat een antwoord rechtstreeks bij de agent uitkomt."""
        from services.notify.notify_service import meld

        board = self.db.get(Board, plan.board_id)
        meld("aandacht_nodig",
             f"Planning '{plan.name}' staat stil bij {ticket.key if ticket else '?'}",
             (plan.note or "De planning is gepauzeerd.")
             + (f"\n\nTicket: {ticket.title}" if ticket else ""),
             {"board_id": plan.board_id, "board_name": board.name if board else None,
              "plan_id": plan.id, "lab_id": board.lab_id if board else None,
              "ticket_id": ticket.id if ticket else None,
              "ticket_key": ticket.key if ticket else None,
              "thread_id": ticket.agent_thread_id if ticket else None})

    async def advance(self, plan_id: int) -> Dict[str, Any]:
        """Zet het volgende ticket in gang, als dat kan."""
        from services.agent import background_runs
        from services.boards.agent_work import start_ticket_run

        plan = self.get(plan_id)
        if plan.state not in ("running", "scheduled"):
            return {"plan": plan.id, "state": plan.state, "gestart": None}
        items = self.items(plan.id)
        self._ruim_dode_runs_op(items)

        # Items die stilliggen tot hun tijd om is, mogen weer mee. `resume_at`
        # blijft staan tot het item ECHT start: hem hier wissen maakte in de UI
        # onzichtbaar waar een wachtend ticket op wachtte, en dat is precies
        # het tasten in het duister dat we wilden wegnemen.
        nu = _now_iso()

        # Items die buiten de planning om zijn afgerond (de agent zette het
        # ticket op Klaar, of jij sleepte het erheen) alsnog afvinken. Zonder
        # dit blijft de planning eeuwig lopen met tickets die al af zijn — en
        # zou ze de agent opnieuw op afgerond werk zetten.
        for it in items:
            if it.state in ("waiting", "blocked"):
                t = self.db.get(Ticket, it.ticket_id)
                if self._afgerond_buitenom(plan, it, t):
                    self._rond_af_buitenom(plan, it, t)

        lopend = [i for i in items if i.state == "running"]
        wachtend = [i for i in items if i.state in ("waiting", "blocked")
                    and (not i.resume_at or i.resume_at <= nu)]

        if not lopend and not wachtend:
            geparkeerd = [i for i in items if i.state == "waiting" and i.resume_at]
            if geparkeerd:
                # Alles wat nog moet gebeuren ligt te wachten op een tijdstip.
                # Niet "klaar", maar ook niets te doen: de scheduler komt terug.
                vroegste = min(i.resume_at for i in geparkeerd)
                return {"plan": plan.id, "state": plan.state, "gestart": None,
                        "wacht_tot": vroegste}
            plan.state = "done"
            plan.finished_at = plan.updated_at = _now_iso()
            plan.note = None
            self.db.commit()
            log.infox("Planning klaar", plan=plan.id)
            self._meld_planning(plan, items)
            return {"plan": plan.id, "state": "done", "gestart": None}

        ruimte = self._vrije_ruimte(plan, len(lopend))
        gestart: List[str] = []
        uitslag: Dict[str, Any] = {"plan": plan.id, "state": plan.state, "gestart": None}

        for volgende in wachtend:
            if ruimte <= 0:
                break
            ticket = self.db.get(Ticket, volgende.ticket_id)
            if ticket is None:
                volgende.state = "skipped"
                volgende.error = "Ticket bestaat niet meer"
                volgende.finished_at = _now_iso()
                self.db.commit()
                continue

            open_keys = self.blokkades(ticket)
            if open_keys:
                # Blokkades stoppen de planning NIET meer: een ticket dat op een
                # ander wacht gaat opzij, de rest gaat door. Alleen als er
                # daarna niets anders te doen is, staat de planning stil — en
                # dat merk je dan aan de melding, niet aan een lege rij.
                volgende.state = "blocked"
                volgende.wait_reason = f"wacht op {', '.join(open_keys)}"
                volgende.error = None
                self.db.commit()
                uitslag.setdefault("geblokkeerd", []).append(
                    {"ticket": ticket.key, "wacht_op": open_keys})
                continue

            botsing = self._claim_botsing(plan, ticket, volgende)
            if botsing:
                # Dit ticket zat de vorige keer aan iets waar nu iemand anders
                # aan zit. Overslaan tot die klaar is; hij komt vanzelf terug.
                volgende.wait_reason = (
                    f"{botsing['resource']} is in gebruik door {botsing['bezet_door']}")
                self.db.commit()
                uitslag.setdefault("wacht_op_claim", []).append(
                    {"ticket": ticket.key, **botsing})
                continue

            werker, bezet_door = self._claim_worker(plan)
            if werker is None and bezet_door is not None:
                # Geen werker vrij: geen probleem maar een wachtrij. Zodra er
                # een vrijkomt — of de autoscaler er een heeft bijgezet en
                # ingericht — komt deze planning vanzelf aan bod.
                uitslag["wacht_op_planning"] = None if bezet_door == -1 else bezet_door
                uitslag["wacht_op_werker"] = bezet_door == -1
                break

            if plan.state == "scheduled":
                plan.state = "running"
            if not plan.started_at:
                plan.started_at = _now_iso()
            volgende.state = "running"
            volgende.error = None
            volgende.resume_at = None
            volgende.wait_reason = None
            volgende.worker_id = werker.id if werker is not None else None
            volgende.started_at = _now_iso()
            plan.note = None
            plan.updated_at = _now_iso()
            self.db.commit()

            try:
                res = await start_ticket_run(
                    self.db, ticket.id,
                    extra_instruction=self._instructie_voor(plan, ticket),
                    trigger=f"planning '{plan.name}'",
                    lab_worker_id=werker.id if werker is not None else None)
            except HTTPException as exc:
                volgende.state = "failed"
                volgende.error = str(exc.detail)[:2000]
                volgende.finished_at = _now_iso()
                self.db.commit()
                log.warningx("Ticket uit planning kon niet starten", plan=plan.id,
                             ticket=ticket.key, error=str(exc.detail)[:200])
                uitslag.setdefault("fouten", []).append(
                    {"ticket": ticket.key, "fout": str(exc.detail)[:300]})
                # Eén ticket dat niet start is geen reden om de rest stil te
                # leggen; dat was het wél toen er maar één tegelijk liep.
                self._meld_pauze(plan, ticket) if len(wachtend) == 1 else None
                continue

            volgende.run_id = res.get("run_id")
            self.db.commit()
            background_runs.on_finish(res["run_id"], _maak_afloop_hook(plan.id, volgende.id))
            gestart.append(ticket.key)
            ruimte -= 1

        if gestart:
            uitslag["gestart"] = gestart[0] if len(gestart) == 1 else gestart
            uitslag["gestart_aantal"] = len(gestart)
        uitslag["state"] = plan.state
        return uitslag

    # ── eindpunten ──────────────────────────────────────────────────────────
    #
    # Een planning wist tot nu toe alleen via haar eigen afloop-hook dat een
    # ticket klaar was. Alles wat daarbuiten valt bleef eeuwig hangen: een
    # ticket dat met `board__wait_until` parkeerde en daarna door de agent
    # werd afgerond, een ticket dat iemand met de hand naar Klaar sleepte, een
    # run waarvan de hook verdween bij een herstart. De planning bleef dan
    # "running" met items op "waiting" — en zou bij het hervatten de agent
    # opnieuw op een al afgerond ticket zetten.
    #
    # Daarom kijkt de planning nu ook naar het TICKET zelf. Dat is de plek waar
    # "af" zichtbaar is voor iedereen: de kolom.

    def _klaar_kolommen(self, board: Optional[Board]) -> set:
        """Kolommen die 'af' betekenen: de kolom waar de agent zijn werk in
        neerzet, plus alles wat op het bord als klaar-kolom gemarkeerd staat."""
        if board is None:
            return set()
        uit = {str(c.get("key")) for c in (board.columns or []) if c.get("is_done")}
        if board.agent_done_column:
            uit.add(str(board.agent_done_column))
        return uit

    def _afgerond_buitenom(self, plan: TicketPlan, item: TicketPlanItem,
                           ticket: Optional[Ticket]) -> bool:
        """Is dit ticket klaar zonder dat de planning dat meekreeg?

        Twee voorwaarden, en de tweede is er omdat de eerste alleen te weinig
        was.

        1. Het ticket staat in een klaar-kolom. Alleen de kolom telt, en met
           opzet niet `agent_state`: die zegt hoe de laatste RUN afliep, niet of
           het werk af is. De kolom is de uitspraak van de agent (of van jou)
           dát het klaar is.
        2. Het item heeft in DEZE planning gedraaid. Zonder die eis vinkte een
           nieuwe planning meteen alles af wat al op Klaar stond — en dat is
           precies wat je NIET bedoelt als je acht afgeronde tickets inplant om
           ze te laten controleren. Een ticket dat nog niet gedraaid heeft,
           start gewoon, in welke kolom het ook staat.

        Wat deze toets moet vangen is het andere geval: een ticket dat wél is
        gestart, met `board__wait_until` parkeerde, en daarna door de agent zelf
        is afgerond. Dat kreeg de planning niet mee en bleef eeuwig hangen.
        """
        if ticket is None:
            return False
        if not (item.started_at or item.run_id):
            return False
        board = self.db.get(Board, plan.board_id)
        return ticket.status in self._klaar_kolommen(board)

    def _rond_af_buitenom(self, plan: TicketPlan, item: TicketPlanItem,
                          ticket: Ticket) -> None:
        item.state = "done"
        item.finished_at = item.finished_at or _now_iso()
        item.resume_at = None
        item.worker_id = None
        item.error = None
        self._geef_claims_vrij(item, reden="ticket staat in een klaar-kolom")
        self.db.commit()
        log.infox("Planning-item afgerond op de kolom van het ticket",
                  plan=plan.id, ticket=ticket.key, kolom=ticket.status)

    def _ruim_dode_runs_op(self, items: List[TicketPlanItem]) -> None:
        """Een item dat "running" heet terwijl zijn run allang weg is (herstart
        van de backend) blokkeert anders voor eeuwig een plek in de rij."""
        from services.agent import background_runs

        for it in items:
            if it.state != "running":
                continue
            if it.run_id and background_runs.is_active(it.run_id):
                continue
            it.state = "failed"
            it.error = "De run is verdwenen (herstart van de backend?)"
            it.finished_at = _now_iso()
            self._geef_claims_vrij(it, reden="run verdwenen")
        self.db.commit()

    def _vrije_ruimte(self, plan: TicketPlan, lopend: int) -> int:
        """Hoeveel tickets er nog bij mogen.

        Zonder eigen plafond is het antwoord "zoveel als er werkers vrij zijn",
        en dat regelt `_claim_worker` toch al per ticket. Dan is dit alleen een
        bovengrens tegen een lus die honderd keer probeert: meer dan er werkers
        kunnen zijn heeft geen zin.
        """
        if plan.max_parallel:
            return max(0, int(plan.max_parallel) - lopend)
        from models.lab import Lab
        board = self.db.get(Board, plan.board_id)
        lab = self.db.get(Lab, board.lab_id) if board and board.lab_id else None
        plafond = int(getattr(lab, "max_workers", 1) or 1) if lab is not None else 1
        return max(0, plafond - lopend)

    def _instructie_voor(self, plan: TicketPlan, ticket: Ticket) -> Optional[str]:
        """De instructie van de planning, plus wat dit ticket over ZIJN plek
        moet weten als de planning met aparte werkmappen draait."""
        delen = [ (plan.instruction or "").strip() ]
        if (plan.workspace_mode or "gedeeld") == "apart":
            map_ = self.werkmap(plan, ticket)
            delen.append(
                f"### Je eigen werkmap\n"
                f"Deze planning draait meerdere tickets tegelijk in hetzelfde lab, en jullie "
                f"delen /workspace. Werk daarom in `{map_}` (maak hem aan als hij er niet is) "
                f"en laat bestanden daarbuiten met rust, tenzij je ze alleen LEEST. Wat je "
                f"buiten die map wijzigt, wijzig je onder de handen van een collega die op "
                f"hetzelfde moment aan een ander ticket werkt.")
        tekst = "\n\n".join(d for d in delen if d)
        return tekst or None

    @staticmethod
    def werkmap(plan: TicketPlan, ticket: Ticket) -> str:
        veilig = "".join(c if c.isalnum() or c in "._-" else "_" for c in (ticket.key or "ticket"))
        return f"/workspace/.plan-{plan.id}/{veilig}"

    # ── claims: wie zit waaraan ───────────────────────────────────────────
    #
    # Zodra tickets naast elkaar draaien, kan LabX niet weten of dat veilig is.
    # Afhankelijkheden vooraf invullen werkt bij drie tickets en niet bij
    # twintig; een LLM die het vooraf inschat, gokt. De agent weet het wél —
    # hij staat op het punt die pipeline aan te passen. Dus meldt hij het.
    #
    # Twee kanten:
    # - RUNTIME: `board__claim` geeft of weigert. Weigeren is geen fout maar
    #   informatie ("SWI-88 zit erin sinds 19:18"); de agent kan wachten, iets
    #   anders doen, of afronden met een opmerking.
    # - PLANNING: een ticket dat eerder iets claimde, wordt niet gestart zolang
    #   iemand anders dat vasthoudt. Zo botsen ze niet pas halverwege.

    @staticmethod
    def _normaliseer(resource: str) -> str:
        return " ".join(str(resource or "").strip().lower().split())[:255]

    def _claims_van(self, plan_item_id: int) -> List[PlanClaim]:
        return (self.db.query(PlanClaim)
                .filter(PlanClaim.plan_item_id == plan_item_id,
                        PlanClaim.released_at.is_(None)).all())

    def _actieve_claims(self, plan: TicketPlan, *, behalve_item: Optional[int] = None) -> Dict[str, PlanClaim]:
        """Wat er NU vastgehouden wordt binnen dit lab.

        Per lab en niet per planning: twee planningen op hetzelfde lab delen
        dezelfde containers, dezelfde az-sessie en dezelfde Fabric-omgeving.
        Een claim die alleen binnen één planning gold, zou precies de botsing
        missen die het duurst is.
        """
        board = self.db.get(Board, plan.board_id)
        if board is None:
            return {}
        borden = [b.id for b in self.db.query(Board).filter(
            Board.lab_id == board.lab_id).all()] if board.lab_id else [board.id]
        rijen = (self.db.query(PlanClaim, TicketPlanItem)
                 .join(TicketPlanItem, TicketPlanItem.id == PlanClaim.plan_item_id)
                 .join(TicketPlan, TicketPlan.id == TicketPlanItem.plan_id)
                 .filter(TicketPlan.board_id.in_(borden),
                         PlanClaim.released_at.is_(None),
                         # Alleen wat bij levend werk hoort: een claim van een
                         # item dat klaar of mislukt is, houdt niets tegen.
                         TicketPlanItem.state.in_(("running", "waiting", "blocked"))).all())
        uit: Dict[str, PlanClaim] = {}
        for claim, item in rijen:
            if behalve_item is not None and item.id == behalve_item:
                continue
            uit.setdefault(claim.resource, claim)
        return uit

    def _claim_botsing(self, plan: TicketPlan, ticket: Ticket,
                       item: Optional[TicketPlanItem] = None) -> Optional[Dict[str, Any]]:
        """Zou dit ticket botsen met iets dat een ANDER ticket vasthoudt?

        Gebaseerd op wat het ticket EERDER claimde. Dat is geen voorspelling
        maar geheugen: een ticket dat de vorige keer aan `PL_RUN_SILVER` zat,
        doet dat de volgende keer vrijwel zeker weer. Is er geen geschiedenis,
        dan start hij gewoon en vangt `board__claim` het onderweg af.

        Zijn EIGEN claims tellen niet mee, en dat is geen detail: een ticket
        houdt zijn claims vast terwijl het met `board__wait_until` staat te
        wachten (juist dan wil je dat niemand anders in die pipeline zit).
        Telden ze wél mee, dan botst het ticket bij het hervatten met zichzelf
        en start het nooit meer. Precies dat legde op 11-09 een planning van
        acht tickets volledig stil: alle acht wachtten, alle acht hielden hun
        claims vast, en alle acht blokkeerden zichzelf.
        """
        eerder = {c.resource for c in self.db.query(PlanClaim)
                  .filter(PlanClaim.ticket_id == ticket.id).all()}
        if not eerder:
            return None
        actief = self._actieve_claims(
            plan, behalve_item=(item.id if item is not None else None))
        overlap = sorted(eerder & set(actief))
        if not overlap:
            return None
        houder = actief[overlap[0]]
        bezet_ticket = self.db.get(Ticket, houder.ticket_id)
        return {"resource": overlap[0],
                "bezet_door": bezet_ticket.key if bezet_ticket else str(houder.ticket_id),
                "sinds": houder.created_at}

    def _geef_claims_vrij(self, item: TicketPlanItem, *, reden: str = "") -> int:
        rijen = self._claims_van(item.id)
        for c in rijen:
            c.released_at = _now_iso()
        if rijen:
            log.infox("Claims vrijgegeven", item=item.id, aantal=len(rijen), reden=reden)
        return len(rijen)

    def claim(self, *, lab_id: str, worker_id: Optional[int],
              resources: List[str], reden: str) -> Dict[str, Any]:
        """De agent meldt waar hij aan zit. Alles-of-niets.

        Deels toekennen zou de agent in een halve toestand achterlaten waarin
        hij denkt te mogen beginnen: dan heeft hij de pipeline wel en de tabel
        niet, en komt hij daar halverwege achter.
        """
        gevraagd = [self._normaliseer(r) for r in (resources or [])]
        gevraagd = [r for r in gevraagd if r]
        if not gevraagd:
            return {"error": "Geef minstens één bron op, bv. \"fabric:acc:PL_RUN_SILVER\"."}
        item = self._lopend_item(lab_id, worker_id)
        if item is None:
            return {"result": ("Dit ticket draait niet vanuit een planning, dus er is niemand om "
                               "mee te botsen. Je hoeft niets te claimen.")}
        plan = self.get(item.plan_id)
        actief = self._actieve_claims(plan, behalve_item=item.id)
        botsingen = []
        for r in gevraagd:
            houder = actief.get(r)
            if houder is None:
                continue
            bezet = self.db.get(Ticket, houder.ticket_id)
            botsingen.append({"bron": r,
                              "bezet_door": bezet.key if bezet else str(houder.ticket_id),
                              "sinds": houder.created_at})
        if botsingen:
            omschrijving = "; ".join(
                f"{b['bron']} zit bij {b['bezet_door']} (sinds {str(b['sinds'])[11:16]} UTC)"
                for b in botsingen)
            return {"result": (
                f"NIET toegekend — {omschrijving}. Er is niets voor je gereserveerd, dus begin "
                f"hier niet aan. Kies iets anders uit dit ticket dat wél vrij is, of gebruik "
                f"`board__wait_until` om het later opnieuw te proberen; zet in een opmerking "
                f"waar je op wacht."), "conflict": botsingen}

        al = {c.resource for c in self._claims_van(item.id)}
        nieuw = [r for r in gevraagd if r not in al]
        for r in nieuw:
            self.db.add(PlanClaim(plan_item_id=item.id, ticket_id=item.ticket_id,
                                  run_id=item.run_id, resource=r,
                                  reason=(reden or "")[:1000] or None,
                                  created_at=_now_iso()))
        self.db.commit()
        return {"result": (
            f"Toegekend: {', '.join(gevraagd)}. Zolang dit ticket loopt (ook terwijl het met "
            f"`board__wait_until` wacht) komt er niemand anders aan. De claim gaat vanzelf los "
            f"als het ticket klaar is; geef hem eerder vrij met `board__release` zodra je klaar "
            f"bent met een bron, dan kan een wachtend ticket door.")}

    def release(self, *, lab_id: str, worker_id: Optional[int],
                resources: Optional[List[str]] = None) -> Dict[str, Any]:
        item = self._lopend_item(lab_id, worker_id)
        if item is None:
            return {"result": "Dit ticket draait niet vanuit een planning; er is niets te "
                              "geven."}
        gevraagd = {self._normaliseer(r) for r in (resources or []) if self._normaliseer(r)}
        rijen = self._claims_van(item.id)
        vrij = [c for c in rijen if not gevraagd or c.resource in gevraagd]
        for c in vrij:
            c.released_at = _now_iso()
        self.db.commit()
        if not vrij:
            return {"result": "Je had niets (meer) vastgehouden."}
        return {"result": f"Vrijgegeven: {', '.join(c.resource for c in vrij)}."}

    def wacht_tot(self, *, lab_id: str, worker_id: Optional[int],
                  minuten: int, reden: str) -> Dict[str, Any]:
        """De agent wacht op iets dat tijd kost; de planning pakt het ticket
        later opnieuw op.

        Dit vervangt wat de agent anders zelf probeerde: een wekker zetten die
        in een headless run niet bestaat, of minutenlang pollen in het lab.
        Beide kosten een werker en het tweede kost ook nog zijn context. Nu
        gaat de planning op pauze met een hervattijd, komt de werker vrij voor
        ander werk, en start dit ticket straks opnieuw — met zijn eigen
        opmerkingen als context.
        """
        from datetime import timedelta

        from models.board import Board
        minuten = max(1, min(int(minuten or 5), 720))
        item = self._lopend_item(lab_id, worker_id)
        if item is None:
            return {"error": ("Dit ticket draait niet vanuit een planning, dus er is niets om "
                              "later te hervatten. Wacht binnen deze run (kort), of zet in een "
                              "opmerking wat er nog moet gebeuren en rond af.")}
        plan = self.get(item.plan_id)
        tot = (datetime.now(timezone.utc) + timedelta(minutes=minuten)).isoformat()
        # Alleen dit ITEM wacht; de planning loopt door. Vroeger ging de hele
        # planning op pauze, waardoor wachten op een pipeline van een uur ook
        # de vier tickets stillegde die daar niets mee te maken hadden — en de
        # vrijgekomen werker naar een ándere planning ging in plaats van naar
        # het volgende ticket van deze.
        item.state = "waiting"
        item.resume_at = tot
        item.wait_reason = reden[:1000]
        item.worker_id = None
        item.finished_at = _now_iso()
        # Claims blijven staan: wie een uur op een pipeline wacht, wil juist
        # niet dat er ondertussen iemand anders in zit.
        plan.note = None
        plan.updated_at = _now_iso()
        self.db.commit()
        ticket = self.db.get(Ticket, item.ticket_id)
        board = self.db.get(Board, plan.board_id) if plan else None
        if ticket is not None and board is not None:
            from services.boards.board_service import BoardService
            BoardService(self.db).add_comment(
                ticket.id, kind="activity", author="agent",
                body=f"Wacht tot {tot[11:16]} UTC voordat het werk verdergaat — {reden[:500]}")
        log.infox("Ticket uit planning wacht", plan=plan.id, item=item.id,
                  tot=tot, minuten=minuten)
        vastgehouden = [c.resource for c in self._claims_van(item.id)]
        return {"result": (
            f"Genoteerd. Dit ticket wordt om {tot[11:16]} UTC opnieuw opgepakt; je werker komt "
            f"nu vrij en de planning '{plan.name}' gaat ondertussen verder met het volgende "
            f"ticket. "
            + (f"Je claim op {', '.join(vastgehouden)} blijft staan, dus daar komt niemand aan. "
               if vastgehouden else "")
            + f"Rond deze run nu af: zet in een OPMERKING wat je hebt gedaan en wat er na de "
              f"wachttijd moet gebeuren — die opmerking is straks je enige context.")}

    def _lopend_item(self, lab_id: str, worker_id: Optional[int]) -> Optional[TicketPlanItem]:
        """Het planning-ticket dat nu op deze werker draait. Een werker doet er
        één tegelijk, dus dat is eenduidig; zonder werker (één-werker-lab of
        een oudere run) valt hij terug op het lopende item van dit lab."""
        from models.board import Board
        borden = [b.id for b in self.db.query(Board).filter(Board.lab_id == lab_id).all()]
        if not borden:
            return None
        q = (self.db.query(TicketPlanItem)
             .join(TicketPlan, TicketPlan.id == TicketPlanItem.plan_id)
             .filter(TicketPlan.board_id.in_(borden), TicketPlanItem.state == "running"))
        if worker_id:
            return q.filter(TicketPlanItem.worker_id == int(worker_id)).first()
        return q.first()

    # ── sturen ──────────────────────────────────────────────────────────────

    def pause(self, plan_id: int, *, note: str = "Handmatig gepauzeerd") -> TicketPlan:
        plan = self.get(plan_id)
        if plan.state in ("done", "cancelled"):
            raise HTTPException(status_code=409, detail="Deze planning is al afgelopen")
        plan.state = "paused"
        plan.note = note
        plan.updated_at = _now_iso()
        self.db.commit()
        return plan

    async def resume(self, plan_id: int) -> Dict[str, Any]:
        plan = self.get(plan_id)
        if plan.state in ("done", "cancelled"):
            raise HTTPException(status_code=409, detail="Deze planning is al afgelopen")
        # Een geblokkeerd item mag het opnieuw proberen: misschien is dat
        # andere ticket inmiddels wél klaar — dat is meestal juist de reden
        # dat iemand op hervatten drukt.
        for it in self.items(plan.id):
            if it.state == "blocked":
                it.state = "waiting"
        plan.state = "running"
        plan.note = None
        plan.resume_at = None
        plan.updated_at = _now_iso()
        self.db.commit()
        return await self.advance(plan.id)

    def cancel(self, plan_id: int) -> TicketPlan:
        """Afbreken laat lopend werk met rust: een run halverwege afkappen
        levert een half ticket en een verwarrend verslag. Wat nog wacht wordt
        overgeslagen."""
        plan = self.get(plan_id)
        for it in self.items(plan.id):
            if it.state in ("waiting", "blocked"):
                it.state = "skipped"
                it.finished_at = _now_iso()
        plan.state = "cancelled"
        plan.finished_at = plan.updated_at = _now_iso()
        self.db.commit()
        return plan

    def reorder(self, plan_id: int, item_ids: List[int]) -> List[TicketPlanItem]:
        items = {i.id: i for i in self.items(plan_id)}
        for i, item_id in enumerate(item_ids):
            it = items.get(int(item_id))
            if it is not None:
                it.position = float((i + 1) * 100)
        self.db.commit()
        return self.items(plan_id)

    def remove_item(self, plan_id: int, item_id: int) -> None:
        it = self.db.get(TicketPlanItem, item_id)
        if it is None or it.plan_id != plan_id:
            raise HTTPException(status_code=404, detail="Regel niet gevonden")
        if it.state == "running":
            raise HTTPException(status_code=409,
                                detail="Dit ticket draait nu — pauzeer de planning of wacht het af")
        self.db.delete(it)
        self.db.commit()

    # ── serialiseren ────────────────────────────────────────────────────────

    def to_dict(self, plan: TicketPlan, *, with_items: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": plan.id, "board_id": plan.board_id, "name": plan.name,
            "state": plan.state, "start_at": plan.start_at, "source": plan.source,
            "instruction": plan.instruction, "note": plan.note,
            "resume_at": getattr(plan, "resume_at", None),
            "max_parallel": getattr(plan, "max_parallel", None),
            "workspace_mode": getattr(plan, "workspace_mode", "gedeeld") or "gedeeld",
            "created_at": plan.created_at, "updated_at": plan.updated_at,
            "started_at": plan.started_at, "finished_at": plan.finished_at,
        }
        items = self.items(plan.id)
        telling: Dict[str, int] = {}
        for it in items:
            telling[it.state] = telling.get(it.state, 0) + 1
        out["counts"] = telling
        out["total"] = len(items)
        if with_items:
            tickets = {t.id: t for t in self.db.query(Ticket)
                       .filter(Ticket.id.in_([i.ticket_id for i in items] or [0])).all()}
            out["items"] = []
            for it in items:
                t = tickets.get(it.ticket_id)
                out["items"].append({
                    "id": it.id, "ticket_id": it.ticket_id,
                    "ticket_key": t.key if t else None,
                    "ticket_title": t.title if t else "(verwijderd)",
                    "depends_on": list(getattr(t, "depends_on", None) or []) if t else [],
                    "position": it.position, "state": it.state, "run_id": it.run_id,
                    "error": it.error, "started_at": it.started_at,
                    "finished_at": it.finished_at,
                    # Wacht dit ticket op een tijdstip, en waar zit het aan?
                    # Zonder die twee lijkt een parallel lopende planning een
                    # rommeltje: vier regels "waiting" zonder uitleg.
                    "resume_at": getattr(it, "resume_at", None),
                    "wait_reason": getattr(it, "wait_reason", None),
                    "claims": [c.resource for c in self._claims_van(it.id)],
                })
        return out


_SCHAAL_BEZIG: set = set()


async def _schaal_worker(lab_id: str) -> None:
    from db.database import SessionLocal
    from services.lab.lab_service import LabService

    db = SessionLocal()
    try:
        w = await LabService(db).ensure_extra_worker(lab_id)
        if w is not None:
            log.infox("Autoscaler zette een werker bij", lab_id=lab_id, werker=w.index)
    except Exception as exc:  # noqa: BLE001
        log.warningx("Autoscaler kon niet bijschalen", lab_id=lab_id, error=str(exc)[:300])
    finally:
        _SCHAAL_BEZIG.discard(lab_id)
        db.close()


def _maak_afloop_hook(plan_id: int, item_id: int):
    """Wanneer de run van dit item klaar is: de uitslag vastleggen en meteen
    door naar het volgende. Het doorpakken gebeurt in een eigen taak — de hook
    draait in de sessie van de aflopende run, en die wil je niet openhouden
    voor de hele rest van de planning."""
    def _hook(db: Session, run) -> None:
        item = db.get(TicketPlanItem, item_id)
        if item is None:
            return
        if item.state != "running":
            # Al afgehandeld of opnieuw ingepland (de agent vroeg om later
            # verder te gaan). Zijn uitslag overschrijven zou die herplanning
            # stilzwijgend ongedaan maken.
            return
        status = getattr(run, "status", None) or "failed"
        if status == "limited":
            # Gebruikslimiet: het ticket is niet mislukt, het mag alleen even
            # niet. Terug in de rij met de hersteltijd erop — dezelfde weg als
            # `board__wait_until`, dus de planning gaat ondertussen verder met
            # de tickets die er niets mee te maken hebben.
            #
            # Claims blijven staan, net als bij wachten: het werk is niet af en
            # niemand anders zou nu in diezelfde pipeline moeten gaan zitten.
            item.state = "waiting"
            item.resume_at = getattr(run, "resume_at", None)
            item.wait_reason = "gebruikslimiet bereikt"
            item.worker_id = None
            item.error = None
            item.finished_at = _now_iso()
            db.commit()
            _plan_verder(plan_id)
            return
        item.state = "done" if status == "completed" else "failed"
        if status != "completed":
            item.error = (getattr(run, "error", None) or f"Run eindigde als '{status}'")[:2000]
        item.finished_at = _now_iso()
        # Het ticket is klaar (of stuk): wat het vasthield komt vrij, zodat een
        # ticket dat erop wachtte meteen door kan. Zou dit hier niet gebeuren,
        # dan blokkeert een afgelopen ticket de rest van de planning voorgoed.
        PlanService(db)._geef_claims_vrij(item, reden=f"item {status}")
        if item.worker_id:
            from services.lab.lab_service import LabService
            LabService(db).touch_worker(item.worker_id)
        plan = db.get(TicketPlan, plan_id)
        if plan is not None:
            plan.updated_at = _now_iso()
        db.commit()
        _plan_verder(plan_id)
    return _hook


def _plan_verder(plan_id: int) -> None:
    """Volgende ticket in gang zetten, in een eigen taak met een eigen sessie."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warningx("Planning niet voortgezet: geen event loop", plan=plan_id)
        return
    loop.create_task(_advance_worker(plan_id))


async def _advance_worker(plan_id: int) -> None:
    from db.database import SessionLocal
    db = SessionLocal()
    try:
        await PlanService(db).advance(plan_id)
    except Exception as exc:  # noqa: BLE001 — een achtergrondtaak heeft geen aanroeper
        log.warningx("Planning voortzetten mislukt", plan=plan_id, error=str(exc)[:300])
    finally:
        db.close()


async def tick() -> int:
    """Scheduler-tick: planningen die aan de beurt zijn losmaken, en lopende
    planningen die stilvielen weer aanschoppen.

    Dat tweede is het vangnet: normaal duwt de afloop-hook de planning door,
    maar na een herstart van de backend is die hook weg, en een planning die
    op een bezet lab stond te wachten heeft niemand die hem wekt."""
    from db.database import SessionLocal

    db = SessionLocal()
    aantal = 0
    try:
        svc = PlanService(db)
        nu = _now_iso()
        due = (db.query(TicketPlan)
               .filter(TicketPlan.state == "scheduled",
                       TicketPlan.start_at.isnot(None),
                       TicketPlan.start_at <= nu).all())
        for plan in due:
            log.infox("Geplande planning gestart", plan=plan.id, gepland_voor=plan.start_at)
            await svc.advance(plan.id)
            aantal += 1
        # Planningen die wachtten op iets dat tijd kost (de agent vroeg om
        # later verder te gaan) en waarvan de tijd om is.
        wakker = (db.query(TicketPlan)
                  .filter(TicketPlan.state == "paused",
                          TicketPlan.resume_at.isnot(None),
                          TicketPlan.resume_at <= nu).all())
        for plan in wakker:
            log.infox("Wachtende planning hervat", plan=plan.id, wachtte_tot=plan.resume_at)
            plan.resume_at = None
            plan.state = "running"
            plan.note = None
            db.commit()
            await svc.advance(plan.id)
            aantal += 1
        for plan in db.query(TicketPlan).filter(TicketPlan.state == "running").all():
            res = await svc.advance(plan.id)
            if res.get("gestart"):
                aantal += 1
    except Exception as exc:  # noqa: BLE001
        log.warningx("Planning-tick mislukt", error=str(exc)[:300])
    finally:
        db.close()
    return aantal
