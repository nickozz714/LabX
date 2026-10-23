"""routers/opsplitsen_router.py — een feature-branch in kleinere PR's.

Twee ingangen, met opzet gescheiden: eerst kijken wat de verdeling zou zijn,
en pas daarna maken. Wat bij elkaar hoort is een oordeel, en dat hoor je te
zien voordat er branches ontstaan die iemand moet opruimen.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab.opsplitsen import Opsplitsen

router = APIRouter(prefix="/opsplitsen", tags=["opsplitsen"],
                   dependencies=[Depends(require_user)])


@router.get("/{lab_id}/voorstel")
async def voorstel(lab_id: str, db: Session = Depends(get_db)):
    return await Opsplitsen(db).voorstel(lab_id)


@router.post("/{lab_id}")
async def maak(lab_id: str, body: Dict[str, Any] | None = None,
               db: Session = Depends(get_db)):
    b = body or {}
    return await Opsplitsen(db).maak(lab_id, groepen=list(b.get("groepen") or []))
