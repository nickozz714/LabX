"""services/lab/lab_adres.py — waar JIJ een branch-lab opent.

Dit bestaat omdat het antwoord niet altijd `localhost` is, en de knop dat wel
aannam. Zolang LabX, je browser en het lab op dezelfde machine stonden klopte
dat; verhuis het cluster naar een computer in je netwerk en de knop wijst naar
een poort op je eigen machine waar niets luistert. Dan staat er "applicatie
gestart" en gebeurt er bij het openen niets -- een melding die waar is naast
een knop die stuk lijkt.

Labs publiceren met opzet op de loopback van hun eigen machine: een labpoort
hoort niet ongevraagd op je netwerk. De agent die daar al draait kan hem op
verzoek doorzetten, en dat verzoek doen we hier -- pas als je op Openen drukt,
zodat er niets openstaat wat je niet gebruikt.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

#: Lang genoeg voor een agent die een socket moet openen, kort genoeg om niet
#: achter een knop te blijven hangen die het toch niet gaat doen.
WACHT = 10.0


def _vraag_agent(agent_url: str, token: str, pad: str,
                 body: Dict[str, Any]) -> Dict[str, Any]:
    verzoek = urllib.request.Request(
        agent_url.rstrip("/") + pad,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST")
    with urllib.request.urlopen(verzoek, timeout=WACHT) as r:
        return json.loads(r.read() or b"{}")


#: De echte, zodat een test hem kan terugzetten na hem te hebben vervangen.
_echt_vraag_agent = _vraag_agent


def adres_voor_lab(db: Session, lab_id: str,
                   container_poort: Optional[int] = None) -> Dict[str, Any]:
    """Het adres waarop je dit branch-lab in je browser opent.

    Staat het lab op deze machine, dan is dat gewoon de gepubliceerde poort op
    localhost. Staat het op een gekoppelde machine, dan vragen we de agent
    daar om die poort door te zetten en geven we het adres van die doorgang.
    """
    from models.cluster import Cluster
    from models.lab import Lab
    from models.lab_host import LabHost

    p = db.get(Lab, lab_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Lab niet gevonden")
    kaart = {int(k): int(v) for k, v in (p.poort_map or {}).items()}
    if not kaart:
        raise HTTPException(
            status_code=400,
            detail="Dit lab publiceert geen poorten; start het eerst.")

    gekozen = container_poort if container_poort in kaart else next(iter(kaart))
    machine_poort = kaart[gekozen]

    c = db.get(Cluster, p.cluster_id) if p.cluster_id else None
    host_id = (c.host_id if c else None) or p.host_id
    if not host_id:
        return {"url": f"http://localhost:{machine_poort}", "via": "deze machine"}

    host = db.get(LabHost, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="De machine van dit lab bestaat niet meer.")
    if not host.agent_url or not host.agent_token:
        raise HTTPException(
            status_code=409,
            detail=(f"Dit lab draait op {host.naam}, en daar staat de poort op de "
                    f"loopback van die machine. De agent daar heeft nog geen ingang: "
                    f"werk hem bij en start hem opnieuw, dan kan LabX de poort "
                    f"laten doorzetten."))

    try:
        uit = _vraag_agent(host.agent_url, host.agent_token, "/poort",
                           {"poort": machine_poort})
    except urllib.error.HTTPError as exc:
        reden = ""
        try:
            reden = (json.loads(exc.read() or b"{}") or {}).get("detail", "")
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(
            status_code=502,
            detail=f"De agent op {host.naam} wilde de poort niet openzetten: "
                   f"{reden or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=(f"Geen contact met de agent op {host.naam} ({host.agent_url}). "
                    f"Draait hij daar nog? — {exc.reason}")) from exc

    gastheer = urllib.parse.urlparse(host.agent_url).hostname or ""
    poort = int(uit.get("poort") or 0)
    if not gastheer or not poort:
        raise HTTPException(
            status_code=502,
            detail=f"De agent op {host.naam} gaf geen bruikbaar adres terug.")
    log.infox("Labpoort doorgezet", lab=p.name, machine=host.naam,
              lab_poort=machine_poort, poort=poort)
    return {"url": f"http://{gastheer}:{poort}", "via": host.naam}
