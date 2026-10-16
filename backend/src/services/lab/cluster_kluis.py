"""
services/lab/cluster_kluis.py

De kluis van een cluster.

Een cluster weet welke geheimen zijn applicatie nodig heeft -- dat is de
tweede lijst van de verkenner, en die lijst is de helft van wat een plan
oplevert. Tot nu toe stonden de WAARDEN in de gedeelde kluis, en daar is de
naam globaal uniek. Dat werkt voor `teams-webhook`, en niet voor `JWT_SECRET`:
die heet in elke applicatie zo.

**De zoekvolgorde.** Eerst de kluis van het cluster, dan de gedeelde. Zo kan
een toegangstoken één keer bestaan en overal gelden, terwijl `DB_PASS` per
cluster iets anders is. Een cluster-geheim wint altijd van een gedeeld geheim
met dezelfde naam -- het specifiekere antwoord hoort te winnen, anders is het
specifieke geheim zinloos.

**De waarde komt hier naar buiten.** Deze module is de enige plek die
ontsleutelt, en het resultaat gaat één kant op: de omgeving van een container.
Niet naar een scherm, niet naar een log, niet naar de context van een model.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.cluster_secret import ClusterSecret

log = get_logger(__name__)

#: Een naam die in een omgevingsvariabele past. Ruimer dan de gedeelde kluis,
#: want daar moest hij in `{{secret:...}}` passen; hier is het een env-naam.
_NAAM_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,119}$")


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClusterKluis:
    def __init__(self, db: Session) -> None:
        self.db = db

    def lijst(self, cluster_id: int) -> List[ClusterSecret]:
        return (self.db.query(ClusterSecret)
                .filter(ClusterSecret.cluster_id == cluster_id)
                .order_by(ClusterSecret.naam).all())

    def haal(self, cluster_id: int, naam: str) -> Optional[ClusterSecret]:
        return (self.db.query(ClusterSecret)
                .filter(ClusterSecret.cluster_id == cluster_id,
                        ClusterSecret.naam == str(naam or "").strip()).first())

    def zet(self, cluster_id: int, *, naam: str, waarde: Optional[str] = None,
            omschrijving: Optional[str] = None) -> ClusterSecret:
        """Aanmaken of bijwerken.

        Een lege waarde bij het bijwerken betekent "laat staan": het scherm
        krijgt hem toch nooit terug, dus een leeg veld is de normale toestand
        als je alleen de omschrijving aanpast.
        """
        from utils.crypto import encrypt

        naam = str(naam or "").strip()
        if not _NAAM_OK.match(naam):
            raise HTTPException(
                status_code=400,
                detail=("Een naam begint met een letter of _ en bevat verder letters, "
                        "cijfers, _, - of een punt."))
        rij = self.haal(cluster_id, naam)
        if rij is None:
            if not (waarde or "").strip():
                raise HTTPException(status_code=400,
                                    detail="Een nieuw geheim heeft een waarde nodig.")
            rij = ClusterSecret(cluster_id=cluster_id, naam=naam,
                                waarde_encrypted=encrypt(waarde),
                                created_at=_nu(), updated_at=_nu())
            self.db.add(rij)
        elif (waarde or "").strip():
            rij.waarde_encrypted = encrypt(waarde)
        if omschrijving is not None:
            rij.omschrijving = omschrijving or None
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)
        log.infox("Clustergeheim opgeslagen", cluster_id=cluster_id, naam=naam)
        return rij

    def verwijder(self, cluster_id: int, naam: str) -> bool:
        rij = self.haal(cluster_id, naam)
        if rij is None:
            return False
        self.db.delete(rij)
        self.db.commit()
        return True

    # ── opzoeken ─────────────────────────────────────────────────────────────

    def waarde(self, cluster_id: int, naam: str) -> Optional[str]:
        """De waarde, of None. Eerst dit cluster, dan de gedeelde kluis.

        Het specifiekere antwoord wint. Zou het andersom zijn, dan is een
        geheim per cluster zinloos: het wordt dan overschaduwd door precies
        het gedeelde geheim dat je wilde vermijden.
        """
        from utils.crypto import decrypt

        rij = self.haal(cluster_id, naam)
        if rij is not None:
            try:
                rij.last_used_at = _nu()
                self.db.commit()
                return decrypt(rij.waarde_encrypted)
            except Exception as exc:  # noqa: BLE001 — een kapotte sleutel is geen crash
                log.warningx("Clustergeheim niet te ontsleutelen",
                             cluster_id=cluster_id, naam=naam, error=str(exc)[:200])
                return None
        return self._gedeeld(naam)

    def _gedeeld(self, naam: str) -> Optional[str]:
        from services.secrets.vault import VaultService

        kluis = VaultService(self.db)
        rij = kluis.haal(naam)
        return kluis.waarde_van(rij) if rij is not None else None

    def bestaat(self, cluster_id: int, naam: str) -> bool:
        """Is deze naam ergens te vinden? Zonder te ontsleutelen."""
        if self.haal(cluster_id, naam) is not None:
            return True
        from services.secrets.vault import VaultService

        return VaultService(self.db).haal(naam) is not None

    def los_op(self, cluster_id: int, namen: Any) -> Dict[str, str]:
        """De omgeving die een branch-lab van zijn cluster meekrijgt.

        Alleen wat gevonden wordt komt terug. Een ontbrekend geheim levert
        geen lege variabele op: die is erger dan een die ontbreekt, want een
        applicatie die `JWT_SECRET=""` krijgt start vaak gewoon -- met een
        sleutel die iedereen kan raden.
        """
        uit: Dict[str, str] = {}
        for naam in (namen or {}):
            waarde = self.waarde(cluster_id, str(naam))
            if waarde:
                uit[str(naam)] = waarde
        return uit

    def to_dict(self, rij: ClusterSecret) -> Dict[str, Any]:
        """Nooit de waarde. Wel hoe lang hij is -- genoeg om te zien of je de
        juiste hebt geplakt, te weinig om hem te gebruiken."""
        lengte = 0
        try:
            from utils.crypto import decrypt

            lengte = len(decrypt(rij.waarde_encrypted))
        except Exception:  # noqa: BLE001
            lengte = 0
        return {"naam": rij.naam, "omschrijving": rij.omschrijving,
                "lengte": lengte, "leesbaar": lengte > 0,
                "created_at": rij.created_at, "updated_at": rij.updated_at,
                "last_used_at": rij.last_used_at}
