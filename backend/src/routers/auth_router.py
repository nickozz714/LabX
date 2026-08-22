"""routers/auth_router.py — single-admin login with first-time setup.

The account lives in the database once the user creates it via the setup
screen (POST /auth/setup, only available while no account exists). The
LABX_ADMIN_USERNAME/PASSWORD env vars remain a bootstrap/fallback so an
existing deployment keeps working unchanged — but DB credentials, once set,
always win.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from authentication import create_access_token, hash_password, require_user, verify_password
from config import settings
from db.database import get_db
from models.setting import AppSettings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str
    password: str


class SetupPayload(BaseModel):
    username: str
    password: str


class CredentialsPayload(BaseModel):
    current_password: str
    username: str
    password: str


def _settings_row(db: Session) -> AppSettings:
    row = db.get(AppSettings, 1)
    if not row:
        from datetime import datetime, timezone
        row = AppSettings(id=1, updated_at=datetime.now(timezone.utc).isoformat())
        db.add(row)
        db.commit()
    return row


def _has_db_account(row: AppSettings) -> bool:
    return bool((row.admin_username or "").strip() and row.admin_password_hash)


def _check_password(row: AppSettings, username: str, password: str) -> bool:
    if _has_db_account(row):
        return (hmac.compare_digest(username, row.admin_username or "")
                and verify_password(password, row.admin_password_hash or ""))
    if settings.ADMIN_PASSWORD:
        return (hmac.compare_digest(username, settings.ADMIN_USERNAME)
                and hmac.compare_digest(password, settings.ADMIN_PASSWORD))
    return False


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """Unauthenticated: does this installation still need first-time account
    setup? True only when there is NO database account AND no env password —
    the login page switches to the 'account aanmaken' form on true."""
    row = _settings_row(db)
    needs_setup = not _has_db_account(row) and not settings.ADMIN_PASSWORD
    return {"needs_setup": needs_setup}


@router.post("/setup")
def setup(payload: SetupPayload, db: Session = Depends(get_db)):
    """Create the admin account — first-time only: refused as soon as any
    account exists (DB or env), so this can never be used to take over a
    configured installation."""
    row = _settings_row(db)
    if _has_db_account(row) or settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=409, detail="Er is al een account geconfigureerd")
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Gebruikersnaam mag niet leeg zijn")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Wachtwoord moet minimaal 8 tekens zijn")
    row.admin_username = username
    row.admin_password_hash = hash_password(payload.password)
    db.commit()
    token = create_access_token(username)
    return {"access_token": token, "token_type": "bearer", "username": username}


@router.post("/login")
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    row = _settings_row(db)
    if not _has_db_account(row) and not settings.ADMIN_PASSWORD:
        raise HTTPException(status_code=409, detail="Nog geen account — maak er eerst een aan")
    if not _check_password(row, payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Ongeldige gebruikersnaam of wachtwoord")
    token = create_access_token(payload.username)
    return {"access_token": token, "token_type": "bearer", "username": payload.username}


@router.put("/credentials", dependencies=[Depends(require_user)])
def update_credentials(payload: CredentialsPayload, db: Session = Depends(get_db)):
    """Change username/password (Settings → Account). Requires the CURRENT
    password — a live session token alone must not be enough to take over
    the account. Also the migration path off an env-configured password."""
    row = _settings_row(db)
    current_user = row.admin_username if _has_db_account(row) else settings.ADMIN_USERNAME
    if not _check_password(row, current_user or "", payload.current_password):
        raise HTTPException(status_code=401, detail="Huidig wachtwoord onjuist")
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Gebruikersnaam mag niet leeg zijn")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Wachtwoord moet minimaal 8 tekens zijn")
    row.admin_username = username
    row.admin_password_hash = hash_password(payload.password)
    db.commit()
    token = create_access_token(username)
    return {"access_token": token, "token_type": "bearer", "username": username}
