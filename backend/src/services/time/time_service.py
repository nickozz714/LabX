"""Urenregistratie: wat is er wanneer aan welk project gedaan.

Drie bronnen, bewust uit elkaar gehouden (zie `models/time_entry.py`):

1. **Gemeten** — de looptijd van agent-runs. Komt er vanzelf in, is hard, maar
   het is MACHINEtijd. Een run die op een pipeline wacht, meet die wachttijd
   mee. Dit is context, geen urenstaat.
2. **Geschat** — een schatting van de tijd die JIJ kwijt was, afgeleid uit je
   eigen berichten en opmerkingen. Hier komt een uurregel uit, en daarom staat
   er altijd bij dat het een schatting is.
3. **Gemeld / handmatig** — de agent of jij, met de hand.

En daar bovenop de verdeling (`verdeling.py`): bruto optellen over projecten
die tegelijk liepen geeft meer uren dan er klok voorbij is.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models.background_run import BackgroundRun
from models.board import Board, Ticket, TicketComment
from models.message import Message
from models.thread import Thread
from models.time_entry import TimeEntry
from services.time import verdeling
from component_logging import get_logger

log = get_logger(__name__)

# Een run die langer dan dit duurt, is niet geloofwaardig als aaneengesloten
# werk: hij is blijven hangen, of de container stond te wachten. Meetellen zou
# het beeld meer vervuilen dan verbeteren.
MAX_RUN_MINUTEN = 6 * 60

# --- schattingsregels, bewust expliciet en bij elkaar ------------------------
# Een schatting waarvan je de aannames niet kunt zien, is een getal dat je niet
# kunt verdedigen tegenover een klant. Daarom staan ze hier bij elkaar in
# plaats van verspreid door de code.
GAT_MINUTEN = 20        # langer niets gedaan = een nieuw werkblok
AANLOOP_MINUTEN = 3     # lezen en nadenken vóór je eerste bericht
UITLOOP_MINUTEN = 2     # het antwoord lezen na je laatste bericht
MAX_BLOK_MINUTEN = 180  # één aaneengesloten blok; daarboven is het een gok

# De activiteitregel die `agent_work.start_ticket_run` schrijft bij een start
# vanaf de knop. Het is onze eigen tekst, dus hierop matchen is veilig — en het
# is het enige spoor dat zegt "hier drukte een mens".
_HANDMATIG_GESTART = "Agent gestart (handmatig)"


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stip(waarde: Optional[str]) -> Optional[datetime]:
    return verdeling._stip(waarde)


class TimeService:
    def __init__(self, db: Session):
        self.db = db

    # ── 1. gemeten: de looptijd van agent-runs ──────────────────────────────

    def verzamel_gemeten(self, *, sinds: Optional[str] = None) -> int:
        """Agent-runs omzetten in gemeten tijdregels. Idempotent.

        De koppeling run → ticket loopt via de THREAD, niet via
        `ticket.agent_run_id`: dat veld onthoudt alleen de laatste run, dus wie
        daarop vertrouwt raakt alle eerdere runs van hetzelfde ticket kwijt. Op
        de echte data scheelde dat 122 tegenover 468 koppelbare runs.
        """
        ticket_van_thread: Dict[str, Ticket] = {}
        for t in self.db.query(Ticket).filter(Ticket.agent_thread_id.isnot(None)).all():
            ticket_van_thread[str(t.agent_thread_id)] = t

        bestaand = {r[0] for r in self.db.query(TimeEntry.source_run_id)
                    .filter(TimeEntry.source_run_id.isnot(None)).all()}

        q = self.db.query(BackgroundRun).filter(
            BackgroundRun.started_at.isnot(None), BackgroundRun.finished_at.isnot(None))
        if sinds:
            q = q.filter(BackgroundRun.started_at >= sinds)

        nieuw = 0
        for run in q.all():
            if run.id in bestaand:
                continue
            ticket = ticket_van_thread.get(str(run.thread_id or ""))
            if ticket is None:
                continue
            begin, eind = _stip(run.started_at), _stip(run.finished_at)
            if not begin or not eind or eind <= begin:
                continue
            minuten = (eind - begin).total_seconds() / 60
            if minuten > MAX_RUN_MINUTEN:
                continue
            self.db.add(TimeEntry(
                board_id=ticket.board_id, ticket_id=ticket.id,
                project=ticket.project, category=None,
                kind="gemeten", minutes=round(minuten, 1),
                source_run_id=run.id, source_thread_id=run.thread_id,
                started_at=run.started_at, ended_at=run.finished_at,
                day=str(run.started_at)[:10],
                note=None, approved=0, created_at=_nu(), updated_at=_nu()))
            nieuw += 1
        if nieuw:
            self.db.commit()
            log.infox("Gemeten tijd verzameld", nieuw=nieuw)
        return nieuw

    # ── 2. geschat: jouw eigen tijd ─────────────────────────────────────────

    def _eigen_auteurs(self) -> set:
        """Onder welke namen JIJ opmerkingen schrijft.

        `user` is wat LabX zelf gebruikt als je in de app een opmerking plaatst.
        Daarnaast sta je in de bron onder je eigen naam, en die verschilt per
        systeem — dus die komt uit de instellingen.

        Dit is geen detail. Eerder telde deze schatting elke opmerking mee die
        niet van de agent kwam, en dus ook die van collega's en
        klantmedewerkers die via de sync meeliften. Op de echte data gaf dat
        23 uur bij de klant met veel Jira-verkeer en 1,4 uur bij de klant waar
        het meeste werk lag — precies omgekeerd aan de werkelijkheid.
        """
        from services.settings_service import get_settings
        namen = {"user"}
        try:
            for n in (get_settings(self.db).eigen_auteurs or []):
                if str(n).strip():
                    namen.add(str(n).strip().lower())
        except Exception:  # noqa: BLE001 — zonder instelling gewoon de standaard
            pass
        return namen

    def _eigen_momenten(self, ticket: Ticket, eigen: Optional[set] = None) -> List[datetime]:
        """De tijdstippen waarop JIJ aan dit ticket zat.

        Twee bronnen, en het onderscheid is wezenlijk:
        - berichten in een CHAT-thread met rol `user` — die heb jij getypt;
        - opmerkingen die onder JOUW naam staan.

        Een bericht met rol `user` in een BOARD-thread telt NIET mee: dat is de
        opdrachttekst die LabX zelf voor de agent samenstelt, niet iemand die
        zit te typen. Dat verschil is het halve verschil tussen een schatting
        en een verzinsel.

        Wat dit NIET ziet: de tijd die je besteedt aan het lézen van wat de
        agent opleverde. Die laat geen spoor na. De schatting is dus een
        ondergrens, geen volledige urenstaat — en dat is beter dan een getal
        dat volledig lijkt en het niet is.
        """
        eigen = eigen if eigen is not None else self._eigen_auteurs()
        momenten: List[datetime] = []

        threads = [t.id for t in self.db.query(Thread)
                   .filter(Thread.id == ticket.agent_thread_id, Thread.source == "chat").all()]
        if threads:
            for (ts,) in (self.db.query(Message.created_at)
                          .filter(Message.thread_id.in_(threads), Message.role == "user").all()):
                d = _stip(ts)
                if d:
                    momenten.append(d)

        for c in (self.db.query(TicketComment)
                  .filter(TicketComment.ticket_id == ticket.id).all()):
            if (c.kind or "") == "activity":
                # Activiteitregels schrijft LabX zelf, dus normaal zeggen ze
                # niets over JOUW tijd. Op één na: "Agent gestart (handmatig)"
                # betekent dat je op de knop drukte. Dat is het enige harde
                # bewijs dat je op dat moment achter het scherm zat, en zonder
                # dat signaal mist de schatting alle tickets waar je stuurde
                # zonder te typen — en dat is bij jou het meeste werk.
                if _HANDMATIG_GESTART in (c.body or ""):
                    d = _stip(c.created_at)
                    if d:
                        momenten.append(d)
                continue
            if (c.author or "").strip().lower() not in eigen:
                continue           # iemand anders; niet jouw tijd
            d = _stip(c.created_at)
            if d:
                momenten.append(d)

        return sorted(momenten)

    @staticmethod
    def _blokken(momenten: List[datetime]) -> List[tuple]:
        """Losse tijdstippen clusteren tot werkblokken."""
        if not momenten:
            return []
        blokken, start, vorige = [], momenten[0], momenten[0]
        for m in momenten[1:]:
            if (m - vorige).total_seconds() / 60 > GAT_MINUTEN:
                blokken.append((start, vorige))
                start = m
            vorige = m
        blokken.append((start, vorige))
        return blokken

    def schat_eigen_tijd(self, ticket: Ticket, *, eigen: Optional[set] = None) -> int:
        """Schat hoeveel tijd jij aan dit ticket kwijt was en leg het vast.

        Bestaande schattingen voor dit ticket worden vervangen: de schatting is
        een afgeleide van de chat en de opmerkingen, dus hij hoort mee te
        bewegen als daar iets bij komt. Een regel die je zelf hebt afgetekend
        of gecorrigeerd blijft staan — die is dan geen schatting meer.
        """
        momenten = self._eigen_momenten(ticket, eigen)
        blokken = self._blokken(momenten)

        (self.db.query(TimeEntry)
         .filter(TimeEntry.ticket_id == ticket.id, TimeEntry.kind == "geschat",
                 TimeEntry.approved == 0)
         .delete(synchronize_session=False))

        gezet = 0
        for begin, eind in blokken:
            duur = (eind - begin).total_seconds() / 60 + AANLOOP_MINUTEN + UITLOOP_MINUTEN
            duur = min(duur, MAX_BLOK_MINUTEN)
            self.db.add(TimeEntry(
                board_id=ticket.board_id, ticket_id=ticket.id,
                project=ticket.project, category=None,
                kind="geschat", minutes=round(duur, 1),
                source_run_id=None, source_thread_id=ticket.agent_thread_id,
                started_at=(begin - timedelta(minutes=AANLOOP_MINUTEN)).isoformat(),
                ended_at=(eind + timedelta(minutes=UITLOOP_MINUTEN)).isoformat(),
                day=begin.isoformat()[:10],
                note=f"Schatting uit {len(momenten)} eigen berichten/opmerkingen",
                approved=0, created_at=_nu(), updated_at=_nu()))
            gezet += 1
        self.db.commit()
        return gezet

    def schat_alles(self, *, board_id: Optional[int] = None) -> int:
        q = self.db.query(Ticket)
        if board_id:
            q = q.filter(Ticket.board_id == board_id)
        eigen = self._eigen_auteurs()      # één keer, niet per ticket
        totaal = 0
        for ticket in q.all():
            totaal += self.schat_eigen_tijd(ticket, eigen=eigen)
        return totaal

    # ── 3. met de hand / door de agent ──────────────────────────────────────

    def log(self, *, ticket_id: Optional[int], board_id: int, minuten: float,
            kind: str = "gemeld", project: Optional[str] = None,
            category: Optional[str] = None, note: Optional[str] = None,
            dag: Optional[str] = None,
            started_at: Optional[str] = None,
            ended_at: Optional[str] = None) -> TimeEntry:
        ticket = self.db.get(Ticket, ticket_id) if ticket_id else None
        regel = TimeEntry(
            board_id=board_id, ticket_id=ticket_id,
            project=project or (ticket.project if ticket else None),
            category=(category or "").strip() or None,
            kind=kind if kind in ("gemeld", "handmatig") else "handmatig",
            minutes=round(max(0.0, float(minuten)), 1),
            source_run_id=None, source_thread_id=None,
            started_at=started_at, ended_at=ended_at,
            day=(dag or (started_at or _nu()))[:10],
            note=note, approved=1 if kind == "handmatig" else 0,
            created_at=_nu(), updated_at=_nu())
        self.db.add(regel)
        self.db.commit()
        self.db.refresh(regel)
        return regel

    def bewerk(self, entry_id: int, payload: Dict[str, Any]) -> TimeEntry:
        regel = self.db.get(TimeEntry, entry_id)
        if regel is None:
            raise ValueError("Deze tijdregel bestaat niet")
        for veld in ("project", "category", "note"):
            if veld in payload:
                waarde = (str(payload[veld] or "")).strip()
                setattr(regel, veld, waarde or None)
        if "minutes" in payload:
            regel.minutes = round(max(0.0, float(payload["minutes"] or 0)), 1)
            # Een gecorrigeerd getal is geen schatting meer.
            if regel.kind == "geschat":
                regel.kind = "handmatig"
        if "approved" in payload:
            regel.approved = 1 if payload["approved"] else 0
        if "day" in payload and payload["day"]:
            regel.day = str(payload["day"])[:10]
        regel.updated_at = _nu()
        self.db.commit()
        self.db.refresh(regel)
        return regel

    def verwijder(self, entry_id: int) -> bool:
        regel = self.db.get(TimeEntry, entry_id)
        if regel is None:
            return False
        self.db.delete(regel)
        self.db.commit()
        return True

    # ── het overzicht ───────────────────────────────────────────────────────

    def overzicht(self, *, van: Optional[str] = None, tot: Optional[str] = None,
                  board_id: Optional[int] = None) -> Dict[str, Any]:
        """Uren per project, met de verdeling over wat tegelijk liep.

        `van`/`tot` zijn dagen (YYYY-MM-DD), `tot` meegerekend.
        """
        q = self.db.query(TimeEntry)
        if van:
            q = q.filter(TimeEntry.day >= van)
        if tot:
            q = q.filter(TimeEntry.day <= tot)
        if board_id:
            q = q.filter(TimeEntry.board_id == board_id)
        regels = q.all()

        borden = {b.id: b.name for b in self.db.query(Board).all()}
        tickets = {t.id: t for t in self.db.query(Ticket).all()}

        def sleutel(r: TimeEntry) -> str:
            """Zonder project valt een regel terug op de klant. Beter dan hem
            op één hoop '(geen project)' gooien: dan is het overzicht leeg
            precies zolang je de projecten nog niet hebt ingevuld."""
            if r.project:
                return r.project
            return borden.get(r.board_id, "onbekend")

        # De verdeling kijkt alleen naar regels met een echt interval. De rest
        # kan niet overlappen, want we weten niet wannéér hij viel.
        met_tijd = [{"started_at": r.started_at, "ended_at": r.ended_at,
                     "project": sleutel(r)}
                    for r in regels if r.started_at and r.ended_at]
        intervallen = verdeling.intervallen_van(met_tijd)
        verdeeld = verdeling.verdeel(intervallen)

        per_project: Dict[str, Dict[str, Any]] = {}
        for r in regels:
            p = sleutel(r)
            rij = per_project.setdefault(p, {
                "project": p, "gemeten": 0.0, "geschat": 0.0, "gemeld": 0.0,
                "handmatig": 0.0, "regels": 0, "tickets": set(), "categorieen": {},
            })
            rij[r.kind] = rij.get(r.kind, 0.0) + r.minutes
            rij["regels"] += 1
            if r.ticket_id:
                rij["tickets"].add(r.ticket_id)
            if r.category:
                rij["categorieen"][r.category] = \
                    rij["categorieen"].get(r.category, 0.0) + r.minutes

        projecten = []
        for p, rij in per_project.items():
            v = verdeeld.get(p, {})
            # Wat je zou schrijven: jouw eigen tijd. Gemeten agent-tijd staat
            # ernaast als context en telt hier NIET in mee.
            eigen = rij["geschat"] + rij["gemeld"] + rij["handmatig"]
            projecten.append({
                "project": p,
                "eigen_minuten": round(eigen, 1),
                "geschat": round(rij["geschat"], 1),
                "gemeld": round(rij["gemeld"], 1),
                "handmatig": round(rij["handmatig"], 1),
                "agent_minuten": round(rij["gemeten"], 1),
                "bruto": v.get("bruto", 0.0),
                "verdeeld": v.get("verdeeld", 0.0),
                "overlap": v.get("overlap", 0.0),
                "tickets": len(rij["tickets"]),
                "regels": rij["regels"],
                "categorieen": sorted(
                    ({"naam": k, "minuten": round(m, 1)}
                     for k, m in rij["categorieen"].items()),
                    key=lambda x: -x["minuten"]),
            })
        projecten.sort(key=lambda x: -x["bruto"])

        bruto_totaal = sum(p["bruto"] for p in projecten)
        verdeeld_totaal = sum(p["verdeeld"] for p in projecten)
        return {
            "van": van, "tot": tot,
            "projecten": projecten,
            "totaal": {
                "eigen_minuten": round(sum(p["eigen_minuten"] for p in projecten), 1),
                "agent_minuten": round(sum(p["agent_minuten"] for p in projecten), 1),
                "bruto": round(bruto_totaal, 1),
                "verdeeld": round(verdeeld_totaal, 1),
                "overlap": round(bruto_totaal - verdeeld_totaal, 1),
                "klok": verdeling.klok(intervallen),
            },
        }

    def categorieen(self) -> List[str]:
        """Wat je eerder gebruikte, voor de autoaanvulling."""
        rijen = (self.db.query(TimeEntry.category)
                 .filter(TimeEntry.category.isnot(None)).distinct().all())
        return sorted({str(r[0]).strip() for r in rijen if (r[0] or "").strip()})

    def projecten(self) -> List[str]:
        """Bestaande projecten plus de labels die je al gebruikt — het eerste
        zetje voor een veld dat nog leeg is."""
        uit = {str(t[0]).strip() for t in
               self.db.query(Ticket.project).filter(Ticket.project.isnot(None)).distinct().all()
               if (t[0] or "").strip()}
        for (labels,) in self.db.query(Ticket.labels).filter(Ticket.labels.isnot(None)).all():
            for x in (labels or []):
                if str(x).strip():
                    uit.add(str(x).strip())
        return sorted(uit)
