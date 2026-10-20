"""services/lab/bundels.py — een zak bestanden bij een cluster, met opdracht.

Een vaste dienst begint leeg. De Cosmos-emulator van IntelligentHive hoort
gevuld te worden uit de bron-database, maar die zit achter een
accountfirewall -- en dan sta je stil, terwijl de data als export gewoon op
je schijf staat. Dat bestand het cluster in krijgen kon tot nu toe alleen via
een lab (de bestandsbrowser), en een vaste dienst ÍS geen lab.

VIER KEUZES DIE DE REST VERKLAREN:

- **Een volume, geen map op de server.** Het cluster kan op een andere
  machine draaien. Een volume is het enige dat daar ook werkt, en het enige
  dat een wegwerpcontainer naast de dienst kan inzien.

- **De inhoud gaat nooit naar het model.** Het ziet de bestandsnamen, hun
  omvang en jouw opdracht, en schrijft een script dat de inhoud ter plekke
  leest. Een dump van een klant hoort in een container, niet in de context
  van een taalmodel. Daarom staat er in de prompt uitdrukkelijk: lees het
  manifest in je script, niet hier.

- **Alleen-lezen aangekoppeld.** Een script dat een dump inleest hoort die
  dump niet te kunnen veranderen -- anders is de tweede poging niet meer
  dezelfde als de eerste, en weet je niet meer wat je geladen hebt.

- **Uitpakken gebeurt in Python, niet in de container.** Een archief van
  buiten mag paden bevatten als `../../etc/passwd`. Dat hier afvangen is
  eenvoudiger te overzien dan een `unzip` in een image, en het scheelt een
  afhankelijkheid die alpine niet heeft.
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.cluster import Cluster, ClusterDienst
from models.cluster_bundel import KOPPELPUNT, TOEGESTANE_SOORTEN, ClusterBundel

log = get_logger(__name__)

#: Hoe groot een archief mag zijn. Een Cosmos-export van een
#: demo-omgeving is zo'n 70 MB uitgepakt en een fractie daarvan ingepakt;
#: 512 MB is ruim en houdt één verzoek nog binnen de minuut.
MAX_ARCHIEF_BYTES = 512 * 1024 * 1024
#: En hoeveel er uitgepakt uit mag komen. Zonder deze grens is een klein
#: archief met een enorme inhoud (een "zip bomb") genoeg om de schijf vol te
#: zetten.
MAX_UITGEPAKT_BYTES = 4 * 1024 * 1024 * 1024
MAX_BESTANDEN = 20_000
#: Hoeveel paden er in de boom bewaard blijven. Genoeg om te zien wat erin
#: zit en om het model een kaart te geven; dit is geen bestandsbrowser.
MAX_BOOM = 500
MAX_BEWAARDE_UITVOER = 8_000
#: Zo lang mag een bundel-opdracht draaien. Dertienduizend documenten in een
#: emulator schrijven duurt minuten, niet seconden.
TOEPAS_TIJD = 1800.0
#: Waarin een opdracht draait als het model geen image kiest.
STANDAARD_IMAGE = "python:3.12-slim"


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


def _leesbaar(bytes_: int) -> str:
    for eenheid, deler in (("GB", 1 << 30), ("MB", 1 << 20), ("kB", 1 << 10)):
        if bytes_ >= deler:
            return f"{bytes_ / deler:.1f} {eenheid}"
    return f"{bytes_} B"


def veilig_pad(naam: str) -> Optional[str]:
    """Het pad binnen de bundel, of None als het niet door de beugel kan.

    Eerst WEIGEREN, dan pas opschonen. Andersom is precies de fout die hier
    eerder in zat: `lstrip("./")` maakte van `/etc/passwd` een keurig
    `etc/passwd` en liet het door.
    """
    naam = (naam or "").replace("\\", "/")
    if not naam or naam.startswith("/") or naam.startswith("~"):
        return None
    delen = [d for d in naam.split("/") if d not in ("", ".")]
    if any(d == ".." for d in delen):
        return None
    pad = "/".join(delen)
    return pad or None


class BundelService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── lezen ────────────────────────────────────────────────────────────────

    def lijst(self, cluster_id: int) -> List[ClusterBundel]:
        return (self.db.query(ClusterBundel)
                .filter(ClusterBundel.cluster_id == int(cluster_id))
                .order_by(ClusterBundel.id.desc()).all())

    def dict(self, b: ClusterBundel) -> Dict[str, Any]:
        return {
            "id": b.id, "cluster_id": b.cluster_id, "naam": b.naam,
            "bestandsnaam": b.bestandsnaam,
            "bytes_uitgepakt": b.bytes_uitgepakt,
            "omvang": _leesbaar(b.bytes_uitgepakt),
            "aantal_bestanden": b.aantal_bestanden,
            "boom": list(b.boom or []),
            "instructie": b.instructie or "",
            "koppelpunt": KOPPELPUNT,
            "laatst_toegepast": b.laatst_toegepast,
            "laatste_exitcode": b.laatste_exitcode,
            "laatste_uitvoer": b.laatste_uitvoer or "",
        }

    # ── binnenhalen ──────────────────────────────────────────────────────────

    async def zet(self, cluster_id: int, *, bestandsnaam: str, pad: str,
                  naam: str = "", instructie: str = "") -> ClusterBundel:
        """Een archief uitpakken in een eigen volume van dit cluster.

        `pad` wijst naar het archief zoals het al op schijf staat -- de router
        streamt de upload daarheen, zodat 500 MB niet door het geheugen van de
        backend hoeft.
        """
        c = self.db.get(Cluster, int(cluster_id))
        if c is None:
            raise HTTPException(status_code=404, detail="Cluster niet gevonden")
        kort = bestandsnaam.lower()
        if not any(kort.endswith(s) for s in TOEGESTANE_SOORTEN):
            raise HTTPException(
                status_code=400,
                detail=(f"'{bestandsnaam}' is geen archief dat LabX kan uitpakken. "
                        f"Verpak het als {', '.join(TOEGESTANE_SOORTEN)}."))
        if os.path.getsize(pad) > MAX_ARCHIEF_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Het archief is groter dan {_leesbaar(MAX_ARCHIEF_BYTES)}.")

        werkmap = tempfile.mkdtemp(prefix="labx-bundel-")
        try:
            boom, totaal = self._pak_uit(pad, werkmap, kort)
            if not boom:
                raise HTTPException(status_code=400,
                                    detail="Het archief is leeg, of bevat alleen paden "
                                           "die buiten de bundel wijzen.")
            rij = ClusterBundel(
                cluster_id=c.id,
                naam=(naam.strip() or _kale_naam(bestandsnaam))[:120],
                bestandsnaam=bestandsnaam[:255], volume="",
                bytes_uitgepakt=totaal, aantal_bestanden=len(boom),
                boom=[{"pad": p, "bytes": b} for p, b in boom[:MAX_BOOM]],
                instructie=(instructie or "").strip() or None,
                created_at=_nu(), updated_at=_nu())
            self.db.add(rij)
            self.db.commit()
            self.db.refresh(rij)

            rij.volume = f"labx_bundel_{rij.id}"
            try:
                await self._runtime(c).vul_volume(rij.volume, werkmap)
            except Exception:
                self.db.delete(rij)
                self.db.commit()
                raise
            self.db.commit()
            self.db.refresh(rij)
            log.infox("Bundel binnengehaald", cluster=c.naam, bundel=rij.naam,
                      bestanden=rij.aantal_bestanden, bytes=rij.bytes_uitgepakt)
            return rij
        finally:
            shutil.rmtree(werkmap, ignore_errors=True)

    def _pak_uit(self, archief: str, doel: str, kort: str) -> tuple:
        """Uitpakken met de grenzen erbij. Geeft (paden, totaal aantal bytes).

        Geen `extractall`: die pakt uit wat erin zit, en wat erin zit komt van
        buiten. Per ingang kijken wat hij beweert te zijn is het enige moment
        waarop een `../..` of een symlink nog tegen te houden is.
        """
        boom: List[tuple] = []
        totaal = 0

        def plek(naam: str) -> Optional[str]:
            veilig = veilig_pad(naam)
            return os.path.join(doel, veilig) if veilig else None

        def ruimte(bytes_: int) -> None:
            if totaal + bytes_ > MAX_UITGEPAKT_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Uitgepakt is dit meer dan {_leesbaar(MAX_UITGEPAKT_BYTES)}.")
            if len(boom) >= MAX_BESTANDEN:
                raise HTTPException(
                    status_code=400,
                    detail=f"Meer dan {MAX_BESTANDEN} bestanden in één bundel.")

        if kort.endswith(".zip"):
            with zipfile.ZipFile(archief) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    doelpad = plek(info.filename)
                    if doelpad is None:
                        log.warningx("Bundelpad geweigerd", pad=info.filename[:200])
                        continue
                    ruimte(info.file_size)
                    os.makedirs(os.path.dirname(doelpad), exist_ok=True)
                    with z.open(info) as bron, open(doelpad, "wb") as uit:
                        shutil.copyfileobj(bron, uit)
                    totaal += info.file_size
                    boom.append((veilig_pad(info.filename), info.file_size))
        else:
            with tarfile.open(archief) as t:
                for lid in t:
                    # Geen symlinks, geen apparaten: alleen echte bestanden.
                    # Een symlink in een archief wijst naar buiten de bundel
                    # zodra hij aangekoppeld wordt, en dat is het hele punt
                    # niet.
                    if not lid.isfile():
                        continue
                    doelpad = plek(lid.name)
                    if doelpad is None:
                        log.warningx("Bundelpad geweigerd", pad=lid.name[:200])
                        continue
                    ruimte(lid.size)
                    os.makedirs(os.path.dirname(doelpad), exist_ok=True)
                    bron = t.extractfile(lid)
                    if bron is None:
                        continue
                    with open(doelpad, "wb") as uit:
                        shutil.copyfileobj(bron, uit)
                    totaal += lid.size
                    boom.append((veilig_pad(lid.name), lid.size))
        boom.sort(key=lambda x: x[0])
        return boom, totaal

    async def verwijder(self, bundel_id: int) -> None:
        b = self._bundel(bundel_id)
        c = self.db.get(Cluster, b.cluster_id)
        if c is not None and b.volume:
            try:
                await self._runtime(c).remove_volume(b.volume)
            except Exception as exc:  # noqa: BLE001 — weg is weg
                log.warningx("Bundelvolume opruimen mislukte", volume=b.volume,
                             error=str(exc)[:200])
        self.db.delete(b)
        self.db.commit()

    def wijzig(self, bundel_id: int, *, naam: Optional[str] = None,
               instructie: Optional[str] = None) -> ClusterBundel:
        b = self._bundel(bundel_id)
        if naam is not None and naam.strip():
            b.naam = naam.strip()[:120]
        if instructie is not None:
            b.instructie = instructie.strip() or None
        b.updated_at = _nu()
        self.db.commit()
        self.db.refresh(b)
        return b

    # ── toepassen ────────────────────────────────────────────────────────────

    async def toepassen(self, bundel_id: int, *, dienst_id: int,
                        instructie: str = "", script: str = "",
                        image: str = "") -> Dict[str, Any]:
        """De bundel op een vaste dienst loslaten.

        Zonder `script` bedenkt het model er een uit jouw opdracht en de
        bestandsnamen. Mét `script` draait precies dat -- want een opdracht
        die één keer goed ging wil je daarna ongewijzigd kunnen herhalen,
        zonder dat een model er opnieuw naar raadt.
        """
        b = self._bundel(bundel_id)
        d = self.db.get(ClusterDienst, int(dienst_id))
        if d is None or d.cluster_id != b.cluster_id:
            raise HTTPException(status_code=404,
                                detail="Die dienst hoort niet bij dit cluster.")
        opdracht = (instructie or b.instructie or "").strip()
        if not script and not opdracht:
            raise HTTPException(
                status_code=400,
                detail=("Zeg erbij wat ermee moet gebeuren — bijvoorbeeld "
                        "'zet deze dump in de Cosmos-emulator'."))

        toelichting = ""
        if not script:
            from services.lab.dienst_acties import DienstActieService, _lees_json

            acties = DienstActieService(self.db)
            uit = _lees_json(await acties._vraag(self._prompt(b, d, opdracht)))
            script = str(uit.get("script") or "").strip()
            image = image or str(uit.get("image") or "")
            toelichting = str(uit.get("toelichting") or "")
            if not script:
                return {"toelichting": toelichting or
                        "Het model kwam niet met een script voor deze bundel.",
                        "script": "", "uitvoer": "", "exit_code": None}

        c = self.db.get(Cluster, b.cluster_id)
        rt = self._runtime(c)
        poorten = [int(p) for p in (d.poorten or [])]
        uitslag = await rt.run_wegwerp(
            image=image or STANDAARD_IMAGE,
            cmd=["sh", "-c", script],
            netwerk=c.netwerk,
            env={"DIENST": d.naam, "DIENST_HOST": d.naam, "BUNDEL": KOPPELPUNT,
                 **({"DIENST_POORT": str(poorten[0])} if poorten else {})},
            volumes={b.volume: KOPPELPUNT},
            timeout=TOEPAS_TIJD)

        b.laatst_toegepast = _nu()
        b.laatste_exitcode = int(uitslag.get("exit_code") or 0)
        b.laatste_uitvoer = (uitslag.get("output") or "")[-MAX_BEWAARDE_UITVOER:]
        b.updated_at = _nu()
        self.db.commit()
        log.infox("Bundel toegepast", bundel=b.naam, dienst=d.naam,
                  exitcode=b.laatste_exitcode)
        return {"toelichting": toelichting, "script": script,
                "image": image or STANDAARD_IMAGE,
                "uitvoer": uitslag.get("output") or "",
                "exit_code": uitslag.get("exit_code")}

    def _prompt(self, b: ClusterBundel, d: ClusterDienst, opdracht: str) -> str:
        from services.lab.dienst_acties import DienstActieService

        regels = "\n".join(
            f"  {KOPPELPUNT}/{x['pad']}  ({_leesbaar(int(x['bytes']))})"
            for x in (b.boom or [])[:120])
        meer = (f"\n  … en nog {b.aantal_bestanden - 120} bestanden"
                if b.aantal_bestanden > 120 else "")
        return _TOEPAS.format(
            beeld=DienstActieService(self.db)._beeld(d),
            bundel=b.naam, koppelpunt=KOPPELPUNT,
            aantal=b.aantal_bestanden, omvang=_leesbaar(b.bytes_uitgepakt),
            bestanden=regels + meer,
            opdracht=opdracht[:4000],
            standaard_image=STANDAARD_IMAGE)

    # ── rijen ────────────────────────────────────────────────────────────────

    def _bundel(self, bundel_id: int) -> ClusterBundel:
        rij = self.db.get(ClusterBundel, int(bundel_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Bundel niet gevonden")
        return rij

    def _runtime(self, c: Cluster):
        from services.lab.lab_service import LabService

        svc = LabService(self.db)
        return svc._docker_op_host(c.host_id) if c.host_id else svc.runtime


def _kale_naam(bestandsnaam: str) -> str:
    naam = bestandsnaam.rsplit("/", 1)[-1]
    for achtervoegsel in TOEGESTANE_SOORTEN:
        if naam.lower().endswith(achtervoegsel):
            return naam[: -len(achtervoegsel)] or naam
    return naam


_TOEPAS = """Je schrijft één script dat een aangeleverde bundel bestanden verwerkt tegen
een vaste dienst in een LabX-cluster.

{beeld}
DE BUNDEL "{bundel}" — {aantal} bestanden, {omvang} uitgepakt — hangt
ALLEEN-LEZEN op {koppelpunt} in de container waarin jouw script draait:
{bestanden}

WAT DE GEBRUIKER WIL:
{opdracht}

HOE JE SCRIPT DRAAIT:
- In een wegwerpcontainer op het netwerk van het cluster, met `sh -c`. Kies
  zelf het image ("image"); {standaard_image} kan bijna alles, mits je zelf
  installeert wat je nodig hebt (`pip install --quiet ...`).
- De dienst is bereikbaar op $DIENST_HOST en $DIENST_POORT. De bundel staat
  op $BUNDEL.
- Er is internet.

HARDE REGELS:
- **Je hebt de INHOUD van de bestanden niet gezien en krijgt die ook niet.**
  Je kent alleen de namen en de omvang. Staat er een manifest of een index
  in de bundel, LEES DIE DAN IN JE SCRIPT en laat je script zich daarnaar
  voegen — ga niet raden wat erin staat.
- Schrijf nooit naar {koppelpunt}; die is alleen-lezen.
- Werk in stappen die je kunt volgen, en print per stap wat er gebeurde en
  hoeveel. Een script dat tien minuten zwijgt en dan "klaar" zegt is niet te
  vertrouwen.
- Laat het script stoppen met een nette melding als iets ontbreekt, in plaats
  van door te gaan met de helft.
- POSIX sh, geen bashisms.

Antwoord met UITSLUITEND JSON:
{{"toelichting": "twee zinnen: wat je script doet en waarom zo",
  "image": "het image waarin het draait",
  "script": "het script"}}"""
