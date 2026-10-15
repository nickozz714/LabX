"""routers/lab_host_router.py — computers in je netwerk die labs draaien.

Twee soorten verkeer, met met opzet verschillende deuren:

- `/aanmelden` komt van een AGENT op een pc in je netwerk. Die heeft geen
  LabX-account en kan er ook geen krijgen — hij meldt alleen dat hij bestaat.
  Daarom staat dit endpoint open, en daarom levert het niets op: een
  aanmelding zet een rij op `nieuw`, en `nieuw` krijgt geen labs, geen
  toegang, niets.

- De rest is gewoon beheer en zit achter een login. Koppelen kan alleen een
  mens, met de code die op het scherm van díé machine staat.

Dat onderscheid is het hele beveiligingsmodel: wat zich meldt mag zichzelf
niet toelaten.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab.hosts import LabHostService

#: Open: alleen aanmelden.
open_router = APIRouter(prefix="/lab-hosts", tags=["lab-hosts"])
#: Achter een login: alles wat iets verandert.
router = APIRouter(prefix="/lab-hosts", tags=["lab-hosts"],
                   dependencies=[Depends(require_user)])


@open_router.post("/aanmelden")
def aanmelden(body: Dict[str, Any], request: Request, db: Session = Depends(get_db)):
    """Een pc meldt dat hij er is, en blijft dat melden.

    Dezelfde ingang voor de eerste keer en voor elke hartslag daarna: de agent
    weet niet of LabX hem al kent, en hoeft dat ook niet te weten.
    """
    svc = LabHostService(db)
    # Het adres waarop wij hem zien is betrouwbaarder dan wat hij zelf zegt;
    # achter NAT klopt zijn eigen idee van zijn IP vaak niet.
    gezien_vanaf = request.client.host if request.client else ""
    poort = int(body.get("agent_poort") or 8098)
    agent_url = str(body.get("agent_url") or "").strip() or (
        f"http://{gezien_vanaf}:{poort}" if gezien_vanaf else "")
    return svc.aanmelden(
        machine_id=str(body.get("machine_id") or ""),
        naam=str(body.get("naam") or ""),
        agent_url=agent_url,
        docker_host=body.get("docker_host"),
        capaciteit=body.get("capaciteit") or {})


@router.get("")
def lijst(db: Session = Depends(get_db)):
    svc = LabHostService(db)
    return [svc.to_dict(h) for h in svc.lijst()]


@router.get("/{host_id}")
def lees(host_id: int, db: Session = Depends(get_db)):
    svc = LabHostService(db)
    return svc.to_dict(svc.get_or_404(host_id))


@router.post("/{host_id}/koppelen")
def koppelen(host_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Toelaten, met de code die op het scherm van die machine staat."""
    svc = LabHostService(db)
    rij = svc.koppelen(host_id, code=str(body.get("code") or ""),
                       docker_host=str(body.get("docker_host") or ""))
    return svc.to_dict(rij)


@router.post("/{host_id}/weigeren")
def weigeren(host_id: int, db: Session = Depends(get_db)):
    svc = LabHostService(db)
    return svc.to_dict(svc.weigeren(host_id))


@router.patch("/{host_id}")
def wijzig(host_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = LabHostService(db)
    return svc.to_dict(svc.wijzig(host_id, **body))


@router.delete("/{host_id}")
def verwijder(host_id: int, db: Session = Depends(get_db)):
    LabHostService(db).verwijder(host_id)
    return {"ok": True}


@router.post("/{host_id}/test")
async def test(host_id: int, db: Session = Depends(get_db)):
    """Kan LabX werkelijk bij de Docker van die machine?

    Een gekoppelde host waar je niet bij kunt, is erger dan een ongekoppelde:
    je zet er labs op en die starten niet. Dus meten in plaats van aannemen.
    """
    from services.lab.docker_runtime import DockerRuntime

    svc = LabHostService(db)
    host = svc.get_or_404(host_id)
    if not host.docker_host:
        return {"ok": False, "detail": "Deze host heeft geen docker-adres."}
    rt = DockerRuntime(docker_host=host.docker_host)
    uit = await rt.diagnose()
    return {"ok": bool(uit.get("daemon_up")), "docker_host": host.docker_host,
            "diagnose": uit}
