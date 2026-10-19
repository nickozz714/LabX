"""services/lab/dienst_acties.py — knoppen bij een vaste dienst, en een chat.

Een vaste dienst is geen lab: er staat geen agent in, je kunt er niet in
chatten, en toch is hij juist het ding waar je wat MEE moet. De Cosmos-
emulator moet leeg, de seed moet opnieuw, de collecties wil je even zien.
Dat deed je tot nu toe in een terminal naast LabX, met een commando dat je
elke keer opnieuw opzocht.

DRIE KEUZES DIE DE REST VERKLAREN:

- **Het model schrijft de knoppen, niet wij.** Welke handelingen zinnig zijn
  volgt uit het IMAGE. Een Cosmos-emulator heeft andere knoppen dan een Redis
  of een Mailpit; een vaste lijst in LabX zou voor allebei verkeerd zijn. Het
  model kent die images en bedenkt er knoppen bij.

- **Je ziet het script voordat je drukt.** Een knop waarvan je niet kunt zien
  wat hij doet, druk je niet in -- zeker niet als een model hem geschreven
  heeft. Het script staat dus gewoon open op het scherm, en is aan te passen.

- **`in` of `naast`.** Gereedschap dat in het image zit (`redis-cli`) draait
  erin. Alles wat een client nodig heeft die er NIET in zit, draait ernaast:
  een wegwerpcontainer op het clusternetwerk die de dienst op naam bereikt.
  De Cosmos-emulator heeft geen curl en geen SDK, en leegmaken is juist daar
  wat je wilt -- zonder deze tweede plek was de hele functie voor die dienst
  onbruikbaar.

Wat er NIET naar het model gaat: de waarden van de omgevingsvariabelen van de
dienst. Alleen de NAMEN. Een emulator-sleutel is een geheim, ook als hij een
bekende testwaarde heeft, en een script dat hem nodig heeft leest hem op het
moment zelf uit zijn eigen omgeving.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.cluster import Cluster, ClusterDienst
from models.cluster_dienst_actie import (
    ACTIE_PLEKKEN, STANDAARD_HULPIMAGE, ClusterDienstActie,
)

log = get_logger(__name__)

#: Hoe lang een actie mag duren. Ruim: een leegmaak-script tegen een emulator
#: die net opstart heeft zijn tijd nodig, en afkappen op dertig seconden laat
#: je met een half leeggemaakte database zitten.
ACTIE_TIJD = 300.0
#: Hoeveel uitvoer er bewaard blijft bij de actie. Genoeg om te zien wat er
#: gebeurde, weinig genoeg om de rij niet te laten ontsporen.
MAX_BEWAARDE_UITVOER = 8_000
#: Hoeveel knoppen één dienst er mag hebben. Meer dan dit is geen bediening
#: meer maar een scriptmap.
MAX_ACTIES = 12


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


class DienstActieService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── lezen en bewerken ────────────────────────────────────────────────────

    def lijst(self, dienst_id: int) -> List[ClusterDienstActie]:
        return (self.db.query(ClusterDienstActie)
                .filter(ClusterDienstActie.dienst_id == int(dienst_id))
                .order_by(ClusterDienstActie.volgorde, ClusterDienstActie.id)
                .all())

    def dict(self, a: ClusterDienstActie) -> Dict[str, Any]:
        return {
            "id": a.id, "dienst_id": a.dienst_id, "label": a.label,
            "uitleg": a.uitleg or "", "plek": a.plek,
            "image": a.image or (STANDAARD_HULPIMAGE if a.plek == "naast" else ""),
            "script": a.script, "gevaarlijk": bool(a.gevaarlijk), "bron": a.bron,
            "laatst_gedraaid": a.laatst_gedraaid,
            "laatste_exitcode": a.laatste_exitcode,
            "laatste_uitvoer": a.laatste_uitvoer or "",
        }

    def zet(self, dienst_id: int, *, actie_id: Optional[int] = None,
            label: str, script: str, uitleg: str = "", plek: str = "in",
            image: str = "", gevaarlijk: bool = False,
            bron: str = "zelf") -> ClusterDienstActie:
        d = self._dienst(dienst_id)
        label = (label or "").strip()[:80]
        script = (script or "").strip()
        if not label:
            raise HTTPException(status_code=400, detail="Een knop heeft een naam nodig.")
        if not script:
            raise HTTPException(status_code=400, detail="Een knop zonder script doet niets.")
        if plek not in ACTIE_PLEKKEN:
            raise HTTPException(status_code=400,
                                detail=f"Onbekende plek '{plek}'. Kies 'in' of 'naast'.")
        rij = None
        if actie_id is not None:
            rij = self.db.get(ClusterDienstActie, int(actie_id))
            if rij is None or rij.dienst_id != d.id:
                raise HTTPException(status_code=404, detail="Knop niet gevonden")
        if rij is None:
            if len(self.lijst(d.id)) >= MAX_ACTIES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Deze dienst heeft al {MAX_ACTIES} knoppen. Haal er een weg.")
            rij = ClusterDienstActie(dienst_id=d.id, bron=bron, created_at=_nu(),
                                     volgorde=len(self.lijst(d.id)))
            self.db.add(rij)
        rij.label, rij.script, rij.uitleg = label, script, (uitleg or "").strip()[:400]
        rij.plek, rij.gevaarlijk = plek, bool(gevaarlijk)
        rij.image = (image or "").strip()[:255] or None
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)
        return rij

    def verwijder(self, actie_id: int) -> None:
        rij = self.db.get(ClusterDienstActie, int(actie_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Knop niet gevonden")
        self.db.delete(rij)
        self.db.commit()

    # ── uitvoeren ────────────────────────────────────────────────────────────

    async def draai(self, actie_id: int) -> Dict[str, Any]:
        rij = self.db.get(ClusterDienstActie, int(actie_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Knop niet gevonden")
        d = self._dienst(rij.dienst_id)
        uitslag = await self._voer_uit(d, plek=rij.plek, image=rij.image or "",
                                       script=rij.script)
        rij.laatst_gedraaid = _nu()
        rij.laatste_exitcode = int(uitslag.get("exit_code") or 0)
        rij.laatste_uitvoer = (uitslag.get("output") or "")[-MAX_BEWAARDE_UITVOER:]
        rij.updated_at = _nu()
        self.db.commit()
        self.db.refresh(rij)
        log.infox("Dienstactie gedraaid", dienst=d.naam, knop=rij.label,
                  exitcode=rij.laatste_exitcode)
        return {"actie": self.dict(rij), **uitslag}

    async def _voer_uit(self, d: ClusterDienst, *, plek: str, image: str,
                        script: str) -> Dict[str, Any]:
        c = self._cluster(d)
        rt = self._runtime(c)
        if plek == "in":
            if not d.container_id:
                raise HTTPException(
                    status_code=409,
                    detail=(f"'{d.naam}' draait niet. Een actie IN de dienst heeft een "
                            f"draaiende container nodig — start hem eerst, of maak er "
                            f"een actie 'naast de dienst' van."))
            return await rt.exec(d.container_id, ["sh", "-c", script],
                                 workdir="/", timeout=ACTIE_TIJD)
        return await rt.run_wegwerp(
            image=image or STANDAARD_HULPIMAGE,
            cmd=["sh", "-c", script],
            netwerk=c.netwerk,
            env=self._hulpomgeving(d),
            timeout=ACTIE_TIJD)

    def _hulpomgeving(self, d: ClusterDienst) -> Dict[str, str]:
        """Wat een script naast de dienst moet weten om hem te vinden.

        Zonder dit staat in elk script de naam van de dienst hard ingetypt, en
        is hij na hernoemen stil kapot.
        """
        poorten = [int(p) for p in (d.poorten or [])]
        uit = {"DIENST": d.naam, "DIENST_HOST": d.naam}
        if poorten:
            uit["DIENST_POORT"] = str(poorten[0])
            uit["DIENST_POORTEN"] = ",".join(str(p) for p in poorten)
        return uit

    # ── het model laten bedenken ─────────────────────────────────────────────

    async def voorstellen(self, dienst_id: int, *,
                          opdracht: str = "") -> Dict[str, Any]:
        """Laat het model knoppen bedenken bij deze dienst en bewaar ze.

        Bestaande knoppen blijven staan: ze worden op label bijgewerkt en
        anders toegevoegd. Iets wat jij zelf hebt bijgesteld mag niet
        verdwijnen omdat het model een ronde later iets anders bedenkt.
        """
        d = self._dienst(dienst_id)
        ruw = await self._vraag(self._prompt_knoppen(d, opdracht))
        uit = _lees_json(ruw)
        voorstellen = [v for v in (uit.get("acties") or []) if isinstance(v, dict)]
        if not voorstellen:
            return {"toegevoegd": 0, "toelichting": uit.get("toelichting") or
                    "Het model had geen knoppen voor deze dienst.",
                    "acties": [self.dict(a) for a in self.lijst(d.id)]}

        bestaand = {a.label.lower(): a for a in self.lijst(d.id)}
        aantal = 0
        for v in voorstellen[:MAX_ACTIES]:
            label = str(v.get("label") or "").strip()[:80]
            script = str(v.get("script") or "").strip()
            if not label or not script:
                continue
            oud = bestaand.get(label.lower())
            # Een knop die JIJ hebt aangepast overschrijft het model niet.
            if oud is not None and oud.bron == "zelf":
                continue
            plek = str(v.get("plek") or "in").strip()
            self.zet(d.id, actie_id=oud.id if oud else None, label=label,
                     script=script, uitleg=str(v.get("uitleg") or ""),
                     plek=plek if plek in ACTIE_PLEKKEN else "in",
                     image=str(v.get("image") or ""),
                     gevaarlijk=bool(v.get("gevaarlijk")), bron="model")
            aantal += 1
        return {"toegevoegd": aantal,
                "toelichting": str(uit.get("toelichting") or ""),
                "acties": [self.dict(a) for a in self.lijst(d.id)]}

    async def chat(self, dienst_id: int, vraag: str, *,
                   geschiedenis: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Eén beurt chat over deze dienst, die ook echt iets mag doen.

        Het model krijgt dezelfde context als de knoppenronde, plus het
        gesprek tot nu toe. Komt het met een script, dan draait dat meteen en
        gaat de uitvoer mee terug -- anders zit je naar een voorstel te kijken
        dat je zelf nog moet uitvoeren, en dan kun je net zo goed zelf een
        terminal openen.
        """
        d = self._dienst(dienst_id)
        vraag = (vraag or "").strip()
        if not vraag:
            raise HTTPException(status_code=400, detail="Er staat geen vraag in.")
        ruw = await self._vraag(self._prompt_chat(d, vraag, geschiedenis or []))
        uit = _lees_json(ruw)
        antwoord = str(uit.get("antwoord") or "").strip()
        script = str(uit.get("script") or "").strip()
        if not script:
            return {"antwoord": antwoord or "Daar heb ik geen opdracht voor.",
                    "script": "", "uitvoer": "", "exit_code": None}
        plek = str(uit.get("plek") or "in").strip()
        plek = plek if plek in ACTIE_PLEKKEN else "in"
        image = str(uit.get("image") or "")
        uitslag = await self._voer_uit(d, plek=plek, image=image, script=script)
        log.infox("Dienstchat voerde iets uit", dienst=d.naam,
                  exitcode=uitslag.get("exit_code"))
        return {
            "antwoord": antwoord,
            "script": script, "plek": plek,
            "image": image or (STANDAARD_HULPIMAGE if plek == "naast" else ""),
            "label": str(uit.get("label") or "").strip()[:80],
            "gevaarlijk": bool(uit.get("gevaarlijk")),
            "uitvoer": uitslag.get("output") or "",
            "exit_code": uitslag.get("exit_code"),
        }

    # ── context en prompts ───────────────────────────────────────────────────

    def _beeld(self, d: ClusterDienst) -> str:
        c = self._cluster(d)
        poorten = ", ".join(str(p) for p in (d.poorten or [])) or "geen"
        gepubliceerd = ", ".join(
            f"{k} → machinepoort {v}" for k, v in (d.poort_map or {}).items()) or "geen"
        sleutels = ", ".join(sorted((d.omgeving or {}).keys())) or "geen"
        return (
            f"DIENST: {d.naam}\n"
            f"IMAGE: {d.image}\n"
            f"STATUS: {d.status}\n"
            f"POORTEN IN DE CONTAINER: {poorten}\n"
            f"NAAR BUITEN: {gepubliceerd}\n"
            f"NAAM OP HET CLUSTERNETWERK: {d.naam} (netwerk {c.netwerk})\n"
            f"OMGEVINGSVARIABELEN (alleen de namen): {sleutels}\n"
            f"CLUSTER: {c.naam}\n")

    def _prompt_knoppen(self, d: ClusterDienst, opdracht: str) -> str:
        huidig = self.lijst(d.id)
        return _KNOPPEN.format(
            beeld=self._beeld(d),
            plekken=_PLEKKEN.format(standaard_image=STANDAARD_HULPIMAGE),
            standaard_image=STANDAARD_HULPIMAGE,
            bestaand=("\n".join(f"- {a.label} ({a.plek})" for a in huidig)
                      or "(nog geen)"),
            opdracht=(f"\nWAT DE GEBRUIKER ERBIJ ZEGT:\n{opdracht.strip()[:2000]}\n"
                      if opdracht.strip() else ""))

    def _prompt_chat(self, d: ClusterDienst, vraag: str,
                     geschiedenis: List[Dict[str, str]]) -> str:
        eerder = "\n".join(
            f"{'JIJ' if (b.get('rol') or '') == 'model' else 'GEBRUIKER'}: "
            f"{str(b.get('tekst') or '')[:1500]}"
            for b in geschiedenis[-8:]) or "(dit is de eerste vraag)"
        knoppen = "\n".join(
            f"- {a.label}: {a.uitleg or '—'}" for a in self.lijst(d.id)) or "(nog geen)"
        return _CHAT.format(beeld=self._beeld(d), knoppen=knoppen, eerder=eerder,
                            vraag=vraag[:4000],
                            plekken=_PLEKKEN.format(standaard_image=STANDAARD_HULPIMAGE))

    async def _vraag(self, prompt: str) -> str:
        from services.agent.claude_cli_provider import ClaudeCliProvider
        from services.lab.verkenner import MODEL_TIJD, VERKENNER_MODEL
        from services.settings_service import get_settings

        s = get_settings(self.db)
        provider = ClaudeCliProvider(
            default_model=VERKENNER_MODEL,
            oauth_token=getattr(s, "oauth_token", None),
            cli_path=s.cli_path or "claude",
            max_turns=1,
            timeout=MODEL_TIJD,
            # Geen gereedschap: het model schrijft een script, het voert niets
            # uit. Dat doet LabX, in de container die erbij hoort, zodat er één
            # plek is waar te zien is wat er gedraaid heeft.
            enable_tool_search=False,
        )
        uit = await provider.chat(prompt)
        return uit.text

    # ── rijen ophalen ────────────────────────────────────────────────────────

    def _dienst(self, dienst_id: int) -> ClusterDienst:
        rij = self.db.get(ClusterDienst, int(dienst_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Dienst niet gevonden")
        return rij

    def _cluster(self, d: ClusterDienst) -> Cluster:
        rij = self.db.get(Cluster, int(d.cluster_id))
        if rij is None:
            raise HTTPException(status_code=404, detail="Cluster niet gevonden")
        return rij

    def _runtime(self, c: Cluster):
        from services.lab.lab_service import LabService

        svc = LabService(self.db)
        return svc._docker_op_host(c.host_id) if c.host_id else svc.runtime


def _lees_json(ruw: str) -> Dict[str, Any]:
    """Het antwoord als JSON. Proza levert een leeg voorstel op, geen crash."""
    tekst = (ruw or "").strip()
    if tekst.startswith("```"):
        tekst = tekst.strip("`")
        tekst = tekst.split("\n", 1)[-1] if "\n" in tekst else tekst
        tekst = tekst.rsplit("```", 1)[0]
    begin, eind = tekst.find("{"), tekst.rfind("}")
    if begin >= 0 and eind > begin:
        try:
            uit = json.loads(tekst[begin:eind + 1])
            if isinstance(uit, dict):
                return uit
        except json.JSONDecodeError:
            pass
    log.infox("Dienstactie kreeg geen JSON terug", lengte=len(tekst))
    return {"antwoord": tekst[:1500], "toelichting": tekst[:600]}


_PLEKKEN = """WAAR EEN SCRIPT DRAAIT -- kies per actie:
- "in": `docker exec` IN de dienst zelf, met `sh -c`. Alleen bruikbaar voor
  gereedschap dat ECHT in dat image zit. Een emulator-image heeft vaak geen
  curl, geen python en geen package manager; gok daar niet op.
- "naast": een wegwerpcontainer op hetzelfde clusternetwerk, die de dienst op
  naam bereikt. Kies hier zelf een image bij ("image"), bijvoorbeeld
  {standaard_image} of een officieel client-image (redis:7-alpine voor
  redis-cli, postgres:16-alpine voor psql). Het script krijgt $DIENST_HOST,
  $DIENST_POORT en $DIENST_POORTEN in zijn omgeving. Heb je een pakket nodig,
  installeer het dan in het script zelf (`pip install --quiet ...`).

Harde regels voor de scripts:
- POSIX sh, geen bashisms. Eén script per actie, het mag meerdere regels zijn.
- Nooit een geheim in het script typen. Heeft de dienst een sleutel in zijn
  omgeving, lees hem dan bij een "in"-actie uit $NAAM; is dat bij een
  "naast"-actie nodig, zeg dat dan in "uitleg" en gebruik de bekende
  standaardwaarde alleen als die openbaar gedocumenteerd is (zoals de vaste
  emulator-sleutel van de Cosmos-emulator).
- Schrijf uitvoer die een mens leest: wat is er gedaan, en hoeveel.
- Zet "gevaarlijk": true bij alles wat data weggooit of overschrijft."""

_KNOPPEN = """Je bedenkt de bedieningsknoppen voor één vaste dienst in een LabX-cluster.

Een vaste dienst is een container die blijft staan terwijl branch-labs komen
en gaan -- een database, een emulator, een broker. Hij draait de code van het
project NIET. De gebruiker wil er handelingen op kunnen doen zonder een
terminal te openen: leegmaken, inhoud bekijken, opnieuw seeden, gezondheid
controleren.

{beeld}
KNOPPEN DIE ER AL ZIJN:
{bestaand}
{opdracht}
{plekken}

Bedenk 3 tot 6 knoppen die voor DIT image werkelijk zin hebben. Liever vier
die werken dan acht die gokken. Laat de knoppen die er al zijn ongemoeid
tenzij je ze beter kunt maken (zelfde label = vervangen).

Antwoord met UITSLUITEND JSON:
{{"toelichting": "één zin over wat je gekozen hebt en waarom",
  "acties": [
    {{"label": "Cosmos leegmaken",
      "uitleg": "Verwijdert alle databases in de emulator.",
      "plek": "naast", "image": "{standaard_image}",
      "gevaarlijk": true,
      "script": "pip install --quiet azure-cosmos\\npython - <<'PY'\\n...\\nPY"}}
  ]}}"""

_CHAT = """Je helpt bij één vaste dienst in een LabX-cluster. De gebruiker vraagt iets,
en jij mag er ook echt iets mee DOEN: geef je een script mee, dan voert LabX
dat meteen uit tegen deze dienst en krijgt de gebruiker de uitvoer te zien.

{beeld}
KNOPPEN DIE ER AL ZIJN:
{knoppen}

HET GESPREK TOT NU TOE:
{eerder}

DE VRAAG:
{vraag}

{plekken}

Is de vraag een vraag om uitleg, laat "script" dan leeg en antwoord gewoon.
Moet er iets gebeuren, zet het dan in "script". Twijfel je of de gebruiker
data wil weggooien, vraag het dan eerst -- zonder script.

Antwoord met UITSLUITEND JSON:
{{"antwoord": "wat je de gebruiker vertelt, in het Nederlands",
  "script": "" of het sh-script,
  "plek": "in" of "naast",
  "image": "alleen bij plek naast",
  "label": "een korte naam, voor als de gebruiker dit als knop wil bewaren",
  "gevaarlijk": true of false}}"""
