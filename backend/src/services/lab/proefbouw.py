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


class InrichtFout(RuntimeError):
    """Het ging mis vóór of buiten het startscript.

    Apart van een gewone fout omdat het een ANDER soort probleem is: een
    mislukte installatie, een lab dat niet opkomt, een image dat niet
    binnenkomt. Daar is geen startscript tegen opgewassen, dus gaat dit niet
    naar het model maar naar jou -- met de echte uitvoer erbij.
    """


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
            try:
                fout = await self._poging(lab_svc, svc, c, p)
            except InrichtFout as exc:
                # Niet naar het model: een mislukte apt of een lab dat niet
                # opkomt is met geen enkel startscript te repareren. Hier
                # hoort de echte uitvoer en een mens.
                self._noteer(c, {"ronde": ronde, "fase": "inrichten mislukt",
                                 "fout": str(exc)[:3000]})
                c.proef_status = "mislukt"
                c.updated_at = _nu()
                self.db.commit()
                log.warningx("Proefbouw gestrand op het inrichten", cluster=c.naam)
                return {"status": "mislukt", "lab_id": p.id,
                        "log": list(c.proef_log or [])}

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

        Geeft terug wat er misging, of None als de applicatie draait. Een
        fout die NIET van het script komt wordt opgeworpen als
        `InrichtFout` -- daar kan het model niets aan repareren, en hem toch
        voorleggen levert alleen een ronde op waarin hij terecht om de echte
        uitvoer vraagt.
        """
        await self._wacht_op_container(lab_svc, p)

        uitslag = await lab_svc.provision(p.id, force=True)
        fout, van_script = self._fout_uit(uitslag)
        if fout is not None and not van_script:
            raise InrichtFout(fout)
        if fout is not None:
            return fout

        gestart = await lab_svc.start_applicatie(p.id)
        if not gestart.get("ok"):
            raise InrichtFout(str(gestart.get("reden") or gestart.get("uitvoer")
                                  or "De applicatie kon niet gestart worden."))

        if await self._antwoordt(svc, c, p):
            return None

        # "Er antwoordt niets" is geen diagnose. Kijk wat er WEL luistert in
        # de container: de applicatie kan prima draaien op de verkeerde poort,
        # en van buiten ziet dat er identiek uit als een applicatie die niet
        # start.
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

        alle = {int(k) for k in (p.poort_map or {})}
        stil = sorted(alle - luistert)
        draait = sorted(alle & luistert)
        return (
            f"Niet alles luistert. LabX publiceert {', '.join(str(x) for x in sorted(alle))}; "
            f"{'daarvan luistert ' + ', '.join(str(x) for x in draait) + ' wel, maar ' if draait else 'er luistert niets op '}"
            f"{', '.join(str(x) for x in stil)} niet.\n"
            f"Een cluster waarvan de helft dood is, is niet klaar -- ook al doet de rest het.\n"
            f"De laatste regels van het startscript:\n{logtekst or '(het log is leeg)'}")

    async def _wacht_op_container(self, lab_svc, p, *, seconden: int = 120) -> None:
        """Wachten tot het lab echt draait.

        `provision()` keert meteen terug met "Lab draait niet" als de
        container nog aan het opkomen is -- en dat las ik als een mislukking.
        Het model kreeg dan een zin zonder enige uitvoer en vroeg terecht om
        de echte foutregel, want die was er niet: er was helemaal niets
        misgegaan behalve dat ik te vroeg keek.
        """
        einde = asyncio.get_running_loop().time() + seconden
        while asyncio.get_running_loop().time() < einde:
            self.db.refresh(p)
            werkers = [w for w in lab_svc.ensure_workers(p) if w.container_id]
            if p.status == "running" and werkers:
                return
            await asyncio.sleep(3)
        raise InrichtFout(
            f"Het lab is na {seconden} seconden nog niet opgekomen; er is geen "
            f"container om in te bouwen.")

    @staticmethod
    def _containerpoort(p) -> Optional[int]:
        """De poort waarop de applicatie hoort te luisteren.

        De EERSTE uit de poortenlijst, niet de laagste. Die volgorde komt van
        de detector, die het hoofd-Dockerfile vooraan zet. Numeriek sorteren
        koos bij IntelligentHive poort 80 -- de nginx-poort uit het
        productie-image -- en dan wacht LabX een kwartier op een applicatie
        die daar nooit komt.
        """
        for sleutel in (p.poort_map or {}):
            return int(sleutel)
        return None

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
    def _fout_uit(uitslag: Dict[str, Any]) -> tuple:
        """(wat er misging, kwam het van het eigen script?).

        Alleen de stap van het EIGEN script is iets wat het model kan
        repareren. Dat de Azure CLI niet installeerde of dat apt omviel is
        vervelend, maar daar helpt een ander startscript niet tegen -- en hem
        dat toch voorleggen levert een ronde op waarin hij terecht zegt dat
        hij er niets uit kan afleiden.

        En als het misgaat: de ECHTE uitvoer van de stap die faalde. Daar
        stond eerst een zin van mij -- "Het inrichten is mislukt vóór het
        startscript" -- en dat is precies wat je niet kunt gebruiken: geen
        commando, geen foutregel, niets om op te handelen.
        """
        stappen = uitslag.get("steps") or []
        eigen = next((s for s in stappen if s.get("key") == "setup-script"), None)
        if eigen is not None and eigen.get("status") == "error":
            return (str(eigen.get("output") or "").strip()[-3000:] or "zonder uitvoer", True)
        if eigen is not None:
            return (None, True)

        mislukt = [s for s in stappen if s.get("status") == "error"]
        if mislukt:
            regels = [
                f"Stap '{s.get('label') or s.get('key')}' faalde "
                f"(exitcode {s.get('exit_code')}):\n"
                f"{str(s.get('output') or '').strip()[-1500:] or '(geen uitvoer)'}"
                for s in mislukt
            ]
            return ("Het inrichten van het lab is mislukt:\n\n" + "\n\n".join(regels), False)

        if uitslag.get("reason"):
            return (f"Het inrichten kon niet beginnen: {uitslag['reason']}", False)
        if uitslag.get("status") == "error":
            return ("Het inrichten is mislukt, maar er is geen uitvoer bewaard.", False)
        return (None, True)

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
        # ALLE gepubliceerde poorten moeten antwoorden, niet alleen de eerste.
        #
        # Bij IntelligentHive draaide de front-end op 5173 en viel de API op
        # 7071 om met "Missing API_KEY". Ik keek naar de eerste, kreeg een
        # 200, en noemde het geslaagd. Een cluster waarvan de helft dood is,
        # is niet klaar -- en het ergste is dat het er geslaagd uitziet.
        ontbreekt = set(kaart)

        lab_svc = LabService(self.db)
        einde = asyncio.get_running_loop().time() + (geduld or GEREED_SECONDEN)
        volgende_controle = asyncio.get_running_loop().time() + LEEFT_ELKE

        while asyncio.get_running_loop().time() < einde:
            try:
                luistert = await lab_svc.luisterpoorten(p.id)
            except Exception:  # noqa: BLE001
                luistert = set()
            if ontbreekt and not (ontbreekt - luistert):
                return f"http://localhost:{machine_poort}"
            if asyncio.get_running_loop().time() >= volgende_controle:
                volgende_controle += LEEFT_ELKE
                if not await self._leeft(p):
                    log.infox("Startscript is gestopt; niet langer wachten", lab_id=p.id)
                    return None
                # Luistert hij al, maar op een ander nummer? Dan verandert dat
                # niet meer vanzelf, en is doorwachten een kwartier verspillen
                # aan een fout die nu al vaststaat.
                if _gekozen_poorten(luistert) - ontbreekt and not (ontbreekt & luistert):
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
                # Meer dan één beurt: met één sneuvelde een ronde op
                # `error_max_turns` -- het model was nog niet klaar met
                # nadenken en dan is de hele poging weg.
                max_turns=4, timeout=900.0, enable_tool_search=False)
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
