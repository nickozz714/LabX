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

#: Hoe lang we wachten tot er iets op de poort antwoordt.
#:
#: Dit stond op 45 seconden, en dat was de denkfout: het startscript INSTALLEERT
#: ook. Bij een kleine React-app duurde `npm install` alleen al twintig
#: seconden, bij een monorepo met twaalf packages duurt de bouw minuten. De
#: proefbouw gaf het dus op terwijl alles goed ging -- de applicatie stond een
#: halve minuut later gewoon te draaien, met een HTTP 200.
#:
#: Lang wachten kost niets zolang we ook merken dat het script GESTOPT is:
#: dan is er niets meer om op te wachten.
GEREED_SECONDEN = int(__import__("os").environ.get("LABX_PROEF_WACHTTIJD", "900"))

#: Hoe vaak we kijken of het script nog leeft, en of er misschien al iets op
#: de VERKEERDE poort luistert. Een gevallen script hoeft de hele wachttijd
#: niet uit te zitten, en een verkeerde poort al helemaal niet: die verandert
#: niet meer vanzelf.
LEEFT_ELKE = 15

#: Poorten die niets zeggen over de applicatie. De Claude-CLI en wat
#: gereedschap openen er zelf een; die horen niet als "hij luistert op het
#: verkeerde nummer" gelezen te worden.
_NEGEER_POORTEN = {22, 53}

#: Alles hierboven is een vluchtige poort die het besturingssysteem uitdeelt,
#: geen nummer dat iemand gekozen heeft. In de proefbouw bleef 41361 opduiken
#: en werd dus gemeld als "hij luistert op het verkeerde nummer" -- terwijl er
#: helemaal geen applicatie luisterde. Dat stuurt het model de verkeerde kant
#: op, en dat is erger dan niets zeggen.
VLUCHTIG_VANAF = 32768


def _gekozen_poorten(poorten) -> set:
    """Alleen poorten waar iemand een besluit over genomen heeft."""
    return {p for p in poorten if p not in _NEGEER_POORTEN and p < VLUCHTIG_VANAF}

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
        """Inrichten, starten, kijken of er iets antwoordt.

        Drie stappen en niet één, omdat ze op verschillende dingen afgerekend
        worden. Inrichten moet EINDIGEN. De applicatie moet juist blijven
        draaien -- die wordt dus op de achtergrond gestart en beoordeeld op de
        poort, niet op een exitcode die nooit komt.
        """
        from services.lab.lab_service import LabService

        lab_svc = LabService(self.db)
        for ronde in range(1, MAX_RONDES + 1):
            fout = await self._poging(lab_svc, svc, c, p)

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
                self._noteer(c, {"ronde": ronde, "fase": "opgegeven", "fout": fout})
                break

            nieuw_script = await self._herstel(c, fout, ronde)
            self._noteer(c, {"ronde": ronde, "fase": "bijgesteld", "fout": fout,
                             "toelichting": nieuw_script.get("toelichting"),
                             "gewijzigd": bool(nieuw_script.get("script"))})
            if not nieuw_script.get("script"):
                break
            c.start_script = nieuw_script["script"]
            c.updated_at = _nu()
            self.db.commit()

        c.proef_status = "mislukt"
        c.updated_at = _nu()
        self.db.commit()
        log.warningx("Proefbouw niet gelukt", cluster=c.naam, rondes=MAX_RONDES)
        return {"status": "mislukt", "lab_id": p.id, "log": list(c.proef_log or [])}

    async def _poging(self, lab_svc, svc, c: Cluster, p) -> Optional[str]:
        """Eén poging: inrichten, starten, wachten tot de poort antwoordt.

        Geeft terug wat er misging, of None als de applicatie draait.
        """
        uitslag = await lab_svc.provision(p.id, force=True)
        fout = self._fout_uit(uitslag)
        if fout is not None:
            return fout

        gestart = await lab_svc.start_applicatie(p.id)
        if not gestart.get("ok"):
            return str(gestart.get("reden") or gestart.get("uitvoer") or "Starten mislukte.")

        if await self._antwoordt(svc, c, p):
            return None

        # "Er antwoordt niets" is geen diagnose. Kijk wat er WEL luistert in
        # de container: de applicatie kan prima draaien op de verkeerde poort,
        # en van buiten ziet dat er identiek uit als een applicatie die niet
        # start. Precies dat gebeurde -- Vite luisterde op 18002, het nummer
        # op de MACHINE, terwijl de container 8099 publiceert.
        logtekst = (await lab_svc.start_log(p.id)).strip()
        gewenst = self._containerpoort(p)
        luistert = await lab_svc.luisterpoorten(p.id)
        verkeerd = sorted(_gekozen_poorten(luistert) - {gewenst})

        if gewenst and verkeerd:
            return (
                f"De applicatie luistert op de verkeerde poort. LabX publiceert "
                f"containerpoort {gewenst}, maar binnen de container luistert er niets "
                f"op {gewenst} -- wel op {', '.join(str(x) for x in verkeerd)}.\n"
                f"Laat de applicatie luisteren op $LABX_POORT ({gewenst}). Dat is de "
                f"poort BINNEN de container; LABX_LAB_POORT is het nummer op de machine "
                f"en daar mag je nooit op binden.\n"
                f"De laatste regels van het startscript:\n{logtekst or '(het log is leeg)'}")

        return (f"Er antwoordt niets op poort {gewenst or '?'} binnen "
                f"{GEREED_SECONDEN} seconden, en er luistert ook niets anders in de "
                f"container.\nDe laatste regels van het startscript:\n"
                f"{logtekst or '(het log is leeg)'}")

    @staticmethod
    def _containerpoort(p) -> Optional[int]:
        kaart = {int(k): int(v) for k, v in (p.poort_map or {}).items()}
        return sorted(kaart)[0] if kaart else None

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

    async def _antwoordt(self, svc, c: Cluster, p, *, geduld: Optional[int] = None) -> Optional[str]:
        """Luistert er iets op de poort van dit lab?

        **De vraag wordt IN het lab gesteld, niet erbuiten.** Dat was de
        hardnekkigste fout van allemaal: ik maakte een TCP-verbinding naar
        127.0.0.1:<machinepoort>, maar LabX draait zelf in een container, en
        daar is die poort niet -- die staat op de loopback van de HOST. Dus
        faalde elke proefbouw op "er antwoordt niets", terwijl een curl vanaf
        de host al rondenlang HTTP 200 gaf.

        Binnen de container kijken heeft nog een voordeel: het werkt ook als
        het lab op een andere machine draait, waar we sowieso niet zomaar bij
        de loopback kunnen.

        We wachten lang, want het script installeert en bouwt ook. Maar we
        kijken ondertussen of het nog LEEFT: een gevallen script hoeft de hele
        wachttijd niet uit te zitten.
        """
        from services.lab.lab_service import LabService

        gewenst = self._containerpoort(p)
        if not gewenst:
            return None
        kaart = {int(k): int(v) for k, v in (p.poort_map or {}).items()}
        machine_poort = kaart[gewenst]

        lab_svc = LabService(self.db)
        einde = asyncio.get_running_loop().time() + (geduld or GEREED_SECONDEN)
        volgende_controle = asyncio.get_running_loop().time() + LEEFT_ELKE

        while asyncio.get_running_loop().time() < einde:
            try:
                luistert = await lab_svc.luisterpoorten(p.id)
            except Exception:  # noqa: BLE001
                luistert = set()
            if gewenst in luistert:
                return f"http://localhost:{machine_poort}"
            if asyncio.get_running_loop().time() >= volgende_controle:
                volgende_controle += LEEFT_ELKE
                if not await self._leeft(p):
                    log.infox("Startscript is gestopt; niet langer wachten", lab_id=p.id)
                    return None
                # Luistert hij al, maar op een ander nummer? Dan verandert dat
                # niet meer vanzelf, en is doorwachten een kwartier verspillen
                # aan een fout die nu al vaststaat.
                if _gekozen_poorten(luistert) - {gewenst}:
                    log.infox("Applicatie luistert op de verkeerde poort", lab_id=p.id)
                    return None
            await asyncio.sleep(3)
        return None

    async def _leeft(self, p) -> bool:
        """Draait het startscript nog?

        Zo niet, dan is er niets meer om op te wachten -- het is gevallen of
        het keerde terug, en allebei betekent dat er niets gaat luisteren.
        """
        from services.lab.lab_service import LabService

        svc = LabService(self.db)
        werkers = [w for w in svc.ensure_workers(p) if w.container_id]
        if not werkers:
            return False
        try:
            uit = await svc.runtime_voor(p).exec(
                werkers[0].container_id,
                ["sh", "-c", "pgrep -f labx-start.sh >/dev/null && echo leeft || echo weg"],
                workdir="/workspace", timeout=20)
            return "leeft" in str(uit.get("output") or "")
        except Exception:  # noqa: BLE001 — niet kunnen kijken is geen doodverklaring
            return True

    async def _herstel(self, c: Cluster, fout: str, ronde: int) -> Dict[str, Any]:
        """Het model het script laten bijstellen op grond van de echte fout."""
        from services.lab.verkenner import VERKENNER_MODEL, _lees_json

        # De omgeving die het lab ECHT krijgt, niet alleen wat op het cluster
        # staat. Zonder de LABX_*-variabelen erbij concludeerde het model
        # "LABX_POORT is niet gezet" en ging het een workaround schrijven voor
        # een probleem dat niet bestond -- die variabele stond er gewoon.
        omgeving = dict(c.omgeving or {})
        omgeving.update(self._labx_omgeving(c))
        prompt = _HERSTEL_PROMPT.format(
            ronde=ronde, max_rondes=MAX_RONDES,
            image=c.lab_image or "het standaard lab-image",
            omgeving="\n".join(f"  {k}={v}" for k, v in sorted(omgeving.items())) or "  (geen)",
            script=c.start_script or "", fout=fout)
        try:
            from services.agent.claude_cli_provider import ClaudeCliProvider
            from services.settings_service import get_settings

            s = get_settings(self.db)
            provider = ClaudeCliProvider(
                default_model=VERKENNER_MODEL,
                oauth_token=getattr(s, "oauth_token", None),
                cli_path=s.cli_path or "claude",
                # Ruim: Opus leest een script, een foutmelding en een
                # omgeving en schrijft er een nieuw script bij. Op 300
                # seconden sneuvelde een hele ronde aan de tijdslimiet, en
                # dan is de rest van de poging ook weg.
                max_turns=1, timeout=900.0, enable_tool_search=False)
            uit = await provider.chat(prompt)
        except Exception as exc:  # noqa: BLE001
            log.warningx("Herstelronde zonder model", error=str(exc)[:300])
            return {"script": None, "toelichting": f"Het model kon niet meekijken: {exc}"}

        antwoord = _lees_json(uit.text)
        return {"script": (antwoord.get("startscript") or "").strip() or None,
                "toelichting": str(antwoord.get("toelichting") or "")[:600]}

    def _labx_omgeving(self, c: Cluster) -> Dict[str, str]:
        """Wat LabX zelf aan het lab meegeeft.

        Staat niet op het cluster maar wordt bij het starten toegevoegd, en
        het model moet het wél weten -- anders lost het iets op wat al
        geregeld is.
        """
        from models.lab import Lab

        p = self.db.get(Lab, c.proef_lab_id) if c.proef_lab_id else None
        if p is None or not (p.poort_map or {}):
            return {}
        from services.lab.clusters import ClusterService

        return ClusterService._adres_omgeving(c, {int(k): int(v) for k, v in p.poort_map.items()})

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
({image}), ALS ROOT, en met de werkkopie van de repo als werkmap -- dus
`package.json` en `pyproject.toml` staan gewoon naast je, zonder dat je ze
hoeft te zoeken.

LabX start dit script op de ACHTERGROND en kijkt daarna of er iets op de
poort antwoordt. Laat de applicatie dus op de voorgrond draaien (`exec ...`):
het script hoort NIET terug te keren als alles goed is. Een script dat wel
terugkeert, laat een lab achter waar niets op luistert.

DE POORT, en hier is geen keuze in: de applicatie luistert op $LABX_POORT.
Dat is de poort BINNEN de container, en die publiceert LabX naar buiten.
LABX_LAB_POORT is het nummer op de MACHINE -- dat is alleen voor het opbouwen
van een adres, en daar mag je nooit op binden. Doe je dat toch, dan draait
alles schijnbaar goed en komt er van buiten niets door.

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
