"""routers/cluster_router.py — development clusters.

Een cluster is een groep containers die bij elkaar horen: vaste diensten die
blijven staan, en branch-labs die komen en gaan. Zie docs/CLUSTERS.md.

De endpoints zijn bewust dun: alles wat een beslissing is, staat in
ClusterService. Dat scheelt een tweede plek waar de regels wonen.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
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
async def verwijder(cluster_id: int, db: Session = Depends(get_db)):
    return await _svc(db).verwijder(cluster_id)


# ── branch-labs ──────────────────────────────────────────────────────────────

@router.post("/{cluster_id}/branches")
async def maak_branch_lab(cluster_id: int, body: Dict[str, Any],
                          db: Session = Depends(get_db)):
    """Een lab voor één branch, afgetakt van een basis naar keuze.

    `basis` is niet standaard "main" omdat dat vaak klopt maar niet altijd:
    van `release/2026.09` aftakken is net zo gewoon, en dat achteraf
    rechtzetten in een lab is omslachtiger dan het meteen goed doen.
    """
    return await _svc(db).maak_branch_lab(
        cluster_id,
        branch=str(body.get("branch") or ""),
        basis=str(body.get("basis") or "main"),
        naam=(str(body["naam"]) if body.get("naam") else None))


@router.post("/{cluster_id}/toegang")
async def controleer_toegang(cluster_id: int, db: Session = Depends(get_db)):
    """Kan dit cluster bij zijn repo, en mag het pushen?

    Meten in plaats van het bij de eerste kloon te ontdekken: een git-fout
    zegt "403" waar drie verschillende oorzaken achter kunnen zitten.
    """
    svc = _svc(db)
    return await svc.controleer_toegang(svc.get_or_404(cluster_id))


@router.post("/branches/{lab_id}/start")
async def start_branch_lab(lab_id: str, db: Session = Depends(get_db)):
    """Een branch-lab aanzetten op het netwerk van zijn cluster."""
    return await _svc(db).start_branch_lab(lab_id)


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


# ── verkennen en goedkeuren ──────────────────────────────────────────────────
#
# `/verkennen` maakt NIETS aan. Het kloont ondiep in een wegwerpcontainer,
# leest, en levert een voorstel. Pas `/goedkeuren` zet dat om in een cluster.
# Die scheiding is de hele kern: er draait geen container waar niemand op
# geklikt heeft.


@router.post("/verkennen")
async def verkennen(body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = _svc(db)
    rij = await svc.verken(
        repo_url=str(body.get("repo_url") or ""),
        branch=str(body.get("branch") or "main"),
        token_geheim=(str(body["token_geheim"]) if body.get("token_geheim") else None),
        host_id=(int(body["host_id"]) if body.get("host_id") else None),
        cluster_id=(int(body["cluster_id"]) if body.get("cluster_id") else None))
    return svc.plan_dict(rij)


@router.post("/plannen/{plan_id}/opnieuw")
async def opnieuw(plan_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Terugkoppeling geven en opnieuw verkennen.

    Levert een NIEUW plan op naast het oude. Alle vier de soorten
    terugkoppeling zijn optioneel, maar er moet er minstens een zijn --
    opnieuw verkennen zonder iets te zeggen levert hetzelfde plan op en kost
    wel een modelaanroep.
    """
    def _kaart(sleutel: str) -> Dict[str, str]:
        waarde = body.get(sleutel)
        return {str(k): str(v) for k, v in waarde.items()} if isinstance(waarde, dict) else {}

    keuze = body.get("keuze")
    return _svc(db).plan_dict(await _svc(db).opnieuw(
        plan_id,
        antwoorden=_kaart("antwoorden"),
        opmerkingen=_kaart("opmerkingen"),
        opmerking=(str(body["opmerking"]) if body.get("opmerking") else None),
        keuze=(list(keuze) if isinstance(keuze, list) else None)))


@router.get("/plannen/{plan_id}")
def lees_plan(plan_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    return svc.plan_dict(svc.plan_of_404(plan_id))


@router.get("/{cluster_id}/plannen")
def plannen_van_cluster(cluster_id: int, db: Session = Depends(get_db)):
    svc = _svc(db)
    svc.get_or_404(cluster_id)
    return [svc.plan_dict(p) for p in svc.plannen(cluster_id)]


@router.post("/plannen/{plan_id}/goedkeuren")
def goedkeuren(plan_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Het plan omzetten in een cluster.

    `gekozen` weglaten betekent: neem over wat gestaafd is. Een vondst die de
    detectoren niet konden terugvinden gaat dan NIET mee -- die moet je
    expliciet aanvinken.
    """
    gekozen = body.get("gekozen")
    return _svc(db).keur_goed(
        plan_id,
        naam=(str(body["naam"]) if body.get("naam") else None),
        host_id=(int(body["host_id"]) if body.get("host_id") else None),
        gekozen=(list(gekozen) if isinstance(gekozen, list) else None),
        token_geheim=(str(body["token_geheim"]) if body.get("token_geheim") else None),
        env_profiel=(str(body["env_profiel"]) if body.get("env_profiel") else None))
