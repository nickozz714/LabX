"""
services/lab/cluster_env.py — env-bestanden van een cluster beheren en uitrollen.

Zie models/cluster_env_file.py voor waarom ze bij het cluster horen.

Deze module is de enige plek die de inhoud ontsleutelt, en het resultaat gaat
één kant op: naar de werkkopie in een lab. Wat eruit komt richting een scherm
of een API is altijd alleen het pad en de NAMEN van de sleutels.
"""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.cluster_env_file import ClusterEnvFile

log = get_logger(__name__)

#: Een regel met een toewijzing, eventueel met `export` ervoor. Commentaar en
#: lege regels horen er niet bij; die laten we ongemoeid in de inhoud staan.
_TOEWIJZING = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

#: Hoe groot een env-bestand mag zijn. Ruim voor een lijst variabelen, krap
#: genoeg dat een per ongeluk gekozen bestand (een dump, een sleutelbos) er
#: niet in glijdt.
MAX_BYTES = 256 * 1024


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sleutelnamen(inhoud: str) -> List[str]:
    namen: List[str] = []
    for regel in inhoud.splitlines():
        m = _TOEWIJZING.match(regel)
        if m and m.group(1) not in namen:
            namen.append(m.group(1))
    return namen


def controleer_pad(pad: str) -> str:
    """Een pad binnen de werkkopie, en niets anders.

    Zonder deze controle is "importeer een env-bestand" ook "schrijf een
    willekeurig bestand in de container": een absoluut pad of een `..` zet hem
    buiten de repo neer. Dat is geen theoretisch geval -- het pad komt uit een
    formulier.
    """
    schoon = (pad or "").strip().replace("\\", "/")
    # Eerst WEIGEREN, dan pas opschonen. Andersom werd `/etc/passwd` via
    # `lstrip("./")` stilletjes `etc/passwd` -- lstrip strip een VERZAMELING
    # tekens, geen voorvoegsel, dus ook `../../etc/passwd` kwam er als
    # `etc/passwd` uit. Een ontsnapping die een plausibel pad wordt is erger
    # dan een die faalt.
    if schoon.startswith("/") or ".." in schoon.split("/"):
        raise HTTPException(
            status_code=400,
            detail="Alleen een pad binnen de repo, zonder '..' en zonder te beginnen met '/'.")
    while schoon.startswith("./"):
        schoon = schoon[2:]
    if not schoon:
        raise HTTPException(status_code=400, detail="Geef het pad van het bestand op.")
    if len(schoon) > 400:
        raise HTTPException(status_code=400, detail="Dat pad is te lang.")
    return schoon


class ClusterEnvBestanden:
    """De env-bestanden van één cluster."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── beheren ────────────────────────────────────────────────────────────
    def zet(self, cluster_id: int, pad: str, inhoud: str) -> Dict[str, Any]:
        """Een bestand importeren of vervangen."""
        from utils.crypto import encrypt

        pad = controleer_pad(pad)
        if inhoud is None:
            raise HTTPException(status_code=400, detail="Het bestand is leeg.")
        ruw = inhoud.encode("utf-8")
        if len(ruw) > MAX_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Dit bestand is groter dan {MAX_BYTES // 1024} kB; dat is geen env-bestand.")

        namen = _sleutelnamen(inhoud)
        rij = (self.db.query(ClusterEnvFile)
               .filter(ClusterEnvFile.cluster_id == cluster_id, ClusterEnvFile.pad == pad)
               .first())
        if rij is None:
            rij = ClusterEnvFile(cluster_id=cluster_id, pad=pad,
                                 inhoud_encrypted=encrypt(inhoud),
                                 sleutels=json.dumps(namen),
                                 created_at=_nu(), updated_at=_nu())
            self.db.add(rij)
        else:
            rij.inhoud_encrypted = encrypt(inhoud)
            rij.sleutels = json.dumps(namen)
            rij.updated_at = _nu()
        self.db.commit()
        log.infox("Env-bestand opgeslagen bij cluster",
                  cluster_id=cluster_id, pad=pad, sleutels=len(namen))
        return self._naar_dict(rij)

    def lijst(self, cluster_id: int) -> List[Dict[str, Any]]:
        rijen = (self.db.query(ClusterEnvFile)
                 .filter(ClusterEnvFile.cluster_id == cluster_id)
                 .order_by(ClusterEnvFile.pad).all())
        return [self._naar_dict(r) for r in rijen]

    def verwijder(self, cluster_id: int, bestand_id: int) -> Dict[str, Any]:
        rij = (self.db.query(ClusterEnvFile)
               .filter(ClusterEnvFile.cluster_id == cluster_id,
                       ClusterEnvFile.id == bestand_id).first())
        if rij is None:
            raise HTTPException(status_code=404, detail="Dat bestand staat niet bij dit cluster.")
        self.db.delete(rij)
        self.db.commit()
        return {"ok": True}

    @staticmethod
    def _naar_dict(rij: ClusterEnvFile) -> Dict[str, Any]:
        """Pad en sleutelNAMEN. Nooit de waarden."""
        try:
            namen = json.loads(rij.sleutels or "[]")
        except ValueError:
            namen = []
        return {"id": rij.id, "pad": rij.pad, "sleutels": namen,
                "aantal": len(namen), "updated_at": rij.updated_at}

    # ── uitrollen ──────────────────────────────────────────────────────────
    async def schrijf_naar_lab(self, p: Any, c: Any) -> List[str]:
        """De bestanden in de werkkopie van dit lab zetten.

        Via STDIN naar `cat`, niet als argument: een env-bestand bevat
        aanhalingstekens, backticks en dollartekens, en die overleven de reis
        door `sh -c` niet ongeschonden.

        Gebeurt bij elke start, niet alleen bij de eerste. Een bestand dat je
        na een mislukte poging bijwerkt, hoort bij de volgende start te
        gelden -- anders moet je het lab weggooien om een typefout te
        herstellen.
        """
        from services.lab.lab_service import LabService
        from utils.crypto import decrypt

        rijen = (self.db.query(ClusterEnvFile)
                 .filter(ClusterEnvFile.cluster_id == c.id).all())
        if not rijen:
            return []

        svc = LabService(self.db)
        werkers = [w for w in svc.ensure_workers(p) if w.container_id]
        if not werkers:
            return []
        rt = svc.runtime_voor(p)
        map_ = svc._repomap(c)
        geschreven: List[str] = []

        for rij in rijen:
            doel = f"{map_}/{rij.pad}"
            opdracht = (f"mkdir -p {shlex.quote(doel.rsplit('/', 1)[0])} && "
                        f"cat > {shlex.quote(doel)} && chmod 600 {shlex.quote(doel)}")
            try:
                await rt.exec(werkers[0].container_id, ["sh", "-c", opdracht],
                              stdin=decrypt(rij.inhoud_encrypted).encode(),
                              workdir="/workspace", timeout=60)
                geschreven.append(rij.pad)
            except Exception as exc:  # noqa: BLE001
                # De naam van het bestand mag in het log, de inhoud niet.
                log.warningx("Env-bestand schrijven mislukte",
                             lab_id=p.id, pad=rij.pad, error=str(exc)[:200])
        if geschreven:
            log.infox("Env-bestanden in de werkkopie gezet",
                      lab_id=p.id, aantal=len(geschreven))
        return geschreven
