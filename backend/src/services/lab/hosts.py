"""services/lab/hosts.py — computers in je netwerk die labs kunnen draaien.

Het register plus de twee handelingen die ertoe doen: een pc die zich meldt,
en een mens die hem koppelt.

Waarom koppelen een handeling van een MENS is: een aanmelding komt van iets op
je netwerk dat zegt dat het een lab-host is. Dat is geen bewijs. Wie zich meldt
komt in de lijst te staan als `nieuw` en krijgt niets -- geen labs, geen
toegang, niets -- tot iemand de code bevestigt die op het scherm van díé pc
staat. Zo kan een apparaat zichzelf niet toelaten.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.lab_host import HOST_STATUSSEN, LabHost

log = get_logger(__name__)

#: Zes cijfers: lang genoeg om niet te raden binnen de tijd dat hij geldig is,
#: kort genoeg om over te typen van het ene scherm naar het andere.
_CODE_LENGTE = 6
#: Niet meer gezien in deze tijd = offline. Ruim boven de hartslag van de
#: agent (30s), zodat één gemiste ronde geen host offline haalt.
OFFLINE_NA_SECONDEN = 150


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


def _koppelcode() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(_CODE_LENGTE))


class LabHostService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── wat de agent doet ────────────────────────────────────────────────────

    def aanmelden(self, *, machine_id: str, naam: str, agent_url: str,
                  docker_host: Optional[str] = None,
                  capaciteit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Een pc meldt zich, of meldt opnieuw dat hij er nog is.

        Dezelfde ingang voor allebei: de agent weet niet of LabX hem al kent
        (hij kan opnieuw geïnstalleerd zijn, of LabX kan een verse database
        hebben). Herkennen gebeurt op `machine_id`, niet op adres -- een laptop
        op wifi krijgt een ander IP en is daarmee niet ineens een andere
        machine.
        """
        mid = (machine_id or "").strip()
        if not mid:
            raise HTTPException(status_code=400, detail="machine_id is verplicht")

        rij = self.db.query(LabHost).filter(LabHost.machine_id == mid).first()
        nieuw = rij is None
        # Was hij weg? Dan zijn zijn labs mogelijk veranderd terwijl wij niet
        # keken -- een machine die herstart heeft geen labcontainers meer.
        # Dit moet VOOR het bijwerken van `laatst_gezien`, want daarna is hij
        # per definitie online.
        kwam_terug = bool(rij) and rij.status == "gekoppeld" and not self.is_online(rij)
        if nieuw:
            rij = LabHost(machine_id=mid, naam=(naam or mid)[:255], status="nieuw",
                          koppelcode=_koppelcode(), created_at=_nu(), updated_at=_nu())
            self.db.add(rij)
            log.infox("Nieuwe lab-host meldt zich", naam=rij.naam, machine_id=mid[:12])

        rij.naam = (naam or rij.naam)[:255]
        rij.agent_url = (agent_url or "").strip()[:255] or rij.agent_url
        if docker_host:
            rij.docker_host = docker_host.strip()[:255]
        self._zet_capaciteit(rij, capaciteit or {})
        rij.laatst_gezien = _nu()
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)

        if kwam_terug:
            log.infox("Lab-host is terug; zijn labs worden nagekeken",
                      naam=rij.naam, host_id=rij.id)
            _reconcile_in_achtergrond(rij.id)

        # De code gaat alleen terug naar een host die nog NIET gekoppeld is --
        # anders zou een aanmelding van buiten hem kunnen uitlezen.
        return {
            "status": rij.status,
            "koppelcode": rij.koppelcode if rij.status == "nieuw" else None,
            # Wat de agent moet doen: wakker blijven zodra hier labs draaien.
            "wakker_houden": rij.status == "gekoppeld",
            "hartslag_seconden": 30,
        }

    def _zet_capaciteit(self, rij: LabHost, c: Dict[str, Any]) -> None:
        def _int(sleutel: str) -> Optional[int]:
            try:
                waarde = c.get(sleutel)
                return int(waarde) if waarde is not None else None
            except (TypeError, ValueError):
                return None

        rij.cpus = _int("cpus") or rij.cpus
        rij.geheugen_mb = _int("geheugen_mb") or rij.geheugen_mb
        # Deze twee juist WEL overschrijven met de verse waarde, ook als die
        # lager is: ze zeggen hoe vol de machine nu zit, en een oude waarde
        # laten staan zou een volle machine beschikbaar laten lijken.
        if _int("vrij_mb") is not None:
            rij.vrij_mb = _int("vrij_mb")
        if _int("schijf_vrij_gb") is not None:
            rij.schijf_vrij_gb = _int("schijf_vrij_gb")
        if c.get("load") is not None:
            rij.load = str(c["load"])[:32]
        if c.get("docker_versie"):
            rij.docker_versie = str(c["docker_versie"])[:64]
        if c.get("besturingssysteem"):
            rij.besturingssysteem = str(c["besturingssysteem"])[:64]
        if c.get("blijft_wakker") is not None:
            rij.blijft_wakker = bool(c["blijft_wakker"])

    # ── wat de mens doet ─────────────────────────────────────────────────────

    def koppelen(self, host_id: int, *, code: str, docker_host: str) -> LabHost:
        """Een aangemelde pc toelaten.

        De code staat op het scherm van díé machine. Dat is het bewijs dat je
        er fysiek bij kunt -- en daarmee precies genoeg voor een thuisnetwerk,
        zonder certificaten die niemand beheert.
        """
        rij = self.get_or_404(host_id)
        if rij.status == "gekoppeld":
            return rij
        if not rij.koppelcode or (code or "").strip() != rij.koppelcode:
            raise HTTPException(
                status_code=400,
                detail=("De koppelcode klopt niet. Hij staat in het venster van de "
                        "LabX-agent op die computer."))
        doel = (docker_host or "").strip()
        if not doel:
            raise HTTPException(
                status_code=400,
                detail=("Geef op hoe LabX bij de Docker van die machine komt, "
                        "bijvoorbeeld ssh://nick@werkpc.local"))
        rij.docker_host = doel[:255]
        rij.status = "gekoppeld"
        rij.koppelcode = None          # eenmalig; een code die blijft staan is een wachtwoord
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)
        log.infox("Lab-host gekoppeld", naam=rij.naam, docker_host=rij.docker_host)
        return rij

    def weigeren(self, host_id: int) -> LabHost:
        rij = self.get_or_404(host_id)
        rij.status = "geweigerd"
        rij.koppelcode = None
        rij.docker_host = None
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)
        return rij

    def wijzig(self, host_id: int, **velden: Any) -> LabHost:
        rij = self.get_or_404(host_id)
        if velden.get("naam"):
            rij.naam = str(velden["naam"])[:255]
        if "max_labs" in velden:
            waarde = velden["max_labs"]
            rij.max_labs = max(1, int(waarde)) if waarde else None
        if "notitie" in velden:
            rij.notitie = (str(velden["notitie"]) or "").strip() or None
        if velden.get("docker_host"):
            rij.docker_host = str(velden["docker_host"])[:255]
        if velden.get("status") in HOST_STATUSSEN:
            rij.status = velden["status"]
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)
        return rij

    def verwijder(self, host_id: int) -> None:
        from models.lab import Lab

        rij = self.get_or_404(host_id)
        labs = [n for (n,) in self.db.query(Lab.name).filter(Lab.host_id == host_id).all()]
        if labs:
            raise HTTPException(
                status_code=400,
                detail=(f"Er draaien nog labs op deze machine: {', '.join(labs)}. "
                        f"Verplaats of verwijder die eerst — anders wijzen ze naar "
                        f"een host die niet meer bestaat."))
        self.db.delete(rij)
        self.db.commit()

    # ── lezen ────────────────────────────────────────────────────────────────

    def get_or_404(self, host_id: int) -> LabHost:
        rij = self.db.get(LabHost, host_id)
        if rij is None:
            raise HTTPException(status_code=404, detail="Lab-host niet gevonden")
        return rij

    def lijst(self) -> List[LabHost]:
        return self.db.query(LabHost).order_by(LabHost.naam).all()

    def is_online(self, rij: LabHost) -> bool:
        if not rij.laatst_gezien:
            return False
        try:
            gezien = datetime.fromisoformat(rij.laatst_gezien)
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - gezien).total_seconds() < OFFLINE_NA_SECONDEN

    def to_dict(self, rij: LabHost) -> Dict[str, Any]:
        from models.lab import Lab

        labs = [{"id": i, "name": n} for i, n in
                self.db.query(Lab.id, Lab.name).filter(Lab.host_id == rij.id).all()]
        return {
            "id": rij.id, "naam": rij.naam, "machine_id": rij.machine_id,
            "status": rij.status, "online": self.is_online(rij),
            "docker_host": rij.docker_host, "agent_url": rij.agent_url,
            "cpus": rij.cpus, "geheugen_mb": rij.geheugen_mb, "vrij_mb": rij.vrij_mb,
            "schijf_vrij_gb": rij.schijf_vrij_gb, "load": rij.load,
            "docker_versie": rij.docker_versie,
            "besturingssysteem": rij.besturingssysteem,
            "max_labs": rij.max_labs, "blijft_wakker": bool(rij.blijft_wakker),
            # De code alleen zolang er gekoppeld moet worden, en dan nog
            # verkort: het scherm hoeft alleen te bevestigen dat je naar de
            # juiste machine kijkt.
            "heeft_koppelcode": bool(rij.koppelcode),
            "laatst_gezien": rij.laatst_gezien,
            "notitie": rij.notitie,
            "labs": labs,
            "created_at": rij.created_at, "updated_at": rij.updated_at,
        }


def _reconcile_in_achtergrond(host_id: int) -> None:
    """De labs van een teruggekeerde machine nakijken, buiten het verzoek om.

    De agent wacht op dit antwoord en moet er niet op blijven hangen: bij hem
    bijpraten kan een paar seconden SSH kosten. Een eigen sessie, want die van
    het verzoek is straks dicht.
    """
    import asyncio

    async def _werk() -> None:
        from db.database import SessionLocal
        from services.lab.lab_service import LabService

        db = SessionLocal()
        try:
            n = await LabService(db).reconcile_host(host_id)
            if n:
                log.infox("Labs bijgewerkt na terugkeer", host_id=host_id, aantal=n)
        except Exception as exc:  # noqa: BLE001 — nooit een aanmelding laten stuklopen
            log.warningx("Nakijken na terugkeer mislukt", host_id=host_id,
                         error=str(exc)[:200])
        finally:
            db.close()

    try:
        asyncio.get_running_loop().create_task(_werk())
    except RuntimeError:
        # Geen lopende lus (een test, een los script): dan gewoon niet.
        pass
