"""routers/settings_router.py — the Settings page's backend: Claude Code CLI
config (cli_path, model, max_turns, extra_args, tool-search) and guard/lab
defaults. The oauth token is write-only, same pattern as an Azure profile
secret: the GET response only says whether one is configured."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_user)])


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    return settings_service.get_public_settings(db)


@router.put("")
def update_settings(payload: Dict[str, Any], db: Session = Depends(get_db)):
    return settings_service.update_settings(db, payload)
