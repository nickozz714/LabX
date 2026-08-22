"""routers/azure_profile_router.py — named Azure profiles: multiple encrypted
identities synced to the LabX host or into a lab. Secrets are write-only."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from schemas.azure_profile import (
    AzureProfileCreate, AzureProfileRead, AzureProfileSyncRequest, AzureProfileUpdate,
)
from services.azure.azure_profile_service import AzureProfileService

router = APIRouter(prefix="/azure-profiles", tags=["azure-profiles"], dependencies=[Depends(require_user)])


def _svc(db: Session) -> AzureProfileService:
    return AzureProfileService(db)


@router.get("", response_model=list[AzureProfileRead])
def list_profiles(db: Session = Depends(get_db)):
    svc = _svc(db)
    return [svc.to_dict(p) for p in svc.list()]


@router.post("", response_model=AzureProfileRead)
def create_profile(data: AzureProfileCreate, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.to_dict(svc.create(data))


@router.post("/capture-host", response_model=AzureProfileRead)
def capture_host_profile(body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    row = svc.capture_from_host(name=str((body or {}).get("name") or "Host az-login"),
                                description=(body or {}).get("description"))
    return svc.to_dict(row)


@router.put("/{profile_id}", response_model=AzureProfileRead)
def update_profile(profile_id: int, data: AzureProfileUpdate, db: Session = Depends(get_db)):
    return _svc(db).to_dict(_svc(db).update(profile_id, data))


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    _svc(db).delete(profile_id)
    return {"ok": True}


@router.post("/{profile_id}/verify")
async def verify_profile(profile_id: int, db: Session = Depends(get_db)):
    return {"ok": True, "identity": await _svc(db).verify(profile_id)}


@router.post("/{profile_id}/sync")
async def sync_profile(profile_id: int, body: AzureProfileSyncRequest, db: Session = Depends(get_db)):
    return await _svc(db).sync(profile_id, target=body.target, lab_id=body.lab_id, az_dir=body.az_dir)
