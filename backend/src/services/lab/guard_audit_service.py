"""
services/lab/guard_audit_service.py

Het audit-spoor van de data-guard wegschrijven en teruglezen.

**Waarom het origineel erin gaat.** Het oude spoor bewaarde een classificatie,
een reden en het AANTAL bytes. Daarmee kon je niet nagaan of er terecht iets
was tegengehouden — je zag "leest ruwe bestandsinhoud" bij een commando dat 13
bytes teruggaf, en verder niets. Je kon dus niet vaststellen dat de guard
ernaast zat, en al helemaal niet dat hij iets liet passeren dat er niet
doorheen had gemogen. Een audit die de vraag "is dit goed gegaan?" niet kan
beantwoorden, is geen audit.

**En waarom dat verantwoord kan.** Dit spoor bevat per definitie precies de
gegevens die de guard tegenhield. Beide teksten staan daarom versleuteld
(Fernet, dezelfde sleutel als tokens en board-secrets), ze gaan nooit terug
naar een model, ze zijn alleen op te vragen door een ingelogde gebruiker, en ze
worden na een termijn opgeruimd. Het alternatief — niets bewaren — maakt de
guard oncontroleerbaar, en een guard die niemand kan controleren is een
geloofsartikel.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.guard_audit import GuardAudit

log = get_logger(__name__)

# Hoeveel er van een tekst bewaard wordt. Ruim genoeg om te beoordelen wat
# eruit kwam, klein genoeg dat een uitvoer van 97 MB de database niet opblaast.
MAX_BEWAARD = 200_000
# Hoe lang. Lang genoeg om achteraf iets uit te zoeken, kort genoeg dat er geen
# archief van klantgegevens ontstaat dat niemand meer beheert.
BEWAARTERMIJN_DAGEN = 14


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sleutel(tekst: Optional[str]) -> Optional[str]:
    if not tekst:
        return None
    from utils.crypto import encrypt
    return encrypt(tekst[:MAX_BEWAARD])


def _ontsleutel(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    from utils.crypto import decrypt
    try:
        return decrypt(token)
    except Exception as exc:  # noqa: BLE001
        log.warningx("Audit-tekst kon niet ontsleuteld worden", error=str(exc)[:120])
        return "[kon niet ontsleuteld worden — is LABX_FERNET_KEY gewijzigd?]"


def leg_vast(db: Session, *, lab_id: Optional[str], lab_name: Optional[str] = None,
             worker_id: Optional[int] = None, run_id: Optional[str] = None,
             command: Optional[str], outcome: str,
             origineel: Optional[str], geleverd: Optional[str],
             findings: Optional[List[Dict[str, Any]]] = None,
             llm_verdict: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Eén regel in het spoor. Faalt nooit hard: een audit die de uitvoering
    laat struikelen is erger dan een ontbrekende regel."""
    try:
        rij = GuardAudit(
            ts=_now_iso(), lab_id=lab_id, lab_name=lab_name, worker_id=worker_id,
            run_id=run_id, command=(command or "")[:20000] or None,
            outcome=outcome, findings=list(findings or []), llm_verdict=llm_verdict,
            original_encrypted=_sleutel(origineel),
            delivered_encrypted=_sleutel(geleverd),
            bytes_original=len(origineel or ""), bytes_delivered=len(geleverd or ""))
        db.add(rij)
        db.commit()
        return rij.id
    except Exception as exc:  # noqa: BLE001
        log.warningx("Guard-audit wegschrijven mislukt", error=str(exc)[:200])
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def werk_bij(db: Session, audit_id: Optional[int], *, outcome: Optional[str] = None,
             geleverd: Optional[str] = None,
             llm_verdict: Optional[Dict[str, Any]] = None) -> None:
    """De regel aanvullen nadat het lokale model zijn oordeel gaf.

    Dat oordeel komt ná het wegschrijven, omdat de modelaanroep async is en de
    guard zelf niet. Zonder deze aanvulling zou in de audit staan dat iets is
    doorgelaten terwijl het model het alsnog tegenhield."""
    if not audit_id:
        return
    try:
        rij = db.get(GuardAudit, audit_id)
        if rij is None:
            return
        if outcome:
            rij.outcome = outcome
        if llm_verdict is not None:
            rij.llm_verdict = llm_verdict
        if geleverd is not None:
            rij.delivered_encrypted = _sleutel(geleverd)
            rij.bytes_delivered = len(geleverd)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warningx("Guard-audit bijwerken mislukt", error=str(exc)[:200])


def lijst(db: Session, *, lab_id: Optional[str] = None, outcome: Optional[str] = None,
          limit: int = 50) -> List[Dict[str, Any]]:
    """Het overzicht. ZONDER de teksten — die vraag je per regel op, zodat
    klantgegevens niet in een lijstweergave meekomen die je toevallig openhebt."""
    q = db.query(GuardAudit)
    if lab_id:
        q = q.filter(GuardAudit.lab_id == lab_id)
    if outcome:
        q = q.filter(GuardAudit.outcome == outcome)
    rijen = q.order_by(GuardAudit.id.desc()).limit(max(1, min(limit, 500))).all()
    return [{
        "id": r.id, "ts": r.ts, "lab_id": r.lab_id, "lab_name": r.lab_name,
        "worker_id": r.worker_id, "run_id": r.run_id,
        "command": (r.command or "")[:400],
        "outcome": r.outcome, "findings": r.findings or [],
        "llm_verdict": r.llm_verdict,
        "bytes_original": r.bytes_original, "bytes_delivered": r.bytes_delivered,
        "heeft_tekst": bool(r.original_encrypted),
    } for r in rijen]


def detail(db: Session, audit_id: int) -> Optional[Dict[str, Any]]:
    """Eén regel MET beide teksten, naast elkaar. Dit is waar je de vraag
    beantwoordt: klopte het dat dit is tegengehouden, en is er niets
    doorgelaten dat er niet doorheen had gemogen?"""
    r = db.get(GuardAudit, audit_id)
    if r is None:
        return None
    return {
        "id": r.id, "ts": r.ts, "lab_id": r.lab_id, "lab_name": r.lab_name,
        "worker_id": r.worker_id, "run_id": r.run_id, "command": r.command,
        "outcome": r.outcome, "findings": r.findings or [],
        "llm_verdict": r.llm_verdict,
        "bytes_original": r.bytes_original, "bytes_delivered": r.bytes_delivered,
        "origineel": _ontsleutel(r.original_encrypted),
        "geleverd": _ontsleutel(r.delivered_encrypted),
    }


def ruim_op(db: Session, dagen: int = BEWAARTERMIJN_DAGEN) -> int:
    """Oude regels weggooien. Draait vanuit de scheduler: zonder dit groeit er
    een archief van precies de gegevens die we tegenhielden."""
    grens = (datetime.now(timezone.utc) - timedelta(days=max(1, dagen))).isoformat()
    aantal = (db.query(GuardAudit).filter(GuardAudit.ts < grens)
              .delete(synchronize_session=False))
    if aantal:
        db.commit()
        log.infox("Guard-audit opgeruimd", verwijderd=aantal, ouder_dan_dagen=dagen)
    return aantal
