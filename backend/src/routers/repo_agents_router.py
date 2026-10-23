"""routers/repo_agents_router.py — de agents die in de repo zelf staan.

Drie ingangen: welke er zijn, wat er in één staat, en hem draaien als gewone
LabX-run in dit lab.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab.repo_agents import RepoAgents

router = APIRouter(prefix="/repo-agents", tags=["repo-agents"],
                   dependencies=[Depends(require_user)])


@router.get("/{lab_id}")
async def lijst(lab_id: str, db: Session = Depends(get_db)):
    return await RepoAgents(db).lijst(lab_id)


@router.get("/{lab_id}/{naam}")
async def lees(lab_id: str, naam: str, db: Session = Depends(get_db)):
    return await RepoAgents(db).lees(lab_id, naam)


@router.post("/{lab_id}/{naam}/start")
async def start(lab_id: str, naam: str, body: Dict[str, Any] | None = None,
                db: Session = Depends(get_db)):
    return await RepoAgents(db).start(
        lab_id, naam=naam, opdracht=str((body or {}).get("opdracht") or ""))
