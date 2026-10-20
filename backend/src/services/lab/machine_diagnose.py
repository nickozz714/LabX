"""services/lab/machine_diagnose.py — één waarheid over een machine.

LabX kent een machine via DRIE kanalen, en die kunnen alle drie iets anders
zeggen:

- **Wat er opgeschreven staat.** `laatst_gezien`, bijgewerkt zodra de
  hostagent zich meldt. Dat is een herinnering, geen meting: hij kan twee
  minuten oud zijn terwijl de machine inmiddels iets anders doet.
- **De hostagent**, over HTTP. Die draait als los proces en kan weg zijn
  terwijl de machine prima aanstaat.
- **Docker, over SSH.** Dat is het kanaal waarmee er werkelijk labs draaien.

Ze lopen uiteen, en juist dán is een melding gevaarlijk. Gemeten geval:
`laatst_gezien` was twee minuten oud, de agent weigerde de verbinding, SSH
gaf `Connection reset by peer` -- en LabX zei "staat die machine aan?" tegen
iemand die ernaast zat met de machine áán. Dat stuurt je naar het verkeerde
probleem, en dat is erger dan geen melding.

Dit bestand voegt de drie samen tot één uitslag met één zin eronder: wat is
er aan de hand, en wat doe je eraan. Alle schermen en alle foutmeldingen
horen diezelfde zin te gebruiken -- anders krijg je opnieuw drie verhalen.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

#: Hoe lang we op elk kanaal wachten. Kort: dit staat tussen jou en een
#: foutmelding, en een diagnose die een halve minuut duurt is zelf een
#: probleem.
AGENT_TIJD = 4.0


async def diagnose(db: Session, host) -> Dict[str, Any]:
    """Alle kanalen bevragen en er één oordeel van maken.

    Geeft `{bruikbaar, kanalen, oorzaak, melding}`. `bruikbaar` zegt of LabX
    hier werk op kan doen -- dat hangt alleen van Docker af; de rest is
    context die de MELDING bruikbaar maakt.
    """
    from services.lab.docker_runtime import DockerRuntime
    from services.lab.hosts import LabHostService

    svc = LabHostService(db)
    opgeschreven = bool(svc.is_online(host))
    agent_ok, agent_melding = await _agent(host)
    ssh_ok, ssh_melding = await _ssh(host)
    docker_ok, docker_melding = False, "Geen docker-adres ingesteld."
    if host.docker_host:
        rt = DockerRuntime(docker_host=host.docker_host)
        try:
            uit = await rt.diagnose()
        except Exception as exc:  # noqa: BLE001 — een mislukte meting ÍS de uitslag
            uit = {"daemon_up": False, "hint": str(exc)[:300]}
        docker_ok = bool(uit.get("daemon_up"))
        docker_melding = "" if docker_ok else str(uit.get("hint") or "")[:300]

    kanalen = {
        "opgeschreven": {"ok": opgeschreven, "laatst_gezien": host.laatst_gezien},
        "agent": {"ok": agent_ok, "melding": agent_melding, "url": host.agent_url},
        "ssh": {"ok": ssh_ok, "melding": ssh_melding},
        "docker": {"ok": docker_ok, "melding": docker_melding},
    }
    oorzaak, melding = _oordeel(host, kanalen)
    return {"bruikbaar": docker_ok, "kanalen": kanalen,
            "oorzaak": oorzaak, "melding": melding}


def _oordeel(host, k: Dict[str, Any]) -> tuple:
    """Van vier signalen naar één zin.

    De volgorde is de kern: wat je als EERSTE uitsluit bepaalt waar je gaat
    zoeken. Eerst of de machine überhaupt leeft, dan of we binnenkomen, dan
    pas of Docker er draait.
    """
    naam = host.naam
    laatst = (k["opgeschreven"]["laatst_gezien"] or "")[:16].replace("T", " ")
    leeft = k["opgeschreven"]["ok"] or k["agent"]["ok"] or k["ssh"]["ok"]

    if k["docker"]["ok"]:
        return "in orde", f"'{naam}' is bereikbaar en Docker draait."

    if not leeft:
        return "machine stil", (
            f"'{naam}' geeft op geen enkel kanaal antwoord — niet via de hostagent en "
            f"niet via SSH. Staat die machine aan en is hij op het netwerk?"
            + (f" Laatst gezien: {laatst}." if laatst else "")
            + " Het werk op die machine blijft gewoon staan.")

    # Hij leeft wél. Dan is "staat hij aan" precies de verkeerde vraag.
    if not k["ssh"]["ok"]:
        return "ssh dicht", (
            f"'{naam}' is aan"
            + (f" (gezien {laatst})" if laatst else "")
            + f", maar LabX komt er niet in over SSH: {k['ssh']['melding']} "
              f"Staat Remote Login aan, en staat de publieke sleutel van LabX in "
              f"~/.ssh/authorized_keys op die machine?")

    return "docker weg", (
        f"'{naam}' is aan en LabX komt binnen, maar Docker antwoordt daar niet"
        + (f": {k['docker']['melding']}" if k["docker"]["melding"] else ".")
        + " Draait Docker Desktop op die machine?")


async def _agent(host) -> tuple:
    """Meldt de hostagent zich nu, op dit moment?"""
    if not host.agent_url:
        return False, "Geen agent-adres ingesteld."
    import httpx

    try:
        async with httpx.AsyncClient(timeout=AGENT_TIJD) as client:
            r = await client.get(f"{host.agent_url.rstrip('/')}/health")
        return (r.status_code == 200), ("" if r.status_code == 200
                                        else f"status {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        return False, _kort(exc)


async def _ssh(host) -> tuple:
    if not host.docker_host:
        return False, "Geen docker-adres ingesteld."
    from services.lab.ssh_sleutel import toegang_test

    try:
        return await toegang_test(host.docker_host)
    except Exception as exc:  # noqa: BLE001
        return False, _kort(exc)


def _kort(exc: Exception) -> str:
    """De kern van een netwerkfout, zonder de stapel eromheen."""
    tekst = str(exc).strip() or type(exc).__name__
    for regel in tekst.splitlines():
        regel = regel.strip()
        if regel and not regel.startswith(("Traceback", "  ")):
            return regel[:200]
    return tekst[:200]


async def voor_lab(db: Session, lab) -> Optional[Dict[str, Any]]:
    """De diagnose van de machine waar dít lab op staat, of None voor een lab
    op deze server."""
    if not getattr(lab, "host_id", None):
        return None
    from models.lab_host import LabHost

    host = db.get(LabHost, int(lab.host_id))
    if host is None:
        return None
    return await diagnose(db, host)


async def afstemming(db: Session, host) -> Dict[str, Any]:
    """Wat LabX denkt, naast wat er werkelijk op die machine staat.

    De administratie en de werkelijkheid lopen uit elkaar zodra iemand buiten
    LabX om iets doet -- een container weggooien, Docker opnieuw installeren,
    een machine terugzetten. Dat is niet erg; wat wél erg is, is dat LabX dan
    vrolijk een status blijft tonen die nergens op slaat, en bij elke knop
    een andere fout geeft.

    Dit zet ze naast elkaar en benoemt de verschillen:

    - **weg**: LabX kent een container die er niet meer is. Startbaar, want
      `start` bouwt hem opnieuw op uit het volume.
    - **ander label**: de container bestaat maar heeft een andere status dan
      LabX denkt (gestopt terwijl LabX "running" zegt, of andersom).
    - **onbekend**: er draait een LabX-container die bij geen enkel lab in
      deze administratie hoort. Dat is meestal een lab van een ándere
      LabX-installatie -- daarom alleen MELDEN en nooit opruimen.

    Niets wordt hier gewijzigd. Een afstemming die stilletjes dingen
    rechtzet, is precies hoe je werk kwijtraakt waar niemand om vroeg.
    """
    from models.lab import Lab
    from services.lab.docker_runtime import DockerRuntime

    labs = db.query(Lab).filter(Lab.host_id == host.id).all()
    uit: Dict[str, Any] = {
        "machine": host.naam, "labs": len(labs),
        "gelijk": [], "weg": [], "ander_label": [], "onbekend": [],
        "gemeten": False, "melding": "",
    }
    if not host.docker_host:
        uit["melding"] = "Deze machine heeft geen docker-adres; er valt niets te meten."
        return uit

    rt = DockerRuntime(docker_host=host.docker_host)
    try:
        aanwezig = await rt.list_managed()
    except Exception as exc:  # noqa: BLE001
        uit["melding"] = (f"De containers op '{host.naam}' zijn niet op te vragen: "
                          f"{_kort(exc)}")
        return uit

    uit["gemeten"] = True
    op_id = {str(c.get("id") or ""): c for c in aanwezig if c.get("id")}
    gezien = set()
    for lab in labs:
        cid = (lab.container_id or "").strip()
        regel = {"lab_id": lab.id, "naam": lab.name, "status": lab.status}
        if not cid:
            # Geen container in de administratie is geen verschil: dat weet
            # LabX zelf ook. `start` bouwt hem dan opnieuw op.
            uit["gelijk"].append({**regel, "toelichting": "geen container; start bouwt hem opnieuw"})
            continue
        werkelijk = op_id.get(cid)
        if werkelijk is None:
            uit["weg"].append({**regel, "container_id": cid[:12],
                               "toelichting": "LabX kent deze container; op de machine bestaat hij niet meer"})
            continue
        gezien.add(cid)
        echt = (werkelijk.get("state") or "").lower()
        verwacht = "running" if lab.status == "running" else "niet running"
        klopt = (echt == "running") == (lab.status == "running")
        if klopt:
            uit["gelijk"].append({**regel, "toelichting": f"container {echt}"})
        else:
            uit["ander_label"].append({**regel, "container_id": cid[:12],
                                       "op_de_machine": echt, "in_labx": verwacht})

    bekend = {(l.container_id or "").strip() for l in labs}
    for cid, c in op_id.items():
        if cid in bekend:
            continue
        uit["onbekend"].append({"container_id": cid[:12], "naam": c.get("name"),
                                "state": c.get("state")})

    verschillen = len(uit["weg"]) + len(uit["ander_label"])
    if verschillen == 0 and not uit["onbekend"]:
        uit["melding"] = f"Alles klopt: {len(uit['gelijk'])} lab(s) staan zoals LabX ze kent."
    else:
        delen = []
        if uit["weg"]:
            delen.append(f"{len(uit['weg'])} container(s) weg")
        if uit["ander_label"]:
            delen.append(f"{len(uit['ander_label'])} met een andere status")
        if uit["onbekend"]:
            delen.append(f"{len(uit['onbekend'])} onbekend op de machine")
        uit["melding"] = "Verschillen: " + ", ".join(delen) + "."
    return uit
