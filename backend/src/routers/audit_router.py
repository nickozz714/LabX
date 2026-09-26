"""routers/audit_router.py — wat er in een lab gebeurd is.

Twee vragen, twee endpoints: de lijst met beurten (wat ging erin, wat kwam
eruit, welke acties) en de aggregatie voor de staafjes (per dag, week of
maand). Beide per lab te filteren, want dat is de vorm waarin je deze vraag
stelt: niet "wat deed LabX" maar "wat deden we bij deze klant".

Het guard-spoor blijft waar het was (/guard/audit): dat gaat over maskeren en
blokkeren, en dat is een andere vraag dan deze.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.audit.activiteit import AuditService

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_user)])


@router.get("/activiteit")
def activiteit(lab_id: Optional[str] = None, van: Optional[str] = None,
               tot: Optional[str] = None, bron: Optional[str] = None,
               limit: int = 50, offset: int = 0,
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    return AuditService(db).gebeurtenissen(lab_id=lab_id, van=van, tot=tot, bron=bron,
                                           limit=limit, offset=offset)


@router.get("/aggregatie")
def aggregatie(lab_id: Optional[str] = None, periode: str = "dag", aantal: int = 14,
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    return AuditService(db).aggregatie(lab_id=lab_id, periode=periode, aantal=aantal)
