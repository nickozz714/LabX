"""routers/voice_router.py — de spraakassistent.

Elk endpoint hier begint met dezelfde controle: staat de functie aan? Zo niet,
dan bestaat hij niet (404). Dat is sterker dan een onzichtbaar tabblad: ook
wie het adres kent, komt er niet in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.voice import VoiceEvent, VoiceSession
from services.voice import bevestiging as bev
from services.voice.sessie import VoiceSessieService

router = APIRouter(prefix="/voice", tags=["voice"], dependencies=[Depends(require_user)])


def _instellingen(db: Session):
    from services.settings_service import get_settings
    return get_settings(db)


def _vereis_aan(db: Session):
    """De functie staat uit → dit endpoint bestaat niet."""
    s = _instellingen(db)
    if not getattr(s, "voice_enabled", False):
        raise HTTPException(status_code=404, detail="Not Found")
    return s


@router.get("/status")
def status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Of de functie aanstaat, en waarmee. Dit endpoint mag ALTIJD — de
    interface moet kunnen weten of het tabblad getoond wordt."""
    s = _instellingen(db)
    aan = bool(getattr(s, "voice_enabled", False))
    uit: Dict[str, Any] = {"aan": aan}
    if aan:
        uit.update({
            "brein": s.voice_brein,
            "microfoon": s.voice_microfoon,
            "bevestiging": s.voice_bevestiging,
            "woord": s.voice_woord or bev.STANDAARD_WOORD,
            "meld_runs": bool(s.voice_meld_runs),
            "sessie_minuten": s.voice_sessie_minuten,
            "dag_limiet_usd": s.voice_dag_limiet_usd,
            "verval_seconden": bev.VERVAL_SECONDEN,
            "vandaag_usd": _vandaag_usd(db),
            # Zonder sleutel werkt alleen het typveld; dat mag de interface weten.
            "sleutel_aanwezig": bool(s.openai_key_encrypted),
        })
    return uit


def _vandaag_usd(db: Session) -> float:
    vandaag = datetime.now(timezone.utc).date().isoformat()
    rijen = (db.query(VoiceSession)
             .filter(VoiceSession.started_at >= vandaag).all())
    return round(sum(r.kosten_usd or 0.0 for r in rijen), 4)


@router.post("/sessies")
def start_sessie(payload: Optional[Dict[str, Any]] = None,
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    s = _vereis_aan(db)
    payload = payload or {}

    # Het dagplafond weigert een nieuwe sessie; een lopende sessie breekt hij
    # niet af. Midden in een zin afgekapt worden is erger dan een euro meer.
    limiet = float(s.voice_dag_limiet_usd or 0)
    if limiet > 0 and _vandaag_usd(db) >= limiet:
        raise HTTPException(status_code=429, detail=(
            f"Het dagplafond van ${limiet:.2f} is bereikt. "
            "Morgen weer, of zet het plafond hoger bij Instellingen."))

    svc = VoiceSessieService(db)
    sessie = svc.start(brein=payload.get("brein") or s.voice_brein,
                       microfoon=payload.get("microfoon") or s.voice_microfoon)
    return _sessie_dto(sessie)


@router.post("/sessies/{session_id}/stop")
def stop_sessie(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _vereis_aan(db)
    sessie = VoiceSessieService(db).stop(session_id)
    if sessie is None:
        raise HTTPException(status_code=404, detail="Deze sessie bestaat niet")
    return _sessie_dto(sessie)


@router.get("/sessies/{session_id}")
def haal_sessie(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _vereis_aan(db)
    sessie = db.get(VoiceSession, session_id)
    if sessie is None:
        raise HTTPException(status_code=404, detail="Deze sessie bestaat niet")
    svc = VoiceSessieService(db)
    wacht = svc.openstaand(session_id)
    uit = _sessie_dto(sessie)
    uit["tijdlijn"] = [_event_dto(e) for e in svc.tijdlijn(session_id)]
    uit["openstaand"] = {
        "id": wacht.id, "zin": wacht.zin, "vervalt_op": wacht.expires_at,
    } if wacht else None
    return uit


@router.get("/sessies")
def lijst_sessies(limit: int = 20, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    _vereis_aan(db)
    rijen = (db.query(VoiceSession)
             .order_by(VoiceSession.started_at.desc())
             .limit(max(1, min(limit, 100))).all())
    return [_sessie_dto(r) for r in rijen]


@router.post("/sessies/{session_id}/zeg")
async def zeg(session_id: str, payload: Dict[str, Any],
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Eén beurt: wat de gebruiker zei (of typte) gaat naar het brein.

    Hetzelfde endpoint voor spraak en toetsenbord. Het typveld is niet alleen
    een testhulpje: het maakt de hele functie bruikbaar zonder microfoon, en
    zonder OpenAI-sleutel.
    """
    s = _vereis_aan(db)
    tekst = str(payload.get("tekst") or "").strip()
    if not tekst:
        raise HTTPException(status_code=400, detail="Er is niets gezegd")

    sessie = db.get(VoiceSession, session_id)
    if sessie is None or sessie.status != "actief":
        raise HTTPException(status_code=409, detail="Deze sessie loopt niet")

    svc = VoiceSessieService(db)

    # Staat er een bevestiging open, dan is dit antwoord dáárvoor bedoeld —
    # niet een nieuwe opdracht. Anders zou "Henk" als losse vraag naar het
    # brein gaan en de wachtende actie stilletjes verlopen.
    if svc.openstaand(session_id) is not None:
        uit = await svc.beantwoord(session_id, antwoord=tekst,
                                   woord=s.voice_woord,
                                   stand=s.voice_bevestiging)
        if uit["status"] != "geweigerd_kanaal":
            svc.noteer(session_id, "assistent", uit.get("melding") or "")
            return {"antwoord": uit.get("melding"), "bevestiging": uit}

    svc.noteer(session_id, "gebruiker", tekst)
    from services.voice.brein import antwoord_op
    uit = await antwoord_op(db, sessie, tekst)
    svc.noteer(session_id, "assistent", uit.get("antwoord") or "",
               kosten_usd=uit.get("kosten_usd"))
    return uit


@router.post("/sessies/{session_id}/bevestig")
async def bevestig(session_id: str, payload: Optional[Dict[str, Any]] = None,
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """De knop in de interface. Spraakbevestiging loopt via /zeg."""
    s = _vereis_aan(db)
    payload = payload or {}
    uit = await VoiceSessieService(db).beantwoord(
        session_id, via_klik=True, akkoord=bool(payload.get("akkoord", True)),
        stand=s.voice_bevestiging)
    return uit


@router.post("/sessies/{session_id}/tool")
def tool(session_id: str, payload: Dict[str, Any],
         db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Eén actie rechtstreeks aanroepen.

    Hier komt het realtime-model binnen: dat praat zelf met de browser en
    stuurt alleen zijn toolaanroepen hierheen.
    """
    _vereis_aan(db)
    naam = str(payload.get("tool") or "").strip()
    if not naam:
        raise HTTPException(status_code=400, detail="Geen actie opgegeven")
    return VoiceSessieService(db).roep_tool_aan(
        session_id, naam, payload.get("args") or {})


@router.get("/tools")
def tools(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """Het toolschema, voor het realtime-model."""
    _vereis_aan(db)
    from services.voice.schema import TOOLSCHEMA
    return TOOLSCHEMA


@router.post("/realtime-token")
async def realtime_token(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Een kortlevend token waarmee de BROWSER rechtstreeks met OpenAI praat.

    De API-sleutel zelf verlaat de server nooit. Dat is het hele punt van deze
    omweg: zonder dit zou de sleutel in de browser moeten staan.
    """
    s = _vereis_aan(db)
    if s.voice_brein != "realtime":
        raise HTTPException(status_code=409,
                            detail="Het realtime-brein staat niet aan.")
    from services.voice.realtime import maak_client_secret
    try:
        return await maak_client_secret(db)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)[:400])


@router.post("/transcribeer")
async def transcribeer(payload: Dict[str, Any],
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Audio uit de browser naar tekst, voor het pijplijn-brein."""
    _vereis_aan(db)
    audio = payload.get("audio_base64")
    if not audio:
        raise HTTPException(status_code=400, detail="Geen audio meegestuurd")
    from services.voice.stt import transcribeer_base64
    try:
        return {"tekst": await transcribeer_base64(db, str(audio),
                                                   payload.get("mime") or "audio/webm")}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)[:400])


def _sessie_dto(s: VoiceSession) -> Dict[str, Any]:
    return {"id": s.id, "brein": s.brein, "microfoon": s.microfoon,
            "status": s.status, "kosten_usd": round(s.kosten_usd or 0.0, 4),
            "beurten": s.beurten, "started_at": s.started_at,
            "ended_at": s.ended_at, "einde_reden": s.einde_reden}


def _event_dto(e: VoiceEvent) -> Dict[str, Any]:
    return {"id": e.id, "ts": e.ts, "soort": e.soort, "tekst": e.tekst,
            "tool": e.tool, "parameters": e.parameters, "resultaat": e.resultaat,
            "kosten_usd": e.kosten_usd}
