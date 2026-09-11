"""routers/guard_router.py — de data-guard beheren en controleren.

Drie dingen, en ze horen bij elkaar:

- **Regels** (de classifier): wat er gezocht wordt en wat ermee gebeurt. Twee
  doelen — `opdracht` houdt commando's tegen vóór uitvoeren, `uitvoer` maskeert
  of blokkeert wat terugkomt.
- **Testen**: plak tekst, zie wat een regel zou doen. Dit staat er omdat een
  reguliere expressie die alles of niets pakt anders pas opvalt als de guard
  al een week het verkeerde doet.
- **Audit**: wat er uit de container kwam én wat het model daarvan kreeg. De
  teksten zitten achter een aparte aanroep per regel, zodat klantgegevens niet
  in een lijstweergave meekomen die je toevallig openhebt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from models.guard_rule import ACTIES, DOELEN, GuardRule

router = APIRouter(prefix="/guard", tags=["guard"], dependencies=[Depends(require_user)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(r: GuardRule) -> Dict[str, Any]:
    return {
        "id": r.id, "key": r.key, "target": r.target, "name": r.name,
        "description": r.description, "kind": r.kind, "pattern": r.pattern,
        "category": r.category, "action": r.action, "enabled": r.enabled,
        "sort_order": r.sort_order,
        # Een ingebouwde regel kun je aanpassen maar niet verwijderen: hij komt
        # bij de volgende start gewoon terug, en dat is verwarrender dan hem
        # uitzetten.
        "builtin": bool(r.key),
    }


@router.get("/rules")
def list_rules(db: Session = Depends(get_db)):
    rows = (db.query(GuardRule)
            .order_by(GuardRule.target, GuardRule.sort_order, GuardRule.id).all())
    return [_dict(r) for r in rows]


@router.get("/detectors")
def list_detectors():
    """De ingebouwde detectors, met uitleg. Dit is de lijst waar je uit kiest
    als je geen eigen patroon wilt schrijven — en dat is voor de meeste
    gevallen de betere keuze, want ze controleren een checksum."""
    return [
        {"key": "bsn", "label": "Burgerservicenummer",
         "uitleg": "Negen cijfers die de elfproef doorstaan. Een ordernummer van "
                   "negen cijfers valt er dus niet onder."},
        {"key": "iban", "label": "IBAN-rekeningnummer",
         "uitleg": "Gecontroleerd met de modulo-97-toets."},
        {"key": "creditcard", "label": "Creditcardnummer",
         "uitleg": "Gecontroleerd met de Luhn-formule."},
        {"key": "email", "label": "E-mailadres",
         "uitleg": "Let op: ook adressen van collega's en systemen."},
        {"key": "telefoon_nl", "label": "Nederlands telefoonnummer",
         "uitleg": "Mobiel en vast, met of zonder landcode."},
        {"key": "dataset", "label": "Uitvoer is een dataset",
         "uitleg": "Tien of meer recordachtige regels. Zoekt naar de VORM van "
                   "klantgegevens in plaats van naar een identificatie — dit vangt "
                   "een dump waar geen enkel BSN in staat."},
    ]


@router.post("/rules")
def create_rule(payload: Dict[str, Any], db: Session = Depends(get_db)):
    doel = str(payload.get("target") or "uitvoer")
    actie = str(payload.get("action") or "maskeren")
    if doel not in DOELEN:
        raise HTTPException(400, f"Onbekend doel '{doel}' — kies uit: {', '.join(DOELEN)}")
    if actie not in ACTIES:
        raise HTTPException(400, f"Onbekende actie '{actie}' — kies uit: {', '.join(ACTIES)}")
    if doel == "opdracht" and actie == "maskeren":
        raise HTTPException(400, "Een commando kun je niet half uitvoeren. Kies "
                                 "'blokkeren', 'waarschuwen' of 'toelaten'.")
    _controleer_patroon(payload)
    now = _now_iso()
    r = GuardRule(
        target=doel, name=str(payload.get("name") or "Naamloze regel")[:255],
        description=payload.get("description"),
        kind=str(payload.get("kind") or "regex"),
        pattern=payload.get("pattern"), key=payload.get("detector") or None,
        category=str(payload.get("category") or "klantgegevens")[:64],
        action=actie, enabled=bool(payload.get("enabled", True)),
        sort_order=int(payload.get("sort_order") or 100),
        created_at=now, updated_at=now)
    db.add(r)
    db.commit()
    db.refresh(r)
    return _dict(r)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, payload: Dict[str, Any], db: Session = Depends(get_db)):
    r = db.get(GuardRule, rule_id)
    if r is None:
        raise HTTPException(404, "Regel niet gevonden")
    if "action" in payload:
        if payload["action"] not in ACTIES:
            raise HTTPException(400, f"Onbekende actie '{payload['action']}'")
        if r.target == "opdracht" and payload["action"] == "maskeren":
            raise HTTPException(400, "Een commando kun je niet half uitvoeren.")
        r.action = payload["action"]
    if "pattern" in payload or "kind" in payload:
        _controleer_patroon({**_dict(r), **payload})
    for veld in ("name", "description", "pattern", "category"):
        if veld in payload:
            setattr(r, veld, payload[veld])
    if "enabled" in payload:
        r.enabled = bool(payload["enabled"])
    if "sort_order" in payload:
        r.sort_order = int(payload["sort_order"] or 100)
    r.updated_at = _now_iso()
    db.commit()
    db.refresh(r)
    return _dict(r)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(GuardRule, rule_id)
    if r is None:
        raise HTTPException(404, "Regel niet gevonden")
    if r.key:
        raise HTTPException(
            400, "Dit is een meegeleverde regel; die komt bij de volgende start terug. "
                 "Zet hem uit in plaats van hem te verwijderen.")
    db.delete(r)
    db.commit()
    return {"ok": True}


def _controleer_patroon(payload: Dict[str, Any]) -> None:
    import re

    if str(payload.get("kind") or "regex") != "regex":
        return
    patroon = payload.get("pattern")
    if not patroon:
        raise HTTPException(400, "Geef een patroon op, of kies een ingebouwde detector.")
    try:
        re.compile(patroon)
    except re.error as exc:
        raise HTTPException(400, f"Dit patroon klopt niet: {exc}")


@router.post("/test")
def test_rule(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Wat zou dit doen met deze tekst? Geeft de treffers, de gemaskeerde
    versie, en een waarschuwing bij de twee manieren waarop een zelfgemaakte
    regel meestal fout is: hij pakt niets, of hij pakt alles."""
    from services.lab.classifier import test_regel

    return test_regel(kind=str(payload.get("kind") or "regex"),
                      pattern=payload.get("pattern"),
                      key=payload.get("detector"),
                      voorbeeld=str(payload.get("sample") or ""))


@router.post("/probeer")
def probeer_alles(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Alle ACTIEVE regels los op een voorbeeld — zoals de guard het echt zou
    doen. Hiermee zie je vóór het aanzetten wat er met jouw soort uitvoer
    gebeurt."""
    from services.lab.classifier import beoordeel_opdracht, beoordeel_uitvoer

    doel = str(payload.get("target") or "uitvoer")
    voorbeeld = str(payload.get("sample") or "")
    if doel == "opdracht":
        return beoordeel_opdracht(db, voorbeeld)
    uit = beoordeel_uitvoer(db, voorbeeld)
    return {"actie": uit["actie"], "findings": uit["findings"],
            "resultaat": uit.get("tekst"), "reden": uit.get("reden")}


@router.get("/status")
async def status(db: Session = Depends(get_db)):
    """Staat alles aan wat aan hoort te staan? Dit scherm bestaat omdat de
    lokale tweede mening maandenlang stil kapot was zonder dat iemand het kon
    zien."""
    from services.lab.classifier import presidio_status
    from services.lab.data_guard_llm import guard_model_status

    rules = db.query(GuardRule).all()
    return {
        "regels": {
            "opdracht": sum(1 for r in rules if r.target == "opdracht" and r.enabled),
            "uitvoer": sum(1 for r in rules if r.target == "uitvoer" and r.enabled),
            "uit": sum(1 for r in rules if not r.enabled),
        },
        "presidio": presidio_status(),
        "lokaal_model": await guard_model_status(),
    }


@router.get("/audit")
def audit_lijst(lab_id: Optional[str] = None, outcome: Optional[str] = None,
                limit: int = 50, db: Session = Depends(get_db)):
    from services.lab.guard_audit_service import lijst

    return lijst(db, lab_id=lab_id, outcome=outcome, limit=limit)


@router.get("/audit/{audit_id}")
def audit_detail(audit_id: int, db: Session = Depends(get_db)):
    """Het origineel én wat het model kreeg, naast elkaar. Dit is waar je de
    vraag beantwoordt: klopte het dat dit is tegengehouden, en is er niets
    doorgelaten dat er niet doorheen had gemogen?"""
    from services.lab.guard_audit_service import detail

    uit = detail(db, audit_id)
    if uit is None:
        raise HTTPException(404, "Auditregel niet gevonden")
    return uit
