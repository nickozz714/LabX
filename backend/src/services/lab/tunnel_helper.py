"""services/lab/tunnel_helper.py — één helper op je machine, voor alle labs.

Het tunnelscript was per lab: je downloadde een bestand, maakte het
uitvoerbaar, en startte het. Dat is drie keer wrijving voor iets wat je
dagelijks doet, en het bestand is bovendien een leugen op termijn -- het
wijst naar een doorgang die bij een herstart van de agent verdwijnt.

Daarom één helper die blijft staan. Hij vraagt LabX welke branch-labs er
zijn, jij kiest er een, en hij regelt de rest: de doorgang laten openen en
het doorgeefluik op je eigen machine starten. Wisselen van lab is dan een
keuze in een menu in plaats van een nieuwe download.

Dat vraagt om een sleutel op je machine, en dus om een eerlijke grens. Deze
sleutel mag precies twee dingen: de branch-labs opsommen en een doorgang
laten openen. Geen labs starten, geen bestanden lezen, geen kluis. Wie hem
in handen krijgt, kan zien welke branches er draaien en een labpoort op het
netwerk van DIE machine zetten -- hetzelfde wat de knop "Openen" in het
scherm doet.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

#: Waar de helper zijn sleutel en zijn keuzes bewaart.
BESTAND = "~/.labx-tunnel"


def sleutel(db: Session, *, maak_aan: bool = False) -> str:
    """De sleutel van de tunnel-helper; maakt hem aan bij de eerste download."""
    from services.settings_service import _row

    # Via de instellingendienst, niet zelf een rij maken: die zet ook
    # `updated_at`, en dat is NOT NULL.
    rij = _row(db)
    if not rij.tunnel_sleutel:
        if not maak_aan:
            return ""
        rij.tunnel_sleutel = secrets.token_urlsafe(32)
        db.commit()
    return rij.tunnel_sleutel or ""


def controleer(db: Session, aangeboden: str) -> None:
    """Hoort deze sleutel bij deze LabX? Zo niet: 401, en verder niets."""
    import hmac

    echt = sleutel(db)
    if not echt or not aangeboden or not hmac.compare_digest(echt, aangeboden):
        raise HTTPException(status_code=401, detail="Ongeldige tunnelsleutel")


def labs(db: Session) -> List[Dict[str, Any]]:
    """De branch-labs waar een tunnel naartoe kan, met hun poorten.

    Alleen wat de helper nodig heeft om je een keuze te laten maken: hoe het
    lab heet, welke branch erin staat, of hij draait, en welke poorten hij
    publiceert. Geen paden, geen geheimen, geen instellingen.
    """
    from models.cluster import Cluster
    from models.lab import Lab

    uit: List[Dict[str, Any]] = []
    for p in db.query(Lab).filter(Lab.cluster_id.isnot(None)).all():
        c = db.get(Cluster, p.cluster_id)
        uit.append({
            "id": p.id,
            "naam": p.name,
            "branch": p.branch or "",
            "cluster": c.naam if c else "",
            "draait": p.status == "running",
            "poorten": sorted(int(k) for k in (p.poort_map or {})),
        })
    return sorted(uit, key=lambda r: (not r["draait"], r["naam"]))
