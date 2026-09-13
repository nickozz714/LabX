"""routers/azure_profile_router.py — named Azure profiles: multiple encrypted
identities synced to the LabX host or into a lab. Secrets are write-only."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
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


@router.post("/capture-lab", response_model=AzureProfileRead)
async def capture_lab_profile(body: Dict[str, Any], db: Session = Depends(get_db)):
    """Een az-sessie die IN een lab is ontstaan vastleggen als profiel — de
    andere kant van de sync. Zo blijft een interactieve login niet in dat ene
    lab hangen."""
    svc = _svc(db)
    lab_id = str((body or {}).get("lab_id") or "").strip()
    if not lab_id:
        raise HTTPException(status_code=400, detail="lab_id is verplicht")
    row = await svc.capture_from_lab(lab_id=lab_id,
                                     name=str((body or {}).get("name") or "Lab az-login"),
                                     description=(body or {}).get("description"))
    return svc.to_dict(row)


@router.post("/{profile_id}/recapture-lab", response_model=AzureProfileRead)
async def recapture_lab_profile(profile_id: int, body: Dict[str, Any],
                                db: Session = Depends(get_db)):
    """De bestanden van dit profiel opnieuw uit een lab halen — na een verse
    login daarbinnen."""
    svc = _svc(db)
    lab_id = str((body or {}).get("lab_id") or "").strip()
    if not lab_id:
        raise HTTPException(status_code=400, detail="lab_id is verplicht")
    return svc.to_dict(await svc.recapture_from_lab(profile_id, lab_id=lab_id))


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


# ── device-code-login voor een eigen Entra-app ─────────────────────────────
#
# Twee stappen, want zo werkt de flow: eerst een code die de gebruiker ergens
# invoert, dan wachten tot hij klaar is. Het pollen doet de BROWSER en niet de
# server: een verzoek dat vijftien minuten openblijft is een verzoek dat elke
# proxy onderweg afkapt, en dan lijkt een geslaagde inlog mislukt.

@router.post("/{profile_id}/device-login")
async def device_login_start(profile_id: int, payload: Optional[Dict[str, Any]] = None,
                             db: Session = Depends(get_db)):
    return await _svc(db).device_login_start(
        profile_id, (payload or {}).get("scopes"))


@router.post("/{profile_id}/device-login/poll")
async def device_login_poll(profile_id: int, payload: Dict[str, Any],
                            db: Session = Depends(get_db)):
    code = str((payload or {}).get("device_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="device_code ontbreekt")
    return await _svc(db).device_login_poll(profile_id, code)


@router.post("/{profile_id}/token-test")
async def token_test(profile_id: int, payload: Dict[str, Any],
                     db: Session = Depends(get_db)):
    """Kan dit profiel een token halen voor deze scope?

    Dit bestaat omdat elke fout hier een ANDERE oorzaak heeft die de gebruiker
    zelf moet oplossen: geen toestemming gegeven, beheerder heeft nog niet
    ingestemd, verkeerde app-id, API niet aangezet in de tenant. De melding van
    Entra zelf (`AADSTS…`) is daarin het enige bruikbare houvast, dus die geven
    we onverkort door in plaats van er "inloggen mislukt" van te maken.
    """
    scope = str((payload or {}).get("scope") or "").strip()
    if not scope:
        raise HTTPException(status_code=400, detail="scope ontbreekt")
    try:
        token = await _svc(db).token_for(profile_id, scope)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:500]}
    import base64
    import json as _json
    claims = {}
    try:
        deel = token.split(".")[1]
        deel += "=" * (-len(deel) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(deel))
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "audience": claims.get("aud"),
            "scopes": (claims.get("scp") or "").split(),
            "upn": claims.get("upn") or claims.get("preferred_username")}
