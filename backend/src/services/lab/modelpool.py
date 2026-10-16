"""services/lab/modelpool.py — meer dan één plek waar het guardmodel draait.

De data-guard vraagt een klein lokaal model om een tweede mening. Dat model
draaide op één plek: de server. Diezelfde server draait ook alle labs, dus
een drukke guard-ronde ging ten koste van het werk zelf -- en als het model
bezig was, stond de rest te wachten.

Machines in je netwerk staan er intussen bij. Die kunnen dit prima doen, en
daarom kun je per machine zeggen HOEVEEL modelcontainers daar mogen draaien.
Bij een vraag kiest LabX er een die vrij is.

DRIE KEUZES DIE DE REST VERKLAREN:

- **Jij zegt hoeveel, niet LabX.** Een model vreet geheugen en CPU, en jij
  weet wat er op iemands werkcomputer nog bij kan. Raden op andermans machine
  is precies wat je niet wilt.

- **Vrij = de minste verzoeken onderweg.** Niet "idle of niet": Ollama kan
  meerdere verzoeken tegelijk aan, maar niet oneindig. Een teller per runner
  is eerlijker dan een vlaggetje en voorkomt dat alles naar dezelfde gaat.

- **Een runner die niet antwoordt, valt uit de pool.** Niet "foutmelding en
  wachten": de guard valt bij gebrek aan model terug op de regellaag (zie
  data_guard_llm), en dat blijft zo. Een trage runner mag nooit een
  commando ophouden.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.lab_host import LabHost
from models.model_runner import ModelRunner

log = get_logger(__name__)

#: Het image waarin het model draait. Hetzelfde als in de compose van de
#: server, zodat er maar één soort runner bestaat.
OLLAMA_IMAGE = "ollama/ollama:latest"
#: Poortbereik op de loopback van de doelmachine. Hoog en ongebruikelijk,
#: zodat we niets overnemen wat daar al luistert.
_POORT_BASIS = 21434
#: Hoeveel guard-vragen er tegelijk naar ÉÉN MACHINE mogen, ongeacht hoeveel
#: runners daar staan.
#:
#: Per runner tellen was fout, en meetbaar fout. Een model is CPU-werk: vier
#: runners op één machine delen dezelfde kernen, dus vier vragen tegelijk
#: maken elke vraag ongeveer vier keer trager zonder dat er meer af komt. En
#: elke runner die werk krijgt laadt zijn EIGEN kopie van het model in het
#: geheugen -- op een MacBook gemeten: 1,44 GB voor één, terwijl de drie
#: ongebruikte runners op 17 MB stonden. Vier actief is dus bijna 6 GB.
#:
#: Meer containers op één machine is daarom geen extra capaciteit. Extra
#: capaciteit komt van extra MACHINES, en dit getal zorgt dat de pool dat ook
#: zo behandelt.
MAX_GELIJKTIJDIG_PER_MACHINE = 2

#: Verzoeken die NU onderweg zijn, per runner-id. In het geheugen en niet in
#: de database: dit is een momentopname die alleen voor dit proces geldt, en
#: een herstart hoort hem te vergeten.
_ONDERWEG: Dict[int, int] = {}


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelPool:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── wat er zou moeten draaien ────────────────────────────────────────────

    def gewenst(self) -> List[Tuple[Optional[int], int]]:
        """(host_id, aantal) voor elke machine die runners mag draaien."""
        return [(h.id, int(h.ollama_runners or 0))
                for h in self.db.query(LabHost)
                .filter(LabHost.status == "gekoppeld").all()
                if (h.ollama_runners or 0) > 0]

    def runners(self, host_id: Optional[int] = None) -> List[ModelRunner]:
        q = self.db.query(ModelRunner).filter(ModelRunner.status != "gestopt")
        if host_id is not None:
            q = q.filter(ModelRunner.host_id == host_id)
        return q.order_by(ModelRunner.id).all()

    # ── de pool op orde brengen ──────────────────────────────────────────────

    async def verzoen(self) -> Dict[str, int]:
        """Starten wat er te weinig is, weghalen wat te veel is.

        Draait periodiek. Een machine die nu niet bereikbaar is, slaan we
        over -- hetzelfde uitgangspunt als bij labs: niet kunnen kijken is
        iets anders dan niets vinden.
        """
        from services.lab.lab_service import LabService

        lab_svc = LabService(self.db)
        bij, weg = 0, 0
        gewenst = dict(self.gewenst())

        for host in self.db.query(LabHost).filter(LabHost.status == "gekoppeld").all():
            doel = gewenst.get(host.id, 0)
            huidig = [r for r in self.runners(host.id) if r.status != "fout"]
            if len(huidig) == doel:
                continue
            try:
                rt = lab_svc._docker_op_host(host.id)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Modelpool overgeslagen (machine niet beschikbaar)",
                             host=host.naam, error=str(exc)[:160])
                continue

            while len(huidig) < doel:
                r = await self._start_runner(rt, host)
                if r is None:
                    break
                huidig.append(r)
                bij += 1
            while len(huidig) > doel:
                await self._stop_runner(rt, huidig.pop())
                weg += 1

        if bij or weg:
            self.db.commit()
            log.infox("Modelpool bijgewerkt", erbij=bij, eraf=weg)
        return {"erbij": bij, "eraf": weg}

    async def _start_runner(self, rt: Any, host: LabHost) -> Optional[ModelRunner]:
        bezet = {r.verre_poort for r in self.runners() if r.verre_poort}
        poort = next(p for p in range(_POORT_BASIS, _POORT_BASIS + 50)
                     if p not in bezet)
        volgnr = len(self.runners(host.id)) + 1
        naam = f"labx-model-{host.id}-{volgnr}"
        rij = ModelRunner(host_id=host.id, naam=naam, verre_poort=poort,
                          status="startend", created_at=_nu(), updated_at=_nu())
        self.db.add(rij)
        self.db.flush()
        try:
            rij.container_id = await rt.run_model_container(
                naam=naam, image=OLLAMA_IMAGE, volume=f"labx_model_{host.id}",
                verre_poort=poort)
            log.infox("Modelrunner gestart", naam=naam, machine=host.naam)
        except Exception as exc:  # noqa: BLE001
            rij.status = "fout"
            rij.fout = str(exc)[:500]
            log.warningx("Modelrunner starten mislukt", naam=naam,
                         machine=host.naam, error=str(exc)[:200])
            return None
        rij.updated_at = _nu()
        return rij

    async def _stop_runner(self, rt: Any, rij: ModelRunner) -> None:
        if rij.container_id:
            try:
                await rt.remove(rij.container_id, timeout=120)
            except Exception as exc:  # noqa: BLE001
                log.warningx("Modelrunner opruimen mislukt", naam=rij.naam,
                             error=str(exc)[:200])
        rij.status = "gestopt"
        rij.updated_at = _nu()

    # ── draait hij, en kent hij het model? ───────────────────────────────────

    async def controleer(self) -> Dict[str, int]:
        """Elke runner nalopen: antwoordt hij, en staat het model erop?

        Het model moet per machine worden opgehaald -- een paar honderd MB tot
        een paar GB, eenmalig, in een volume dat een herstart overleeft. Dat
        is de prijs van spreiding, en hij wordt één keer betaald.

        Een runner die nog aan het ophalen is blijft `startend`. Hij doet dan
        nog niet mee: een vraag erheen sturen zou stranden op "model not
        found", en dat is erger dan hem even overslaan.
        """
        import httpx

        from services.lab.poort_tunnel import tunnels

        model = self._modelnaam()
        hosts = {h.id: h for h in self.db.query(LabHost).all()}
        klaar = bezig = stuk = 0

        for r in self.runners():
            host = hosts.get(r.host_id)
            if host is None or not host.docker_host or not r.verre_poort:
                continue
            try:
                lokaal = await tunnels.lokale_poort(host.docker_host, int(r.verre_poort))
                async with httpx.AsyncClient(timeout=10) as c:
                    tags = (await c.get(f"http://127.0.0.1:{lokaal}/api/tags")).json()
            except Exception as exc:  # noqa: BLE001
                r.status, r.fout = "startend", f"Nog niet bereikbaar: {str(exc)[:200]}"
                r.updated_at = _nu()
                bezig += 1
                continue

            namen = {m.get("name", "") for m in (tags.get("models") or [])}
            if any(n == model or n.startswith(model + ":") for n in namen):
                if r.status != "klaar":
                    log.infox("Modelrunner klaar", naam=r.naam,
                              machine=host.naam, model=model)
                r.status, r.fout = "klaar", None
                klaar += 1
            else:
                # Ophalen, en NIET wachten tot het klaar is: dit duurt minuten
                # en de scheduler heeft meer te doen. De volgende ronde ziet
                # hem vanzelf in /api/tags verschijnen.
                _start_pull(f"http://127.0.0.1:{lokaal}", model, r.naam,
                            host_id=r.host_id)
                r.status = "startend"
                r.fout = f"Model {model} wordt opgehaald"
                bezig += 1
            r.updated_at = _nu()

        self.db.commit()
        return {"klaar": klaar, "bezig": bezig, "fout": stuk}

    def _modelnaam(self) -> str:
        from services.lab.data_guard_llm import _model

        return _model()

    # ── een runner kiezen voor één vraag ─────────────────────────────────────

    async def kies(self) -> Optional[Tuple[int, str]]:
        """(runner_id, url) van de minst bezette bruikbare runner, of None.

        None is een normaal antwoord en geen fout: dan is er geen runner in je
        netwerk en gebruikt de guard de url uit de instellingen, precies zoals
        altijd.
        """
        from services.lab.poort_tunnel import tunnels

        kandidaten = [r for r in self.runners()
                      if r.status in ("klaar", "startend") and r.verre_poort]
        if not kandidaten:
            return None

        hosts = {h.id: h for h in self.db.query(LabHost).all()}
        kandidaten = [r for r in kandidaten
                      if r.host_id in hosts
                      and hosts[r.host_id].status == "gekoppeld"
                      and hosts[r.host_id].docker_host]
        if not kandidaten:
            return None

        # Per MACHINE tellen, niet per runner: de runners op één machine delen
        # dezelfde kernen, dus acht "vrije plekken" op vier containers is een
        # belofte die de machine niet kan waarmaken.
        per_machine: Dict[int, int] = {}
        for r in kandidaten:
            per_machine[r.host_id] = per_machine.get(r.host_id, 0) + _ONDERWEG.get(r.id, 0)

        ruimte = [r for r in kandidaten
                  if per_machine.get(r.host_id, 0) < MAX_GELIJKTIJDIG_PER_MACHINE]
        if not ruimte:
            # Alles vol. Dan niet in de rij gaan staan: de guard valt terug op
            # de server-url, en die mag best even wachten.
            return None
        # De minst belaste MACHINE eerst, en daarbinnen de minst belaste
        # runner. Zo spreidt het werk over machines en niet over containers
        # die toch dezelfde processor delen.
        ruimte.sort(key=lambda r: (per_machine.get(r.host_id, 0), _ONDERWEG.get(r.id, 0)))
        gekozen = ruimte[0]

        try:
            lokaal = await tunnels.lokale_poort(
                hosts[gekozen.host_id].docker_host, int(gekozen.verre_poort))
        except Exception as exc:  # noqa: BLE001
            gekozen.status = "fout"
            gekozen.fout = f"Geen tunnel: {str(exc)[:300]}"
            gekozen.updated_at = _nu()
            self.db.commit()
            return None
        # Hier bijwerken en niet na afloop: "laatst gebruikt" moet zeggen
        # wanneer er voor het laatst werk heen ging. Pas bij een geslaagd
        # antwoord tellen zou een runner die steeds in een time-out loopt er
        # ongebruikt uit laten zien, terwijl hij juist te veel krijgt.
        gekozen.laatst_gebruikt = _nu()
        gekozen.updated_at = _nu()
        self.db.commit()
        return gekozen.id, f"http://127.0.0.1:{lokaal}"

    @staticmethod
    def bezet(runner_id: int) -> None:
        _ONDERWEG[runner_id] = _ONDERWEG.get(runner_id, 0) + 1

    @staticmethod
    def vrij(runner_id: int) -> None:
        if runner_id in _ONDERWEG:
            _ONDERWEG[runner_id] = max(0, _ONDERWEG[runner_id] - 1)

    def markeer(self, runner_id: int, *, klaar: bool, fout: str = "") -> None:
        rij = self.db.get(ModelRunner, runner_id)
        if rij is None:
            return
        rij.status = "klaar" if klaar else "fout"
        rij.fout = None if klaar else fout[:500]
        if klaar:
            rij.laatst_gebruikt = _nu()
        rij.updated_at = _nu()
        self.db.commit()

    # ── voor het scherm ──────────────────────────────────────────────────────

    def overzicht(self) -> List[Dict[str, Any]]:
        hosts = {h.id: h.naam for h in self.db.query(LabHost).all()}
        return [{
            "id": r.id, "naam": r.naam,
            "machine": hosts.get(r.host_id) or "deze server",
            "status": r.status, "fout": r.fout,
            "onderweg": _ONDERWEG.get(r.id, 0),
            "laatst_gebruikt": r.laatst_gebruikt,
        } for r in self.runners()]


#: Welke pulls er lopen. Gesleuteld op MACHINE en niet op runner: de runners
#: op één machine delen één volume, dus vier processen die tegelijk in
#: dezelfde modelopslag schrijven is vragen om een halve download. Eén haalt
#: hem op, de rest ziet hem vanzelf verschijnen.
_PULLS: Dict[Any, Any] = {}


def _start_pull(url: str, model: str, naam: str, host_id: Any = None) -> None:
    """Het model op deze machine laten ophalen, op de achtergrond."""
    sleutel = host_id if host_id is not None else naam
    if _PULLS.get(sleutel) and not _PULLS[sleutel].done():
        return

    async def _werk() -> None:
        import httpx

        log.infox("Model ophalen op runner", runner=naam, model=model)
        try:
            # Ruim budget: een paar GB over een netwerkverbinding naar een
            # machine die ook nog van alles doet.
            async with httpx.AsyncClient(timeout=3600) as c:
                r = await c.post(f"{url}/api/pull", json={"model": model})
                r.raise_for_status()
            log.infox("Model opgehaald", runner=naam, model=model)
        except Exception as exc:  # noqa: BLE001
            log.warningx("Model ophalen mislukt", runner=naam, error=str(exc)[:200])

    try:
        _PULLS[sleutel] = asyncio.get_running_loop().create_task(_werk())
    except RuntimeError:
        pass
