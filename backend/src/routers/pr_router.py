"""routers/pr_router.py — pull requests van een branch-lab.

Vier ingangen, en de volgorde is de volgorde waarin je ze gebruikt: kijken
wat erin zou komen, hem maken, de reacties lezen, en de reacties die ertoe
doen op het ticket zetten.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab.pull_requests import PullRequests

router = APIRouter(prefix="/pr", tags=["pr"],
                   dependencies=[Depends(require_user)])


@router.get("/{lab_id}/rechten")
def rechten(lab_id: str, db: Session = Depends(get_db)):
    """Wat het token bij deze repo mag -- zonder iets te veranderen."""
    return PullRequests(db).rechten(lab_id)


@router.get("/{lab_id}/voorstel")
def voorstel(lab_id: str, basis: str = "", db: Session = Depends(get_db)):
    """Wat er in de pull request zou komen, zonder hem te maken.

    `basis` is de branch waarvan deze is afgetakt. Leeg = LabX leidt hem af
    uit de open pull request, en valt anders terug op de standaardbranch --
    zichtbaar als aanname, zie basisbranch.py."""
    return PullRequests(db).voorstel(lab_id, basis=basis)


@router.post("/{lab_id}")
def maak(lab_id: str, body: Dict[str, Any] | None = None,
         db: Session = Depends(get_db)):
    b = body or {}
    return PullRequests(db).maak(
        lab_id,
        titel=str(b.get("titel") or ""),
        beschrijving=str(b.get("beschrijving") or ""),
        naar=str(b.get("naar") or ""),
        concept=bool(b.get("concept")))


@router.get("/{lab_id}/comments")
def comments(lab_id: str, db: Session = Depends(get_db)):
    return PullRequests(db).comments(lab_id)


@router.post("/{lab_id}/naar-ticket")
def naar_ticket(lab_id: str, body: Dict[str, Any] | None = None,
                db: Session = Depends(get_db)):
    ids = [str(x) for x in ((body or {}).get("ids") or [])]
    return PullRequests(db).naar_ticket(lab_id, ids=ids)
