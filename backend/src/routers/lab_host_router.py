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


@router.get("/sleutel")
async def sleutel():
    """De publieke SSH-sleutel van deze LabX.

    Dit is het enige dat je op zo'n machine moet zetten, en daarom staat hij
    in het koppelvenster met een kopieerknop: er is geen manier om een sleutel
    op een vreemde computer te krijgen zonder die computer één keer aan te
    raken, dus dan maken we die ene keer zo kort mogelijk.
    """
    from services.lab.ssh_sleutel import publieke_sleutel

    sleutel = await publieke_sleutel()
    return {
        "publieke_sleutel": sleutel,
        # Kant-en-klaar om te plakken in een terminal op die machine.
        "commando": ("mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo "
                     f"'{sleutel}' >> ~/.ssh/authorized_keys && "
                     "chmod 600 ~/.ssh/authorized_keys"),
    }


@router.get("")
def lijst(db: Session = Depends(get_db)):
    svc = LabHostService(db)
    return [svc.to_dict(h) for h in svc.lijst()]


@router.get("/{host_id}")
def lees(host_id: int, db: Session = Depends(get_db)):
    svc = LabHostService(db)
    return svc.to_dict(svc.get_or_404(host_id))


@router.post("/{host_id}/koppelen")
async def koppelen(host_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Toelaten, met de code die op het scherm van die machine staat."""
    from services.lab.ssh_sleutel import vertrouw_host

    svc = LabHostService(db)
    rij = svc.koppelen(host_id, code=str(body.get("code") or ""),
                       docker_host=str(body.get("docker_host") or ""))
    # Nu, en alleen nu: er staat een mens bij die net de code van dat scherm
    # heeft overgetypt. Dat is het eerlijke moment om de hostsleutel van die
    # machine te leren kennen -- lukt het niet (machine uit), dan is dat geen
    # reden om het koppelen af te breken; de testknop vertelt het je.
    if rij.docker_host:
        await vertrouw_host(rij.docker_host)
    return svc.to_dict(rij)


@router.post("/{host_id}/weigeren")
async def weigeren(host_id: int, db: Session = Depends(get_db)):
    from services.lab.poort_tunnel import tunnels

    svc = LabHostService(db)
    adres = svc.get_or_404(host_id).docker_host
    uit = svc.to_dict(svc.weigeren(host_id))
    # Geen toegang meer betekent ook: geen openstaande tunnel meer. Zou die
    # blijven staan, dan praat LabX nog met een machine die het niet meer mag.
    if adres:
        await tunnels.sluit_machine(adres)
    return uit


@router.patch("/{host_id}")
def wijzig(host_id: int, body: Dict[str, Any], db: Session = Depends(get_db)):
    svc = LabHostService(db)
    return svc.to_dict(svc.wijzig(host_id, **body))


@router.delete("/{host_id}")
async def verwijder(host_id: int, db: Session = Depends(get_db)):
    from services.lab.poort_tunnel import tunnels

    svc = LabHostService(db)
    adres = svc.get_or_404(host_id).docker_host
    svc.verwijder(host_id)
    if adres:
        await tunnels.sluit_machine(adres)
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
    # Twee vragen, en met opzet apart: "ik kan niet inloggen" los je op met de
    # publieke sleutel, "ik ben binnen maar er draait geen Docker" niet. Eén
    # samengevoegde melding stuurt je naar het verkeerde probleem.
    from services.lab.ssh_sleutel import toegang_test

    ssh_ok, ssh_melding = await toegang_test(host.docker_host)
    rt = DockerRuntime(docker_host=host.docker_host)
    uit = await rt.diagnose()
    ok = bool(uit.get("daemon_up"))
    detail = None
    if not ok:
        detail = (uit.get("hint") if ssh_ok
                  else f"Inloggen op die machine lukt niet: {ssh_melding} "
                       f"Staat de publieke sleutel van LabX in ~/.ssh/authorized_keys "
                       f"op die computer, en staat Remote Login aan?")
    return {"ok": ok, "docker_host": host.docker_host,
            "ssh_ok": ssh_ok, "ssh_melding": ssh_melding,
            "detail": detail, "diagnose": uit}
