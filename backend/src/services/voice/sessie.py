"""De spraaksessie: tijdlijn, toolaanroepen en de bevestigingslus.

Dit is de enige plek waar een schrijfactie daadwerkelijk kan gebeuren, en de
volgorde is met opzet star:

    tool aangeroepen → parameters opgelost → bevestiging weggeschreven
                     → gebruiker bevestigt → exact díé parameters uitgevoerd

Tussen die stappen zit de database, niet het geheugen van een model. Daardoor
kan er geen licht zitten tussen de zin die je hoorde en de actie die volgt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.voice import VoiceEvent, VoicePendingAction, VoiceSession
from services.voice import bevestiging as bev
from services.voice.acties import Antwoord, Context, LEESACTIES as _LEES
from services.voice.opdrachtacties import (OPDRACHT_LEESACTIES,
                                           OPDRACHT_SCHRIJFACTIES,
                                           OPDRACHT_UITVOERDERS)
from services.voice.schrijfacties import SCHRIJFACTIES as _SCHRIJF
from services.voice.schrijfacties import UITVOERDERS as _UITVOER

# De begeleide opdracht hangt er als een aparte laag naast en wordt hier
# samengevoegd: zo hoeft acties.py niets van opdrachtacties.py te weten, en
# blijft die importrichting één kant op.
LEESACTIES = {**_LEES, **OPDRACHT_LEESACTIES}
SCHRIJFACTIES = {**_SCHRIJF, **OPDRACHT_SCHRIJFACTIES}
UITVOERDERS = {**_UITVOER, **OPDRACHT_UITVOERDERS}

log = get_logger(__name__)


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoiceSessieService:
    def __init__(self, db: Session):
        self.db = db
        self.ctx = Context(db)

    # ── sessies ─────────────────────────────────────────────────────────────

    def ruim_op(self, *, minuten: int = 30) -> int:
        """Sessies die niemand meer heeft afgesloten, alsnog sluiten.

        Een sessie bleef "actief" tot je op Stoppen klikte. Navigeer je weg of
        herlaad je de pagina, dan bleef hij staan -- en dan zie je er na een
        middag werken zes openstaan, met evenveel kostenplafonds die los van
        elkaar meetellen. De instelling voor de sessieduur bestond al en werd
        nergens afgedwongen; dit is die instelling.
        """
        from datetime import timedelta

        grens = (datetime.now(timezone.utc) - timedelta(minutes=max(1, minuten)))
        oud = (self.db.query(VoiceSession)
               .filter(VoiceSession.status == "actief",
                       VoiceSession.started_at < grens.isoformat()).all())
        for s in oud:
            self.stop(s.id, reden="verlopen")
        return len(oud)

    def start(self, *, brein: str, microfoon: str) -> VoiceSession:
        # Eén gesprek tegelijk. Twee open sessies betekent twee tijdlijnen,
        # twee concepten en twee kostentellers -- en jij praat maar tegen één.
        for lopend in (self.db.query(VoiceSession)
                       .filter(VoiceSession.status == "actief").all()):
            self.stop(lopend.id, reden="vervangen")

        s = VoiceSession(id=str(uuid4()), brein=brein, microfoon=microfoon,
                         status="actief", started_at=_nu())
        self.db.add(s)
        self.db.commit()
        self.db.refresh(s)
        self.noteer(s.id, "systeem", f"Sessie gestart ({brein}, {microfoon}).")
        # Een sessie die opent met alleen een grijze systeemregel voelt alsof
        # er niets gebeurd is. Deze begroeting kost geen modelaanroep en zegt
        # meteen wat je kunt doen.
        self.noteer(s.id, "assistent", self._begroeting(brein, microfoon))
        return s

    def _begroeting(self, brein: str, microfoon: str) -> str:
        voorbeeld = ("Vraag bijvoorbeeld wat er nu loopt, "
                     "of hoe het met een ticket staat.")
        if brein == "realtime":
            if microfoon == "open":
                return "Zet de microfoon aan en begin maar. " + voorbeeld
            return ("Zet de microfoon aan, houd daarna de praatknop ingedrukt. "
                    + voorbeeld)
        if microfoon == "open":
            return "Ik luister. " + voorbeeld
        return "Houd de praatknop ingedrukt, of typ. " + voorbeeld

    def stop(self, session_id: str, *, reden: str = "gestopt") -> Optional[VoiceSession]:
        s = self.db.get(VoiceSession, session_id)
        if s is None:
            return None
        s.status = "gestopt"
        s.ended_at = _nu()
        s.einde_reden = reden
        # Alles wat nog op bevestiging wachtte, vervalt met de sessie mee.
        # Een actie die een gesprek overleeft is precies wat je niet wilt.
        (self.db.query(VoicePendingAction)
         .filter(VoicePendingAction.session_id == session_id,
                 VoicePendingAction.status == "wacht")
         .update({"status": "verlopen", "resolved_at": _nu()},
                 synchronize_session=False))
        self.db.commit()
        self.noteer(session_id, "systeem", f"Sessie gestopt ({reden}).")
        return s

    def noteer(self, session_id: str, soort: str, tekst: str, *,
               tool: Optional[str] = None, parameters: Optional[Dict] = None,
               resultaat: Optional[str] = None,
               kosten_usd: Optional[float] = None) -> VoiceEvent:
        e = VoiceEvent(session_id=session_id, ts=_nu(), soort=soort, tekst=tekst,
                       tool=tool, parameters=parameters, resultaat=resultaat,
                       kosten_usd=kosten_usd)
        self.db.add(e)
        if kosten_usd:
            s = self.db.get(VoiceSession, session_id)
            if s is not None:
                s.kosten_usd = round((s.kosten_usd or 0.0) + float(kosten_usd), 6)
        self.db.commit()
        self.db.refresh(e)
        return e

    def tijdlijn(self, session_id: str, *, limit: int = 200):
        return (self.db.query(VoiceEvent)
                .filter(VoiceEvent.session_id == session_id)
                .order_by(VoiceEvent.id.asc()).limit(limit).all())

    # ── tools ───────────────────────────────────────────────────────────────

    def roep_tool_aan(self, session_id: str, tool: str,
                      args: Dict[str, Any]) -> Dict[str, Any]:
        """Eén toolaanroep vanuit het brein.

        Lezen levert feiten op. Schrijven levert NOOIT een resultaat op maar
        een bevestiging — dat verschil zit hier in de code en niet in een
        instructie aan het model, want een instructie kan genegeerd worden.
        """
        args = {k: v for k, v in (args or {}).items() if v is not None}

        # De context krijgt de sessie mee: een begeleide opdracht hoort bij
        # dit gesprek en mag niet uit een ander opduiken.
        self.ctx.session_id = session_id

        if tool in LEESACTIES:
            uit: Antwoord = LEESACTIES[tool](self.ctx, **args)
            self.noteer(session_id, "actie", f"{tool}", tool=tool, parameters=args,
                        resultaat=_kort(uit))
            return _als_dict(uit)

        if tool in SCHRIJFACTIES:
            uit = SCHRIJFACTIES[tool](self.ctx, **args)
            if uit.bevestiging is None:
                # Niet gevonden of dubbelzinnig: doorvragen, niets klaarzetten.
                self.noteer(session_id, "actie", f"{tool} (doorvragen)",
                            tool=tool, parameters=args, resultaat=_kort(uit))
                return _als_dict(uit)

            wacht = self._zet_klaar(session_id, uit.bevestiging)
            self.noteer(session_id, "bevestiging", wacht.zin,
                        tool=tool, parameters=wacht.parameters)
            return {
                "wacht_op_bevestiging": True,
                "id": wacht.id,
                "zin": wacht.zin,
                "vervalt_over_seconden": bev.VERVAL_SECONDEN,
            }

        # Onbekende tool: niet stilzwijgend negeren.
        self.noteer(session_id, "fout", f"Onbekende actie '{tool}'.")
        return {"fout": f"'{tool}' bestaat niet."}

    def _zet_klaar(self, session_id: str, b) -> VoicePendingAction:
        # Hoogstens één openstaande bevestiging per sessie: twee tegelijk maakt
        # "ja" dubbelzinnig, en dat is precies wat deze hele lus voorkomt.
        (self.db.query(VoicePendingAction)
         .filter(VoicePendingAction.session_id == session_id,
                 VoicePendingAction.status == "wacht")
         .update({"status": "verlopen", "resolved_at": _nu()},
                 synchronize_session=False))
        rij = VoicePendingAction(
            id=str(uuid4()), session_id=session_id, tool=b.tool,
            parameters=b.parameters, zin=b.zin, status="wacht",
            created_at=_nu(), expires_at=bev.vervalt_op())
        self.db.add(rij)
        self.db.commit()
        self.db.refresh(rij)
        return rij

    def openstaand(self, session_id: str) -> Optional[VoicePendingAction]:
        rij = (self.db.query(VoicePendingAction)
               .filter(VoicePendingAction.session_id == session_id,
                       VoicePendingAction.status == "wacht")
               .order_by(VoicePendingAction.created_at.desc()).first())
        if rij is None:
            return None
        if bev.is_verlopen(rij.expires_at):
            rij.status = "verlopen"
            rij.resolved_at = _nu()
            self.db.commit()
            self.noteer(session_id, "systeem", "De bevestiging is vervallen.")
            return None
        return rij

    def net_verlopen(self, session_id: str, binnen_seconden: int = 120
                     ) -> Optional[VoicePendingAction]:
        """De laatste bevestiging die kort geleden is vervallen.

        Nodig om een te laat antwoord te herkennen. Zonder dit belandt een
        late "henk" als gewone zin bij het brein, dat dan dezelfde actie
        opnieuw voorstelt -- je denkt te bevestigen en krijgt ongemerkt een
        nieuw voorstel voor je.
        """
        from datetime import datetime, timedelta, timezone

        rij = (self.db.query(VoicePendingAction)
               .filter(VoicePendingAction.session_id == session_id,
                       VoicePendingAction.status == "verlopen")
               .order_by(VoicePendingAction.resolved_at.desc()).first())
        if rij is None or not rij.resolved_at:
            return None
        try:
            toen = datetime.fromisoformat(rij.resolved_at)
        except ValueError:
            return None
        if toen.tzinfo is None:
            toen = toen.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - toen > timedelta(seconds=binnen_seconden):
            return None
        return rij

    # ── bevestigen ──────────────────────────────────────────────────────────

    async def beantwoord(self, session_id: str, *, antwoord: Optional[str] = None,
                         via_klik: bool = False, akkoord: bool = True,
                         woord: Optional[str] = None,
                         stand: str = "beide") -> Dict[str, Any]:
        """Een openstaande bevestiging afhandelen.

        `via_klik` komt van de knop in de interface, `antwoord` van de stem.
        Welke van de twee is toegestaan, bepaalt de ingestelde stand — anders
        sta je "Henk" te roepen tegen een systeem dat alleen naar een klik
        luistert, of andersom.
        """
        rij = self.openstaand(session_id)
        if rij is None:
            return {"status": "niets", "melding": "Er staat niets te bevestigen."}

        if via_klik:
            if stand == "spraak":
                return {"status": "geweigerd_kanaal",
                        "melding": "Bevestigen gaat in deze stand met je stem."}
            bevestigd = bool(akkoord)
        else:
            if stand == "klik":
                return {"status": "geweigerd_kanaal",
                        "melding": "Bevestigen gaat in deze stand via het scherm."}
            bevestigd = bev.is_bevestiging(antwoord, woord)
            self.noteer(session_id, "antwoord", str(antwoord or ""))

        if not bevestigd:
            rij.status = "geweigerd"
            rij.resolved_at = _nu()
            self.db.commit()
            self.noteer(session_id, "systeem", "Niet bevestigd — er is niets gebeurd.")
            return {"status": "geweigerd",
                    "melding": "Niet bevestigd, dus ik heb niets gedaan."}

        uitvoerder = UITVOERDERS.get(rij.tool)
        if uitvoerder is None:
            rij.status = "geweigerd"
            rij.resolved_at = _nu()
            self.db.commit()
            return {"status": "fout", "melding": f"'{rij.tool}' kan ik niet uitvoeren."}

        try:
            # Exact de parameters die in de zin stonden. Hier wordt niets
            # opnieuw opgezocht: dat is het hele punt van de bevestiging.
            resultaat = await uitvoerder(self.db, dict(rij.parameters or {}))
            rij.status = "bevestigd"
            rij.resultaat = resultaat
        except Exception as exc:  # noqa: BLE001 — de gebruiker moet horen wat er misging
            rij.status = "bevestigd"
            rij.resultaat = f"mislukt: {str(exc)[:300]}"
            log.warningx("Spraakactie mislukt", tool=rij.tool, error=str(exc)[:300])
        rij.resolved_at = _nu()
        self.db.commit()

        self.noteer(session_id, "actie", rij.zin, tool=rij.tool,
                    parameters=rij.parameters, resultaat=rij.resultaat)
        return {"status": "uitgevoerd", "melding": rij.resultaat}


def _als_dict(a: Antwoord) -> Dict[str, Any]:
    uit: Dict[str, Any] = {}
    if a.feiten is not None:
        uit["feiten"] = a.feiten
    if a.vraag:
        uit["vraag"] = a.vraag
    if a.keuzes:
        uit["keuzes"] = a.keuzes
    return uit or {"feiten": {}}


def _kort(a: Antwoord) -> str:
    if a.vraag:
        return a.vraag[:300]
    if a.feiten:
        return str(a.feiten)[:600]
    return ""
