"""routers/secret_router.py — de kluis met geheimen die overal gelden.

Waarden gaan er alleen IN. Er is geen endpoint dat een waarde teruggeeft, ook
niet aan een ingelogde gebruiker: wat hier ligt hoort de kluis nooit te
verlaten behalve op weg naar een tool of een commando (zie
services/secrets/vault.py).
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.secrets.vault import VaultService

router = APIRouter(prefix="/secrets", tags=["secrets"], dependencies=[Depends(require_user)])


def _svc(db: Session) -> VaultService:
    return VaultService(db)


@router.get("")
def list_secrets(lab_id: str | None = None, db: Session = Depends(get_db)):
    """Alle geheimen, of alleen die in dit lab gelden."""
    svc = _svc(db)
    rijen = svc.beschikbaar(lab_id) if lab_id else svc.lijst()
    return [svc.to_dict(r) for r in rijen]


@router.put("/{naam}")
def put_secret(naam: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Aanmaken of bijwerken. Een lege waarde laat de bestaande staan — het
    scherm krijgt hem toch nooit terug, dus een leeg veld is de normale
    toestand als je alleen de omschrijving aanpast."""
    svc = _svc(db)
    try:
        rij = svc.zet(naam=naam,
                      waarde=payload.get("value"),
                      omschrijving=payload.get("description"),
                      lab_ids=payload.get("lab_ids"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return svc.to_dict(rij)


@router.delete("/{naam}")
def delete_secret(naam: str, db: Session = Depends(get_db)):
    if not _svc(db).verwijder(naam):
        raise HTTPException(status_code=404, detail=f"Geheim '{naam}' bestaat niet")
    return {"ok": True}


@router.post("/{naam}/test")
def test_secret(naam: str, db: Session = Depends(get_db)):
    """Bestaat hij, en is hij te ontsleutelen? Geeft de LENGTE terug en de
    eerste twee tekens — genoeg om te zien of je de juiste hebt geplakt, te
    weinig om hem te gebruiken."""
    svc = _svc(db)
    rij = svc.haal(naam)
    if rij is None:
        raise HTTPException(status_code=404, detail=f"Geheim '{naam}' bestaat niet")
    waarde = svc.waarde_van(rij)
    if waarde is None:
        return {"ok": False, "melding": "Niet te ontsleutelen — is LABX_FERNET_KEY gewijzigd?"}
    return {"ok": True, "lengte": len(waarde), "begint_met": waarde[:2] + "…"}
