"""services/lab/clusters.py — development clusters.

Een cluster is een groep containers die bij elkaar horen omdat ze dezelfde
applicatie draaien: vaste diensten die blijven staan, en branch-labs die komen
en gaan. Zie docs/CLUSTERS.md voor het waarom.

Deze laag doet stap 1: het cluster zelf, zijn netwerk, en de vaste diensten.
Branch-labs komen daar bovenop en zijn gewone labs -- dat is met opzet, zodat
werkers, data-guard, geheimen en verplaatsen niet twee keer bestaan.

DRIE DINGEN DIE DE REST VERKLAREN:

- **Eén cluster, één machine.** De vaste diensten en de branch-labs moeten
  elkaar over een lokaal netwerk kunnen vinden; verspreid over machines kan
  dat niet. Welke machine is wel vrij -- dezelfde lab-hosts als overal.

- **Namen in plaats van poorten.** Binnen het cluster praat alles op
  containernaam (`ih-cosmos:8081`). Poorten publiceren is alleen voor JOU,
  en dan op de loopback van die machine -- net als bij labpoorten.

- **Een dienst is wegwerpbaar, zijn data niet.** De container mag je opnieuw
  opbouwen vanaf een andere branch; het volume eronder blijft. Anders is
  "even opnieuw opbouwen" een beslissing met gevolgen die niemand overzag.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.cluster import Cluster, ClusterDienst

log = get_logger(__name__)


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


def _laatste_zinnige_regel(tekst: str) -> str:
    """De regel die zegt wat er mis ging, niet de ruis ervoor.

    Docker schrijft voortgang en fouten door elkaar. Een mislukte start gaf
    daardoor een scherm vol "Pulling fs layer" waar nergens uit bleek wat er
    aan de hand was. We nemen de laatste regel die geen voortgang is.
    """
    # Een regel met "error" erin wint: docker sluit een gebruiksfout af met
    # "See 'docker run --help'.", en dan is de laatste regel juist de minst
    # bruikbare.
    ruis = ("pulling fs layer", "waiting", "downloading", "extracting",
            "verifying checksum", "download complete", "pull complete",
            "already exists", "digest:", "status:")
    regels = [r.strip() for r in (tekst or "").splitlines() if r.strip()]
    echt = [r for r in regels
            if not any(r.lower().startswith(x) or x in r.lower()[:40] for x in ruis)]
    fouten = [r for r in echt if "error" in r.lower()]
    if fouten:
        return fouten[-1]
    return (echt[-1] if echt else (regels[-1] if regels else "onbekende fout"))


def _veilige_naam(naam: str) -> str:
    """Een naam die docker accepteert als netwerk- en containernaam."""
    kaal = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (naam or "").strip().lower()).strip("-")
    return kaal or "cluster"


class ClusterService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── lezen ────────────────────────────────────────────────────────────────

    def get_or_404(self, cluster_id: int) -> Cluster:
        rij = self.db.get(Cluster, int(cluster_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Cluster niet gevonden")
        return rij

    def lijst(self) -> List[Cluster]:
        return self.db.query(Cluster).order_by(Cluster.naam).all()

    def diensten(self, cluster_id: int) -> List[ClusterDienst]:
        return (self.db.query(ClusterDienst)
                .filter(ClusterDienst.cluster_id == cluster_id)
                .order_by(ClusterDienst.naam).all())

    def _runtime(self, c: Cluster):
        from services.lab.lab_service import LabService

        svc = LabService(self.db)
        return svc._docker_op_host(c.host_id) if c.host_id else svc.runtime

    # ── aanmaken en wijzigen ─────────────────────────────────────────────────

    def maak(self, *, naam: str, host_id: Optional[int] = None,
             **velden: Any) -> Cluster:
        naam = (naam or "").strip()
        if not naam:
            raise HTTPException(status_code=400, detail="Naam is verplicht")
        if self.db.query(Cluster).filter(Cluster.naam == naam).first():
            raise HTTPException(status_code=409,
                                detail=f"Er is al een cluster '{naam}'.")
        if host_id:
            # Dezelfde controle als bij een lab: een machine die niet
            # gekoppeld is, krijgt geen werk.
            from services.lab.lab_service import LabService
            LabService(self.db)._docker_op_host(int(host_id))

        rij = Cluster(naam=naam[:120], host_id=host_id or None,
                      netwerk=f"labx-cluster-{_veilige_naam(naam)}"[:120],
                      status="nieuw", omgeving={}, created_at=_nu(), updated_at=_nu())
        self._zet_velden(rij, velden)
        self.db.add(rij)
        self.db.commit()
        self.db.refresh(rij)
        log.infox("Cluster aangemaakt", naam=rij.naam, netwerk=rij.netwerk,
                  host_id=rij.host_id)
        return rij

    def wijzig(self, cluster_id: int, **velden: Any) -> Cluster:
        rij = self.get_or_404(cluster_id)
        self._zet_velden(rij, velden)
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)
        return rij

    @staticmethod
    def _zet_velden(rij: Cluster, velden: Dict[str, Any]) -> None:
        for sleutel in ("repo_url", "repo_token_geheim", "lab_image",
                        "start_script", "env_profiel", "notitie"):
            if sleutel in velden:
                waarde = velden[sleutel]
                setattr(rij, sleutel, (str(waarde).strip() or None) if waarde else None)
        if "omgeving" in velden and isinstance(velden["omgeving"], dict):
            rij.omgeving = {str(k): str(v) for k, v in velden["omgeving"].items()}

    def verwijder(self, cluster_id: int) -> None:
        from models.lab import Lab

        rij = self.get_or_404(cluster_id)
        labs = [n for (n,) in self.db.query(Lab.name)
                .filter(Lab.cluster_id == cluster_id).all()]
        if labs:
            raise HTTPException(
                status_code=409,
                detail=(f"Er horen nog branch-labs bij dit cluster: "
                        f"{', '.join(labs)}. Ruim die eerst op — anders blijven "
                        f"ze achter zonder vaste diensten om mee te praten."))
        for d in self.diensten(cluster_id):
            self.db.delete(d)
        self.db.delete(rij)
        self.db.commit()

    # ── vaste diensten ───────────────────────────────────────────────────────

    def voeg_dienst_toe(self, cluster_id: int, *, naam: str, image: str,
                        poorten: Optional[List[int]] = None,
                        omgeving: Optional[Dict[str, str]] = None) -> ClusterDienst:
        c = self.get_or_404(cluster_id)
        naam = _veilige_naam(naam)
        if not image.strip():
            raise HTTPException(status_code=400, detail="Een dienst heeft een image nodig.")
        bestaat = (self.db.query(ClusterDienst)
                   .filter(ClusterDienst.cluster_id == c.id,
                           ClusterDienst.naam == naam).first())
        if bestaat:
            raise HTTPException(
                status_code=409,
                detail=(f"'{naam}' bestaat al in dit cluster. De naam is ook de "
                        f"DNS-naam op het netwerk, dus hij moet uniek zijn."))
        rij = ClusterDienst(cluster_id=c.id, naam=naam, image=image.strip()[:255],
                            poorten=[int(p) for p in (poorten or [])][:8],
                            omgeving={str(k): str(v) for k, v in (omgeving or {}).items()},
                            status="gestopt", created_at=_nu(), updated_at=_nu())
        self.db.add(rij)
        self.db.commit()
        self.db.refresh(rij)
        return rij

    #: Vanaf welk nummer de poorten van clusters op de machine lopen. Hoog
    #: genoeg om niets te overschrijven wat er al draait, en op één hoop zodat
    #: je in één oogopslag ziet wat van LabX is.
    POORT_BASIS = 18000

    def _ken_poorten_toe(self, d: ClusterDienst) -> Dict[int, int]:
        """{containerpoort: machinepoort}, stabiel zolang de dienst bestaat.

        De containerpoort op de machine publiceren leek simpel, maar botst op
        alles wat daar al luistert -- bij de eerste proef liep de emulator op
        8081 vast op "address already in use". Een eigen bereik lost dat op en
        geeft meteen het adres dat je wilt: http://machine:18000.
        """
        huidig = {int(k): int(v) for k, v in (d.poort_map or {}).items()}
        bezet = set()
        for ander in self.db.query(ClusterDienst).all():
            if ander.id != d.id:
                bezet.update(int(v) for v in (ander.poort_map or {}).values())
        volgende = self.POORT_BASIS
        uit: Dict[int, int] = {}
        for p in [int(x) for x in (d.poorten or [])]:
            if p in huidig and huidig[p] not in bezet:
                uit[p] = huidig[p]
            else:
                while volgende in bezet or volgende in uit.values():
                    volgende += 1
                uit[p] = volgende
            bezet.add(uit[p])
        return uit

    async def start_dienst(self, dienst_id: int) -> ClusterDienst:
        d = self._dienst_of_404(dienst_id)
        c = self.get_or_404(d.cluster_id)
        rt = self._runtime(c)
        await rt.ensure_network_named(c.netwerk)

        # Eerst de oude weg, anders botst de naam. Een dienst is wegwerpbaar;
        # zijn volume niet, en dat blijft dus staan.
        if d.container_id:
            try:
                await rt.remove(d.container_id, timeout=120)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Oude dienst-container opruimen mislukt",
                             dienst=d.naam, error=str(exc)[:200])
        try:
            kaart = self._ken_poorten_toe(d)
            d.poort_map = {str(k): v for k, v in kaart.items()}
            d.container_id = await rt.run_dienst_container(
                naam=d.naam, image=d.image, netwerk=c.netwerk,
                volume=f"labx_dienst_{c.id}_{d.naam}",
                poorten=kaart,
                omgeving=dict(d.omgeving or {}))
            d.status, d.fout = "draait", None
            log.infox("Clusterdienst gestart", cluster=c.naam, dienst=d.naam)
        except Exception as exc:  # noqa: BLE001
            reden = _laatste_zinnige_regel(str(exc))
            d.status, d.fout = "fout", reden[:500]
            c.status, c.fout = "fout", f"{d.naam}: {reden[:300]}"
            log.warningx("Clusterdienst starten mislukt", cluster=c.naam,
                         dienst=d.naam, error=str(exc)[:200])
        d.updated_at = c.updated_at = _nu()
        if d.status == "draait" and all(
                x.status == "draait" for x in self.diensten(c.id)):
            c.status, c.fout = "draait", None
        self.db.commit()
        self.db.refresh(d)
        return d

    async def stop_dienst(self, dienst_id: int) -> ClusterDienst:
        d = self._dienst_of_404(dienst_id)
        c = self.get_or_404(d.cluster_id)
        if d.container_id:
            try:
                await self._runtime(c).remove(d.container_id, timeout=120)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Dienst stoppen mislukt", dienst=d.naam,
                             error=str(exc)[:200])
        d.container_id, d.status, d.fout = None, "gestopt", None
        d.updated_at = _nu()
        self.db.commit()
        self.db.refresh(d)
        return d

    async def verwijder_dienst(self, dienst_id: int) -> None:
        d = self._dienst_of_404(dienst_id)
        await self.stop_dienst(dienst_id)
        self.db.delete(d)
        self.db.commit()

    def _dienst_of_404(self, dienst_id: int) -> ClusterDienst:
        rij = self.db.get(ClusterDienst, int(dienst_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Dienst niet gevonden")
        return rij

    # ── voor het scherm ──────────────────────────────────────────────────────

    def to_dict(self, c: Cluster) -> Dict[str, Any]:
        from models.lab import Lab
        from models.lab_host import LabHost

        host = self.db.get(LabHost, c.host_id) if c.host_id else None
        labs = [{"id": i, "name": n, "branch": b} for i, n, b in
                self.db.query(Lab.id, Lab.name, Lab.branch)
                .filter(Lab.cluster_id == c.id).all()]
        return {
            "id": c.id, "naam": c.naam, "status": c.status, "fout": c.fout,
            "netwerk": c.netwerk,
            "host_id": c.host_id, "machine": host.naam if host else "deze server",
            "repo_url": c.repo_url, "repo_token_geheim": c.repo_token_geheim,
            "lab_image": c.lab_image, "start_script": c.start_script,
            "env_profiel": c.env_profiel, "omgeving": c.omgeving or {},
            "notitie": c.notitie,
            "diensten": [self.dienst_dict(d) for d in self.diensten(c.id)],
            "labs": labs,
            "created_at": c.created_at, "updated_at": c.updated_at,
        }

    @staticmethod
    def dienst_dict(d: ClusterDienst) -> Dict[str, Any]:
        return {"id": d.id, "naam": d.naam, "image": d.image,
                "poorten": d.poorten or [], "omgeving": d.omgeving or {},
                # Waar je hem van buiten bereikt: {containerpoort: machinepoort}.
                "poort_map": d.poort_map or {},
                "status": d.status, "fout": d.fout,
                "draait": bool(d.container_id)}
