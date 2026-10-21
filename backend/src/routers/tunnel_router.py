"""routers/tunnel_router.py — de twee dingen die de tunnel-helper mag.

Twee routers, en het verschil is het hele beveiligingsmodel:

- `open_router` staat open voor wie de TUNNELSLEUTEL heeft. Die sleutel staat
  op jouw machine, in een script dat je zelf hebt opgehaald. Hij mag precies
  twee dingen: de branch-labs opsommen en een doorgang laten openen -- en dat
  tweede is wat de knop "Openen" in het scherm ook doet.
- `router` zit achter je gewone aanmelding, want daar wordt de sleutel
  uitgedeeld. Wie de helper kan downloaden, is al ingelogd.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab import tunnel_helper

open_router = APIRouter(prefix="/tunnel", tags=["tunnel"])
router = APIRouter(prefix="/tunnel", tags=["tunnel"],
                   dependencies=[Depends(require_user)])


def _sleutel(db: Session, meegegeven: str | None) -> None:
    tunnel_helper.controleer(db, (meegegeven or "").strip())


@open_router.get("/labs")
def lijst(x_labx_tunnelsleutel: str | None = Header(default=None),
          db: Session = Depends(get_db)):
    """Welke branch-labs er zijn, zodat de helper je kunt laten kiezen."""
    _sleutel(db, x_labx_tunnelsleutel)
    return tunnel_helper.labs(db)


@open_router.post("/adres")
def adres(body: Dict[str, Any],
          x_labx_tunnelsleutel: str | None = Header(default=None),
          db: Session = Depends(get_db)):
    """De adressen waar dit lab nu te bereiken is, doorgang en al.

    ALLE poorten van het lab, elk met het nummer dat hij op jouw machine
    hoort te krijgen: dat is de containerpoort, want daar verwacht de
    applicatie zichzelf. Opvragen opent de doorgang -- en dat is precies
    waarom de helper dit bij elke keuze opnieuw doet in plaats van een
    nummer te onthouden dat morgen niet meer klopt.
    """
    from models.lab import Lab
    from services.lab.lab_adres import adres_voor_lab

    _sleutel(db, x_labx_tunnelsleutel)
    lab_id = str(body.get("lab_id") or "").strip()
    p = db.get(Lab, lab_id) if lab_id else None
    if p is None or not p.cluster_id:
        raise HTTPException(status_code=404, detail="Dit is geen branch-lab van een cluster.")
    kaart = {int(k): int(v) for k, v in (p.poort_map or {})
             .items()}
    if not kaart:
        raise HTTPException(status_code=400,
                            detail="Dit lab publiceert geen poorten; start het eerst.")

    from urllib.parse import urlparse

    poorten = []
    for container_poort in kaart:
        uit = adres_voor_lab(db, lab_id, container_poort)
        doel = urlparse(uit["url"])
        poorten.append({"lokaal": container_poort,
                        "host": doel.hostname,
                        "poort": int(doel.port or 80)})
    return {"lab": p.name, "via": uit.get("via", ""), "poorten": poorten}


@router.get("/helper")
def helper(request: Request, db: Session = Depends(get_db)):
    """De helper zelf, met jouw server en sleutel erin.

    Eén keer ophalen is genoeg: hij vraagt LabX bij elke start opnieuw welke
    labs er zijn en welke doorgang erbij hoort. Daarom staat er geen enkel
    poortnummer in het bestand.
    """
    from services.lab.tunnel import IMAGE
    from services.lab.tunnel_helper_script import script

    # Het adres waarop JIJ dit ophaalt, is het adres waarop de helper LabX
    # moet vinden. Zelf iets verzinnen gaat mis: de backend ziet alleen zijn
    # eigen containernaam, en een instelling die je moet invullen is een
    # instelling die niet klopt zodra je server verhuist.
    gastheer = (request.headers.get("x-forwarded-host")
                or request.headers.get("host") or "").strip()
    schema = (request.headers.get("x-forwarded-proto") or request.url.scheme
              or "https").strip()
    if not gastheer:
        raise HTTPException(
            status_code=400,
            detail="LabX kan zijn eigen adres niet afleiden uit dit verzoek.")
    inhoud = script(labx_url=f"{schema}://{gastheer}",
                    sleutel=tunnel_helper.sleutel(db, maak_aan=True),
                    image=IMAGE)
    return PlainTextResponse(
        inhoud,
        headers={"Content-Disposition":
                 'attachment; filename="labx-tunnel-helper.command"'})
