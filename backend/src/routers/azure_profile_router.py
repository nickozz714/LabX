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


@router.post("/{profile_id}/refresh")
async def refresh_profile(profile_id: int, apply: bool = True, db: Session = Depends(get_db)):
    """Het refresh token van dit profiel inwisselen voor een vers paar. Nodig
    omdat een profiel dat alleen in de kluis ligt juist verloopt: refresh tokens
    verlopen op stilte, niet op gebruik.

    Het resultaat gaat standaard meteen door naar de host en de labs die dit
    profiel gebruiken: na een vernieuwing hébben die per definitie een oude
    sessie, dus vernieuwen zonder doorzetten is half werk. `apply=false` voor wie
    de stappen los wil zetten."""
    svc = _svc(db)
    result = await svc.refresh_tokens(profile_id)
    if apply:
        result["apply"] = await svc.apply_everywhere(profile_id)
    return result


@router.post("/{profile_id}/apply")
async def apply_profile(profile_id: int, db: Session = Depends(get_db)):
    """De sessie doorzetten naar alles wat dit profiel gebruikt: verifiëren, naar
    de host, en naar elk lab dat eraan hangt."""
    return await _svc(db).apply_everywhere(profile_id)


@router.post("/{profile_id}/recapture-host", response_model=AzureProfileRead)
def recapture_host_profile(profile_id: int, db: Session = Depends(get_db)):
    """De az-bestanden van dit profiel opnieuw van de host halen — na een verse
    'az login'. Alleen het secret wordt vervangen, de rest blijft."""
    svc = _svc(db)
    return svc.to_dict(svc.recapture_from_host(profile_id))


@router.post("/{profile_id}/verify")
async def verify_profile(profile_id: int, db: Session = Depends(get_db)):
    return {"ok": True, "identity": await _svc(db).verify(profile_id)}


@router.post("/{profile_id}/sync")
async def sync_profile(profile_id: int, body: AzureProfileSyncRequest, db: Session = Depends(get_db)):
    return await _svc(db).sync(profile_id, target=body.target, lab_id=body.lab_id, az_dir=body.az_dir)
