"""
services/lab/proefbouw.py

Het startscript één keer echt draaien, en bijstellen tot het werkt.

Een plan dat er goed uitziet maar waarvan het script omvalt, heeft zijn werk
niet af. Het model schrijft dat script op uit wat het in de repo las, en dat
is een gok die pas bij het uitvoeren waar of onwaar wordt. De eerste echte
poging liep vast op één regel:

    cp: cannot stat 'components/app/.env.example': No such file or directory

Dat is geen fout die je met beter nadenken voorkomt -- je ziet het pas als je
het draait. Dus draait LabX het, leest de fout, en laat het model het script
bijstellen op grond van wat er WERKELIJK misging.

**Drie rondes, en dan jij.** Een lus zonder plafond die zichzelf aan het
repareren is, is een lus die een uur later nog bezig is met de verkeerde
aanname. Na drie pogingen krijg jij het laatste log plus wat het model dacht.

**En "af" is niet "exitcode 0".** Af is wanneer jij zegt dat de branch goed
is. Het proeflab blijft daarom staan -- bereikbaar, met zijn poorten, zijn
terminal en zijn bestanden -- tot je het bevestigt. Pas dan is het cluster
klaar, en pas dan is het script het officiële startscript.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.cluster import Cluster

log = get_logger(__name__)

#: Hoe vaak het model het script mag bijstellen. Een lus zonder plafond is
#: een lus die een uur later nog bezig is met de verkeerde aanname.
MAX_RONDES = 3

#: Hoe lang we wachten tot er iets op de poort antwoordt nadat het script
#: klaar is. Een server die net gestart is heeft een paar tellen nodig.
GEREED_SECONDEN = 45

#: De naam van de proefbranch als je er zelf geen kiest.
STANDAARD_BRANCH = "labx/proefbouw"


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


class Proefbouw:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── de lus ───────────────────────────────────────────────────────────────

    async def start(self, cluster_id: int, *, branch: Optional[str] = None) -> Dict[str, Any]:
        """Een proefbranch maken en het startscript erop draaien.

        Lukt het niet, dan stelt het model het script bij en proberen we
        opnieuw -- tot `MAX_RONDES`. Elke ronde komt in `proef_log`, zodat je
        kunt meelezen in plaats van af te wachten.
        """
        from services.lab.clusters import ClusterService

        svc = ClusterService(self.db)
        c = svc.get_or_404(cluster_id)
        if not c.start_script:
            raise HTTPException(
                status_code=400,
                detail="Dit cluster heeft nog geen startscript; er valt niets te proberen.")
        if c.proef_status == "bezig":
            raise HTTPException(status_code=409, detail="Er loopt al een proefbouw.")

        p = await self._proeflab(svc, c, branch or STANDAARD_BRANCH)
        c.proef_lab_id = p.id
        c.proef_status = "bezig"
        c.proef_log = []
        c.updated_at = _nu()
        self.db.commit()

        try:
            return await self._rondes(svc, c, p)
        except Exception as exc:  # noqa: BLE001
            c.proef_status = "mislukt"
            self._noteer(c, {"ronde": 0, "fase": "onverwacht", "fout": str(exc)[:600]})
            self.db.commit()
            raise

    async def _rondes(self, svc, c: Cluster, p) -> Dict[str, Any]:
        from services.lab.lab_service import LabService

        lab_svc = LabService(self.db)
        for ronde in range(1, MAX_RONDES + 1):
            p.setup_script = c.start_script
            self.db.commit()

            uitslag = await lab_svc.provision(p.id, force=True)
            fout = self._fout_uit(uitslag)

            if fout is None:
                adres = await self._antwoordt(svc, c, p)
                self._noteer(c, {"ronde": ronde, "fase": "gelukt",
                                 "adres": adres, "fout": None})
                c.proef_status = "wacht_op_fiat"
                c.updated_at = _nu()
                self.db.commit()
                log.infox("Proefbouw gelukt", cluster=c.naam, ronde=ronde, adres=adres)
                return {"status": c.proef_status, "ronde": ronde, "lab_id": p.id,
                        "adres": adres, "log": list(c.proef_log or [])}

            if ronde == MAX_RONDES:
                break

            nieuw = await self._herstel(c, fout, ronde)
            self._noteer(c, {"ronde": ronde, "fase": "bijgesteld", "fout": fout,
                             "toelichting": nieuw.get("toelichting"),
                             "gewijzigd": bool(nieuw.get("script"))})
            if not nieuw.get("script"):
                break
            c.start_script = nieuw["script"]
            c.updated_at = _nu()
            self.db.commit()

        c.proef_status = "mislukt"
        c.updated_at = _nu()
        self.db.commit()
        log.warningx("Proefbouw niet gelukt", cluster=c.naam, rondes=MAX_RONDES)
        return {"status": "mislukt", "lab_id": p.id, "log": list(c.proef_log or [])}

    # ── de onderdelen ────────────────────────────────────────────────────────

    async def _proeflab(self, svc, c: Cluster, branch: str):
        """Het lab waarin geprobeerd wordt. Bestaat er al een, dan die.

        Het is een GEWOON branch-lab. Niets bijzonders behalve hoe het
        ontstond -- en dat is precies de bedoeling: wat je hier bewijst, geldt
        straks voor elk ander branch-lab.
        """
        from models.lab import Lab

        if c.proef_lab_id:
            bestaand = self.db.get(Lab, c.proef_lab_id)
            if bestaand is not None:
                return bestaand

        bestaat = (self.db.query(Lab)
                   .filter(Lab.cluster_id == c.id, Lab.branch == branch).first())
        if bestaat is None:
            await svc.maak_branch_lab(c.id, branch=branch, basis="main")
            bestaat = (self.db.query(Lab)
                       .filter(Lab.cluster_id == c.id, Lab.branch == branch).first())
        await svc.start_branch_lab(bestaat.id)
        return self.db.get(Lab, bestaat.id)

    @staticmethod
    def _fout_uit(uitslag: Dict[str, Any]) -> Optional[str]:
        """Wat er misging, of None als alles liep.

        Alleen de stap van het EIGEN script telt. Dat de Azure CLI niet
        installeerde is vervelend, maar het is niet waar dit script over gaat
        -- en het model daarop laten repareren is hem een fout laten oplossen
        die hij niet gemaakt heeft.
        """
        stappen = uitslag.get("steps") or []
        eigen = next((s for s in stappen if s.get("key") == "setup-script"), None)
        if eigen is not None:
            if eigen.get("status") == "error":
                return str(eigen.get("output") or "").strip()[-3000:] or "zonder uitvoer"
            # Het script is gedraaid en gaf geen fout. Dat een andere stap
            # struikelde telt hier niet: anders repareert het model een fout
            # die hij niet gemaakt heeft.
            return None
        if uitslag.get("status") == "error":
            # Het script is niet eens aan de beurt geweest.
            return "Het inrichten is mislukt vóór het startscript."
        return None

    async def _antwoordt(self, svc, c: Cluster, p) -> Optional[str]:
        """Luistert er iets op de poort van dit lab?

        Een script dat klaar is en een applicatie die antwoordt zijn twee
        dingen. `exec` op de voorgrond betekent dat het script NIET terugkeert
        als het goed is -- dus hier kijken we naar de poort en niet naar een
        exitcode.
        """
        kaart = {int(k): int(v) for k, v in (p.poort_map or {}).items()}
        if not kaart:
            return None
        machine_poort = sorted(kaart.items())[0][1]
        einde = asyncio.get_running_loop().time() + GEREED_SECONDEN
        while asyncio.get_running_loop().time() < einde:
            if await self._open(machine_poort):
                return f"http://localhost:{machine_poort}"
            await asyncio.sleep(3)
        return None

    @staticmethod
    async def _open(poort: int) -> bool:
        try:
            lezer, schrijver = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", poort), timeout=3)
            schrijver.close()
            await schrijver.wait_closed()
            return True
        except Exception:  # noqa: BLE001 — dicht is een antwoord, geen fout
            return False

    async def _herstel(self, c: Cluster, fout: str, ronde: int) -> Dict[str, Any]:
        """Het model het script laten bijstellen op grond van de echte fout."""
        from services.lab.verkenner import VERKENNER_MODEL, _lees_json

        prompt = _HERSTEL_PROMPT.format(
            ronde=ronde, max_rondes=MAX_RONDES,
            image=c.lab_image or "het standaard lab-image",
            omgeving="\n".join(f"  {k}={v}" for k, v in (c.omgeving or {}).items()) or "  (geen)",
            script=c.start_script or "", fout=fout)
        try:
            from services.agent.claude_cli_provider import ClaudeCliProvider
            from services.settings_service import get_settings

            s = get_settings(self.db)
            provider = ClaudeCliProvider(
                default_model=VERKENNER_MODEL,
                oauth_token=getattr(s, "oauth_token", None),
                cli_path=s.cli_path or "claude",
                max_turns=1, timeout=300.0, enable_tool_search=False)
            uit = await provider.chat(prompt)
        except Exception as exc:  # noqa: BLE001
            log.warningx("Herstelronde zonder model", error=str(exc)[:300])
            return {"script": None, "toelichting": f"Het model kon niet meekijken: {exc}"}

        antwoord = _lees_json(uit.text)
        return {"script": (antwoord.get("startscript") or "").strip() or None,
                "toelichting": str(antwoord.get("toelichting") or "")[:600]}

    def _noteer(self, c: Cluster, regel: Dict[str, Any]) -> None:
        regels = list(c.proef_log or [])
        regels.append({**regel, "op": _nu()})
        c.proef_log = regels[-20:]

    # ── het oordeel ──────────────────────────────────────────────────────────

    def bevestig(self, cluster_id: int) -> Dict[str, Any]:
        """Jij zegt dat de branch goed is. Pas nu is het cluster klaar."""
        from services.lab.clusters import ClusterService

        c = ClusterService(self.db).get_or_404(cluster_id)
        if c.proef_status not in ("wacht_op_fiat", "mislukt"):
            raise HTTPException(
                status_code=409,
                detail="Er is niets om te bevestigen; draai eerst een proefbouw.")
        c.proef_status = "goedgekeurd"
        c.status = "draait"
        c.updated_at = _nu()
        self._noteer(c, {"ronde": 0, "fase": "bevestigd door jou"})
        self.db.commit()
        log.infox("Proefbouw bevestigd", cluster=c.naam)
        return {"status": c.proef_status, "lab_id": c.proef_lab_id}

    async def afkeuren(self, cluster_id: int, *, opmerking: str) -> Dict[str, Any]:
        """Nog niet goed. Jouw woorden gaan mee de volgende ronde in.

        Dit is geen mislukking maar de normale gang van zaken: het model weet
        wat de repo zegt, jij weet wat je wilde zien.
        """
        from services.lab.clusters import ClusterService

        svc = ClusterService(self.db)
        c = svc.get_or_404(cluster_id)
        opmerking = (opmerking or "").strip()
        if not opmerking:
            raise HTTPException(
                status_code=400,
                detail="Zeg erbij wat er niet goed is; anders probeert hij hetzelfde opnieuw.")

        self._noteer(c, {"ronde": 0, "fase": "afgekeurd door jou", "fout": opmerking})
        c.proef_status = "bezig"
        self.db.commit()

        nieuw = await self._herstel(c, f"De gebruiker zegt: {opmerking}", ronde=1)
        if nieuw.get("script"):
            c.start_script = nieuw["script"]
        self._noteer(c, {"ronde": 0, "fase": "bijgesteld",
                         "toelichting": nieuw.get("toelichting"),
                         "gewijzigd": bool(nieuw.get("script"))})
        c.updated_at = _nu()
        self.db.commit()

        from models.lab import Lab

        p = self.db.get(Lab, c.proef_lab_id) if c.proef_lab_id else None
        if p is None:
            c.proef_status = "mislukt"
            self.db.commit()
            return {"status": "mislukt", "log": list(c.proef_log or [])}
        return await self._rondes(svc, c, p)


_HERSTEL_PROMPT = """Je stelt een startscript bij dat zojuist is gedraaid en gefaald.

Dit is ronde {ronde} van {max_rondes}. Het script draait IN een container
({image}), in de werkkopie van de repo, als root, op de voorgrond.

Deze omgevingsvariabelen staan klaar:
{omgeving}

HET SCRIPT:
{script}

WAT ER MISGING (echte uitvoer, geen samenvatting):
{fout}

Repareer de oorzaak, niet het symptoom. Een bestand dat niet bestaat betekent
meestal dat het ergens anders heet of dat de stap overbodig is -- niet dat je
de fout moet onderdrukken. Verzin geen paden die je niet gezien hebt; maak een
stap liever voorwaardelijk (`[ -f x ] && cp x y`) dan dat je gokt.

Laat de applicatie op de voorgrond draaien en op 0.0.0.0 luisteren, anders is
het lab van buiten onbereikbaar.

Antwoord met UITSLUITEND JSON:

{{"toelichting": "één zin: wat er mis was en wat je veranderd hebt",
  "startscript": "#!/bin/sh\\nset -e\\n..."}}

Kun je uit deze fout niet opmaken wat er moet gebeuren, laat `startscript`
dan weg en zeg in `toelichting` wat je zou moeten weten."""
