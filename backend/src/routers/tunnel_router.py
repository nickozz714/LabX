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

import re

from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from authentication import require_user
from db.database import get_db
from services.lab import tunnel_helper

#: Wat als adres van LabX in het script mag belanden. Streng, want deze
#: tekst komt in een shell-variabele terecht op JOUW machine: alles met een
#: aanhalingsteken, een backtick of een dollar hoort daar niet te komen.
_BASIS = re.compile(r"^https?://[A-Za-z0-9._-]+(:\d{1,5})?$")

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
def helper(request: Request, basis: str | None = None,
           db: Session = Depends(get_db)):
    """De helper zelf, met jouw server en sleutel erin.

    Eén keer ophalen is genoeg: hij vraagt LabX bij elke start opnieuw welke
    labs er zijn en welke doorgang erbij hoort. Daarom staat er geen enkel
    poortnummer in het bestand.
    """
    from services.lab.tunnel import IMAGE
    from services.lab.tunnel_helper_script import script

    # Het adres waarop JIJ dit ophaalt, is het adres waarop de helper LabX
    # moet vinden -- en dat weet alleen je browser. Afleiden uit de headers
    # ging mis: tussen jou en de backend zitten twee proxies, en die lieten
    # de poort uit `Host` vallen. De helper kreeg `http://192.168.2.15`, en
    # daar staat niets: LabX luistert op 8443 en 8080.
    #
    # Daarom stuurt het scherm zijn eigen origin mee. De headers blijven de
    # terugval, voor wie dit buiten de UI om ophaalt.
    adres = (basis or "").strip().rstrip("/")
    if not _BASIS.match(adres):
        gastheer = (request.headers.get("x-forwarded-host")
                    or request.headers.get("host") or "").strip()
        schema = (request.headers.get("x-forwarded-proto") or request.url.scheme
                  or "https").strip()
        adres = f"{schema}://{gastheer}" if gastheer else ""
    if not _BASIS.match(adres):
        raise HTTPException(
            status_code=400,
            detail=("LabX kan zijn eigen adres niet bepalen. Haal de helper op "
                    "via het scherm, dan stuurt je browser het adres mee."))
    inhoud = script(labx_url=adres,
                    sleutel=tunnel_helper.sleutel(db, maak_aan=True),
                    image=IMAGE)
    return PlainTextResponse(
        inhoud,
        headers={"Content-Disposition":
                 'attachment; filename="labx-tunnel-helper.command"'})
