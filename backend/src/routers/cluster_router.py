"""routers/cluster_router.py — development clusters.

Een cluster is een groep containers die bij elkaar horen: vaste diensten die
blijven staan, en branch-labs die komen en gaan. Zie docs/CLUSTERS.md.

De endpoints zijn bewust dun: alles wat een beslissing is, staat in
ClusterService. Dat scheelt een tweede plek waar de regels wonen.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab.clusters import ClusterService

router = APIRouter(prefix="/clusters", tags=["clusters"],
                   dependencies=[Depends(require_user)])


def _svc(db: Session) -> ClusterService:
    return ClusterService(db)


@router.get("")
def lijst(db: Session = Depends(get_db)):
    svc = _svc(db)
    return [svc.to_dict(c) for c in svc.lijst()]


@router.post("")
def maak(body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    c = svc.maak(naam=str(body.get("naam") or ""),
                 host_id=(int(body["host_id"]) if body.get("host_id") else None),
                 **{k: v for k, v in body.items() if k not in ("naam", "host_id")})
    return svc.to_dict(c)


@router.get("/{cluster_id}")
def lees(cluster_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.to_dict(svc.get_or_404(cluster_id))


@router.patch("/{cluster_id}")
def wijzig(cluster_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.to_dict(svc.wijzig(cluster_id, **body))


@router.delete("/{cluster_id}")
def verwijder(cluster_id: int, db: Session = Depends(get_db)):
    _svc(db).verwijder(cluster_id)
    return {"ok": True}


# ── vaste diensten ───────────────────────────────────────────────────────────

@router.post("/{cluster_id}/diensten")
def voeg_dienst_toe(cluster_id: int, body: Dict[str, Any],
                    db: Session = Depends(get_db)):
    svc = _svc(db)
    d = svc.voeg_dienst_toe(cluster_id, naam=str(body.get("naam") or ""),
                            image=str(body.get("image") or ""),
                            poorten=body.get("poorten"),
                            omgeving=body.get("omgeving"))
    return svc.dienst_dict(d)


@router.post("/diensten/{dienst_id}/start")
async def start_dienst(dienst_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.dienst_dict(await svc.start_dienst(dienst_id))


@router.post("/diensten/{dienst_id}/stop")
async def stop_dienst(dienst_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.dienst_dict(await svc.stop_dienst(dienst_id))


@router.delete("/diensten/{dienst_id}")
async def verwijder_dienst(dienst_id: int, db: Session = Depends(get_db)):
    await _svc(db).verwijder_dienst(dienst_id)
    return {"ok": True}
