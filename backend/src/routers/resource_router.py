"""routers/resource_router.py — claimbare resources beheren en zien wie wat vasthoudt.

De catalogus is globaal (zoals de lab-extra's); per lab vink je aan welke
ervan gelden. Zie services/lab/resources.py voor waarom dit bestaat.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab.resources import ResourceService

router = APIRouter(prefix="/claim-resources", tags=["claim-resources"],
                   dependencies=[Depends(require_user)])


def _svc(db: Session) -> ResourceService:
    return ResourceService(db)


@router.get("")
def list_resources(db: Session = Depends(get_db)):
    svc = _svc(db)
    return [svc.to_dict(r) for r in svc.catalogus()]


@router.post("")
def create_resource(payload: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    try:
        return svc.to_dict(svc.maak(**payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/{key}")
def update_resource(key: str, payload: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    try:
        return svc.to_dict(svc.werk_bij(key, **payload))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{key}")
def delete_resource(key: str, db: Session = Depends(get_db)):
    """Verwijderen raakt alleen de catalogus. Lopende claims blijven staan tot
    ze vervallen — ze stilzwijgend opheffen zou betekenen dat twee agents
    denken dat ze de browser hebben."""
    svc = _svc(db)
    try:
        svc.verwijder(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}
