"""
services/workflows/engine.py

Het uitvoeren van een workflow: LabX loopt de activiteiten langs, de agent
voert er één tegelijk uit.

**Wat er veranderde.** Vroeger gingen alle stappen in één prompt naar het
model en was de rest aan hem. Daar viel niets op te vertakken (er was tijdens
het draaien geen stap), niets te herhalen, en niets te loggen: een run bewaarde
de eindtekst en verder niets. Nu voert LabX ze stuk voor stuk uit, en dus is er
per activiteit een invoer, een uitvoer, een duur, een prijs en een tak die
genomen is.

**Sessies.** Alle activiteiten van één run delen standaard dezelfde
CLI-sessie: de agent onthoudt wat hij in de vorige stap deed, wat goedkoper is
en meestal is wat je wilt. Een activiteit met `verse_sessie` begint met een
schone lei — precies wat je nodig hebt voor een beoordelaar die niet door zijn
eigen werk beïnvloed mag zijn.

**Lussen en bubbels.** Een `voorelk`-activiteit voert alles wat erin ligt één
keer per element van een lijst uit, netjes achter elkaar, met `item` en
`iteratie` in de context van elke activiteit in die ronde — ook van een `als`.
Dat is wat `herhaal_over` op één activiteit niet kan: daar past maar één stap
in. Een `parallel`-activiteit voert de activiteiten die erin zitten
gelijktijdig uit. Elk daarvan krijgt onvermijdelijk een EIGEN sessie: één
CLI-sessie kan geen twee beurten tegelijk hebben. Heeft het lab meer werkers,
dan worden ze over die containers verdeeld; anders draaien ze naast elkaar in
dezelfde container — twee keer Claude op één pc, met dezelfde afweging als bij
een bundel in een planning. Elke tak krijgt ook zijn eigen databasesessie: één
SQLAlchemy-sessie is niet gemaakt om door taken gedeeld te worden.

**Waarom het in de achtergrond draait.** Een workflow van zeven activiteiten
duurt minuten tot uren. Het HTTP-verzoek dat hem start, geeft daarom meteen
een run-id terug; de voortgang lees je van de run en zijn stappen.

**Waar hij stopt.** Bij een fout zonder `fout`- of `altijd`-verbinding, als de
graaf op is, of bij de bovengrens uit graph.py — het vangnet tegen een
kringetje dat anders een nacht doordraait.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from services.workflows import expressies, graph

log = get_logger(__name__)

# Lopende runs, zodat afbreken meer kan zijn dan een vlag in de database.
_TAKEN: Dict[str, asyncio.Task] = {}
_AFGEBROKEN: set = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── starten ─────────────────────────────────────────────────────────────────

def maak_run(db: Session, workflow: Workflow, *, lab_id: str,
             trigger_type: str = "manual", trigger_ref: Optional[str] = None,
             invoer: Optional[Dict[str, Any]] = None,
             worker_id: Optional[int] = None) -> WorkflowRun:
    """De runregel en de sessie waarin hij gaat draaien.

    De sessie is een gewone thread, zodat je achteraf in het gesprek kunt
    terugkijken (en desnoods doorpraten) — maar hij staat niet in de chatlijst:
    een workflow die elk uur draait zou die anders vullen."""
    from models.thread import Thread

    now = _now_iso()
    thread = Thread(id=str(uuid4()), lab_id=lab_id,
                    title=f"Workflow: {workflow.name}",
                    source="workflow", created_at=now, updated_at=now)
    db.add(thread)
    run = WorkflowRun(id=str(uuid4()), workflow_id=workflow.id, lab_id=lab_id,
                      worker_id=worker_id, trigger_type=trigger_type,
                      trigger_ref=str(trigger_ref) if trigger_ref else None,
                      status="pending", thread_id=thread.id,
                      input_json=invoer or None, created_at=now)
    db.add(run)
    db.commit()
    return run


def start_in_achtergrond(run_id: str) -> bool:
    """De run loslaten als taak. De referentie vasthouden, anders ruimt de
    garbage collector hem halverwege op (zelfde patroon als background_runs)."""
    if run_id in _TAKEN and not _TAKEN[run_id].done():
        return False
    try:
        lus = asyncio.get_running_loop()
    except RuntimeError:
        return False
    taak = lus.create_task(voer_uit(run_id))
    _TAKEN[run_id] = taak
    taak.add_done_callback(lambda _t: _TAKEN.pop(run_id, None))
    return True


def breek_af(run_id: str) -> bool:
    """Afbreken tussen twee activiteiten. De lopende activiteit maakt hij nog
    af — een agent-beurt halverwege afkappen laat het lab in een toestand
    achter waarvan niemand meer weet welke."""
    _AFGEBROKEN.add(run_id)
    return True


# ── de wandeling door de graaf ──────────────────────────────────────────────

async def voer_uit(run_id: str) -> None:
    from db.database import SessionLocal

    db = SessionLocal()
    try:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            return
        workflow = db.get(Workflow, run.workflow_id)
        if workflow is None:
            _rond_af(db, run, "failed", error="De workflow bestaat niet meer")
            return
        nodes, edges = graph.zorg_voor_graaf(workflow)
        start = graph.startnode(nodes, edges)
        if start is None:
            _rond_af(db, run, "failed", error="Deze workflow heeft geen activiteiten")
            return

        run.status = "running"
        run.started_at = _now_iso()
        db.commit()
        log.infox("Workflow gestart", run=run.id, workflow=workflow.name,
                  activiteiten=len(nodes))

        # Een lab gaat vanzelf uit als er een tijd niet in gewerkt is — dat is
        # precies de bedoeling, maar het mag geen reden zijn dat de workflow
        # van vannacht niet draait. Dus zetten we hem aan, en zetten we dat als
        # eerste regel in het verslag: anders lijkt de eerste activiteit
        # onverklaarbaar lang te duren.
        gelukt, melding = await _zorg_dat_het_lab_draait(db, run)
        if not gelukt:
            _rond_af(db, run, "failed", error=melding)
            return

        context: Dict[str, Any] = {"stap": {}, "invoer": run.input_json or {}}
        totalen = {"stappen": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        wachtrij: List[Dict[str, Any]] = [start]
        laatste_uitvoer = ""
        fout: Optional[str] = None
        volgnummer = 0

        while wachtrij:
            if run.id in _AFGEBROKEN:
                _AFGEBROKEN.discard(run.id)
                _rond_af(db, run, "cancelled", output=laatste_uitvoer, totalen=totalen)
                return
            if totalen["stappen"] >= graph.MAX_ACTIVITEITEN_PER_RUN:
                fout = (f"Gestopt na {graph.MAX_ACTIVITEITEN_PER_RUN} activiteiten — "
                        f"loopt deze workflow in een kringetje?")
                break

            node = wachtrij.pop(0)
            volgnummer += 1
            if node["type"] == "voorelk":
                resultaat, tak = await _voer_lus_uit(db, run, node, nodes, edges, context,
                                                     totalen, volgnummer)
                volgnummer += int(resultaat.get("aantal") or 0) * max(
                    1, len(graph.kinderen(nodes, node["id"])))
            elif node["type"] == "parallel":
                resultaat, tak = await _voer_bubbel_uit(db, run, node, nodes, context,
                                                        totalen, volgnummer)
                volgnummer += len(graph.kinderen(nodes, node["id"]))
            else:
                resultaat, tak = await _voer_node_uit(db, run, node, context, totalen,
                                                      volgnummer)
            context["stap"][node["sleutel"]] = resultaat
            if resultaat.get("uitvoer"):
                laatste_uitvoer = resultaat["uitvoer"]

            verder = graph.volgende(nodes, edges, node["id"], tak)
            if resultaat.get("status") == "fout" and not node.get("mag_falen") and not verder:
                fout = f"'{node['naam']}' mislukte: {resultaat.get('error') or 'onbekende fout'}"
                break
            wachtrij.extend(verder)

        _rond_af(db, run, "failed" if fout else "completed",
                 output=laatste_uitvoer, error=fout, totalen=totalen)
    except Exception as exc:  # noqa: BLE001
        log.warningx("Workflow-run viel om", run=run_id, error=str(exc)[:300])
        try:
            run = db.get(WorkflowRun, run_id)
            if run is not None:
                _rond_af(db, run, "failed", error=str(exc)[:2000])
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def _rond_af(db: Session, run: WorkflowRun, status: str, *, output: Optional[str] = None,
             error: Optional[str] = None, totalen: Optional[Dict[str, Any]] = None) -> None:
    run.status = status
    run.output = (output or None)
    run.error = (error or None)
    run.totals_json = totalen or run.totals_json
    run.finished_at = _now_iso()
    db.commit()
    log.infox("Workflow klaar", run=run.id, status=status,
              stappen=(totalen or {}).get("stappen"))
    _meld(db, run, status, error)


def _meld(db: Session, run: WorkflowRun, status: str, error: Optional[str]) -> None:
    """Een workflow die 's nachts draait, mag niet stil mislukken."""
    try:
        from services.notify.notify_service import meld
        wf = db.get(Workflow, run.workflow_id)
        naam = wf.name if wf else f"workflow {run.workflow_id}"
        if status == "failed":
            meld("run_mislukt", f"Workflow '{naam}' mislukt",
                 (error or "Geen verdere melding.")[:1500],
                 {"workflow_run_id": run.id, "lab_id": run.lab_id})
        elif status == "completed" and run.trigger_type != "manual":
            meld("run_klaar", f"Workflow '{naam}' klaar",
                 (run.output or "")[:1500], {"workflow_run_id": run.id})
    except Exception as exc:  # noqa: BLE001
        log.warningx("Melding over workflow overgeslagen", run=run.id, error=str(exc)[:200])


async def _zorg_dat_het_lab_draait(db: Session, run: WorkflowRun) -> Tuple[bool, str]:
    """Het lab aanzetten als het uit staat.

    Het inrichten dat daarna loopt (pakketten terugzetten) wachten we NIET af:
    dat is idempotent en start vanzelf, en de eerste activiteit heeft er
    meestal niets van nodig. Een agent die toch een pakket mist, krijgt dat te
    horen van het pakket zelf — dat is beter dan elke run een paar minuten
    laten wachten op iets dat er meestal al staat."""
    from models.lab import Lab
    from services.lab.lab_service import LabService

    lab = db.get(Lab, run.lab_id) if run.lab_id else None
    if lab is None:
        return False, "Het gekoppelde lab bestaat niet meer"
    if lab.status == "running":
        return True, ""
    stap = _log_stap(db, run, {"id": "__lab__", "naam": f"Lab '{lab.name}' starten",
                               "type": "lab"}, 0, status="running",
                     invoer=f"Het lab stond {lab.status}.")
    try:
        res = await LabService(db).ensure_running(run.lab_id)
    except Exception as exc:  # noqa: BLE001
        _werk_stap_bij(db, stap, status="fout", error=str(exc)[:2000])
        return False, f"Het lab kon niet gestart worden: {str(exc)[:500]}"
    _werk_stap_bij(db, stap, status="ok",
                   uitvoer=("Gestart." if res.get("started") else "Draaide al.")
                           + " Het inrichten loopt op de achtergrond door.")
    log.infox("Lab gestart voor een workflow", run=run.id, lab_id=run.lab_id)
    return True, ""


# ── één activiteit ──────────────────────────────────────────────────────────

async def _voer_node_uit(db: Session, run: WorkflowRun, node: Dict[str, Any],
                         context: Dict[str, Any], totalen: Dict[str, Any],
                         volgnummer: int) -> Tuple[Dict[str, Any], str]:
    """Voert één activiteit uit — zo nodig meerdere keren — en geeft het
    resultaat plus de tak die daarna genomen moet worden."""
    herhaal_over = node.get("herhaal_over")
    if herhaal_over:
        items = expressies.los_op(herhaal_over, context)
        if items is None:
            items = []
        elif not isinstance(items, list):
            items = [items]
        if not items:
            # Niets te doen is niet hetzelfde als mislukt: een lijst die leeg
            # is, is vaak precies het goede nieuws ("geen fouten gevonden").
            _log_stap(db, run, node, volgnummer, status="overgeslagen",
                      invoer=f"herhaal over {herhaal_over}", uitvoer="lege lijst")
            return {"status": "ok", "uitvoer": "", "json": None, "aantal": 0}, "succes"
        resultaten = []
        for i, item in enumerate(items[: node["herhaal_max"]], start=1):
            lokaal = dict(context, item=item, iteratie=i)
            resultaat = await _eenmaal(db, run, node, lokaal, totalen, volgnummer, i, item)
            resultaten.append(resultaat)
            if resultaat.get("status") == "fout" and not node.get("mag_falen"):
                return resultaat, "fout"
        samen = {"status": "ok", "aantal": len(resultaten),
                 "uitvoer": "\n\n".join(r.get("uitvoer") or "" for r in resultaten).strip(),
                 "json": [r.get("json") for r in resultaten if r.get("json") is not None]}
        return samen, "succes"

    herhaal_tot = node.get("herhaal_tot")
    if herhaal_tot:
        laatste: Dict[str, Any] = {}
        for i in range(1, node["herhaal_max"] + 1):
            lokaal = dict(context, iteratie=i)
            laatste = await _eenmaal(db, run, node, lokaal, totalen, volgnummer, i, None)
            lokaal["stap"] = dict(context.get("stap") or {}, **{node["sleutel"]: laatste})
            if expressies.evalueer(herhaal_tot, lokaal):
                return laatste, "succes"
            if laatste.get("status") == "fout" and not node.get("mag_falen"):
                return laatste, "fout"
        laatste["herhaal_grens_bereikt"] = True
        return laatste, "succes" if laatste.get("status") != "fout" else "fout"

    # Binnen een `voorelk` staan de ronde en het element in de context. Zonder
    # ze hier door te geven wordt elke activiteit in de lus gelogd alsof hij
    # één keer gebeurde: in het runverslag is dan niet te zien dát er gelust
    # werd, laat staan bij welk element iets misging.
    resultaat = await _eenmaal(db, run, node, context, totalen, volgnummer,
                               context.get("iteratie"), context.get("item"))
    if node["type"] == "als":
        return resultaat, ("ja" if resultaat.get("waar") else "nee")
    return resultaat, ("fout" if resultaat.get("status") == "fout" else "succes")


async def _voer_lus_uit(db: Session, run: WorkflowRun, node: Dict[str, Any],
                        nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]],
                        context: Dict[str, Any], totalen: Dict[str, Any],
                        volgnummer: int) -> Tuple[Dict[str, Any], str]:
    """Alles in de lus één keer per element van een lijst.

    Dit is wat `herhaal_over` op één activiteit niet kan: binnen de lus liggen
    meerdere activiteiten, en die mogen onderling vertakken. Een `als` op
    `item.complexity` doet dus per element iets anders — precies waarvoor je
    een lus wilt.

    De ronde loopt over de verbindingen BINNEN de groep; wat er ná de lus komt,
    gebeurt één keer. `item` en `iteratie` staan in de context van elke
    activiteit in de ronde, en de uitvoer van een activiteit blijft binnen die
    ronde beschikbaar voor de volgende.
    """
    leden = graph.kinderen(nodes, node["id"])
    bron = node.get("bron") or ""
    rauw = expressies.los_op(bron, context) if bron else None
    if rauw is None:
        items = []
    elif not isinstance(rauw, list):
        items = [rauw]
    else:
        items = rauw
    items = items[: int(node.get("max_items") or 50)]

    if not leden or not items:
        # Een lege lijst is geen fout: "geen incidenten gevonden" is vaak juist
        # het goede nieuws. Maar een lus die nul rondes draait omdat de
        # verwijzing niet bestaat, ziet er precies hetzelfde uit — dus staat
        # erbij wat de bron opleverde.
        if not leden:
            reden = "er ligt geen activiteit in de lus"
        elif not bron:
            reden = "er is geen lijst gekozen om langs te lopen"
        elif rauw is None:
            reden = f"de verwijzing {bron} leverde niets op"
            velden = expressies.beschikbaar_naast(bron, context)
            if velden:
                reden += " — wel aanwezig: " + ", ".join(velden)
        else:
            reden = "de lijst was leeg"
        _log_stap(db, run, node, volgnummer, status="overgeslagen",
                  invoer=f"lijst: {bron or '—'}", uitvoer=reden)
        return {"status": "ok", "uitvoer": "", "aantal": 0, "rondes": []}, "succes"

    # De lus blijft "bezig" zolang de rondes lopen. Stond hij meteen op "ok",
    # dan las het runverslag alsof de lus klaar was voordat er één ronde was
    # gedraaid — en dus alsof er niet gelust werd.
    lusrij = _log_stap(db, run, node, volgnummer, status="running",
                       invoer=f"{len(items)} element(en) uit {bron}\n\n"
                              + "in de lus: " + ", ".join(x["naam"] for x in leden))

    rondes: List[Dict[str, Any]] = []
    mislukt = 0
    nummer = volgnummer
    for index, item in enumerate(items, start=1):
        # Elke ronde begint met een SCHONE kopie van wat er buiten de lus bekend
        # is, plus dit element. Zo lekt ronde 3 niet in ronde 4 — en kan een
        # verwijzing naar `stap.x` binnen de lus nooit stiekem die van vorige
        # ronde zijn.
        lokaal = dict(context, item=item, iteratie=index)
        lokaal["stap"] = dict(context.get("stap") or {})
        start = graph.startnode_in_groep(nodes, edges, node["id"])
        wachtrij = [start] if start else []
        ronde_fout = None
        while wachtrij:
            kind = wachtrij.pop(0)
            nummer += 1
            if nummer - volgnummer > graph.MAX_ACTIVITEITEN_PER_RUN:
                ronde_fout = "te veel activiteiten in deze lus"
                break
            resultaat, tak = await _voer_node_uit(db, run, kind, lokaal, totalen, nummer)
            lokaal["stap"][kind["sleutel"]] = resultaat
            verder = graph.volgende_in_groep(nodes, edges, kind["id"], tak, node["id"])
            if resultaat.get("status") == "fout" and not kind.get("mag_falen") and not verder:
                ronde_fout = f"'{kind['naam']}' mislukte: {resultaat.get('error') or 'onbekend'}"
                break
            wachtrij.extend(verder)
        rondes.append({"iteratie": index, "fout": ronde_fout,
                       "stap": {k: v for k, v in lokaal["stap"].items()}})
        if ronde_fout:
            mislukt += 1
            if node.get("fout_gedrag") != "doorgaan":
                _werk_stap_bij(db, lusrij, status="fout", error=ronde_fout,
                               uitvoer=f"gestopt in ronde {index} van {len(items)}")
                return {"status": "fout", "uitvoer": ronde_fout, "aantal": index,
                        "rondes": rondes, "error": ronde_fout}, "fout"

    # Na de lus is de uitvoer van de LAATSTE ronde beschikbaar onder de sleutels
    # van de activiteiten erin; dat is wat je verwacht als je erachter iets
    # samenvat.
    if rondes:
        context["stap"].update(rondes[-1]["stap"])
    samen = {"status": "fout" if mislukt and node.get("fout_gedrag") != "doorgaan" else "ok",
             "aantal": len(items), "mislukt": mislukt,
             "uitvoer": f"{len(items)} ronde(s) gedaan"
                        + (f", {mislukt} mislukt" if mislukt else "")}
    _werk_stap_bij(db, lusrij, status=samen["status"], uitvoer=samen["uitvoer"],
                   resultaat={"aantal": len(items), "mislukt": mislukt,
                              "bron": bron,
                              "activiteiten": [x["naam"] for x in leden]})
    return samen, ("fout" if samen["status"] == "fout" else "succes")


async def _voer_bubbel_uit(db: Session, run: WorkflowRun, node: Dict[str, Any],
                           nodes: List[Dict[str, Any]], context: Dict[str, Any],
                           totalen: Dict[str, Any],
                           volgnummer: int) -> Tuple[Dict[str, Any], str]:
    """De activiteiten in een bubbel tegelijk uitvoeren.

    Elke tak krijgt zijn eigen databasesessie en zijn eigen CLI-sessie, en waar
    mogelijk een eigen werker. De context die ze meekrijgen is een KOPIE van wat
    er tot nu toe bekend is: takken die tegelijk lopen kunnen elkaars uitvoer
    per definitie niet gebruiken, en doen alsof dat wel kan levert een workflow
    op die soms werkt.
    """
    leden = graph.kinderen(nodes, node["id"])
    if not leden:
        _log_stap(db, run, node, volgnummer, status="overgeslagen",
                  invoer="lege bubbel", uitvoer="geen activiteiten")
        return {"status": "ok", "uitvoer": ""}, "succes"

    werkers = _werkers_voor(db, run, len(leden))
    grens = asyncio.Semaphore(max(1, int(node.get("max_gelijktijdig") or 4)))
    _log_stap(db, run, node, volgnummer, status="ok",
              invoer=f"{len(leden)} activiteiten tegelijk "
                     f"(max {node.get('max_gelijktijdig')})",
              uitvoer=", ".join(x["naam"] for x in leden))

    async def _tak(index: int, kind: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        from db.database import SessionLocal
        eigen_db = SessionLocal()
        try:
            eigen_run = eigen_db.get(WorkflowRun, run.id)
            # Een tak praat nooit met de gedeelde sessie van de run: twee
            # beurten in één CLI-sessie kan niet, en zou de context van beide
            # door elkaar husselen.
            tak_node = dict(kind, verse_sessie=True)
            if werkers:
                eigen_run.worker_id = werkers[index % len(werkers)]
            async with grens:
                resultaat = await _eenmaal(eigen_db, eigen_run, tak_node,
                                           dict(context), totalen,
                                           volgnummer + 1 + index, None, None)
            return kind["sleutel"], resultaat
        except Exception as exc:  # noqa: BLE001
            log.warningx("Tak in een bubbel viel om", run=run.id, node=kind["naam"],
                         error=str(exc)[:200])
            return kind["sleutel"], {"status": "fout", "uitvoer": "", "error": str(exc)[:500]}
        finally:
            eigen_db.close()

    uitkomsten = await asyncio.gather(*[_tak(i, k) for i, k in enumerate(leden)])
    per_sleutel = dict(uitkomsten)
    context["stap"].update(per_sleutel)
    mislukt = [k for k, v in per_sleutel.items() if v.get("status") == "fout"]
    samen = {
        "status": "fout" if (mislukt and node.get("fout_gedrag") != "doorgaan") else "ok",
        "uitvoer": "\n\n".join(f"[{k}] {(v.get('uitvoer') or '').strip()}"
                                for k, v in per_sleutel.items()).strip(),
        "mislukt": mislukt,
        "aantal": len(leden),
    }
    return samen, ("fout" if samen["status"] == "fout" else "succes")


def _werkers_voor(db: Session, run: WorkflowRun, aantal: int) -> List[int]:
    """Werkers om de takken over te verdelen, als het lab er meer heeft.

    Best-effort: geen vrije werker is geen fout maar drukte — dan draaien de
    takken naast elkaar in dezelfde container, wat de gebruiker bij bundels
    ook al als aanvaard risico heeft aangemerkt. Nooit meer werkers dan takken,
    en nooit een werker die niet ingericht is."""
    try:
        from models.lab import Lab
        from services.lab.lab_service import LabService

        lab = db.get(Lab, run.lab_id)
        if lab is None:
            return []
        svc = LabService(db)
        bezet = svc.bezette_werkers(lab.id)
        vrij = [w.id for w in svc.claimbare_werkers(lab) if w.id not in bezet]
        return vrij[:aantal]
    except Exception as exc:  # noqa: BLE001
        log.warningx("Werkers verdelen overgeslagen", run=run.id, error=str(exc)[:200])
        return []


async def _eenmaal(db: Session, run: WorkflowRun, node: Dict[str, Any],
                   context: Dict[str, Any], totalen: Dict[str, Any],
                   volgnummer: int, iteratie: Optional[int],
                   item: Any) -> Dict[str, Any]:
    soort = node["type"]
    begin = time.monotonic()
    totalen["stappen"] = totalen.get("stappen", 0) + 1

    if soort == "als":
        conditie = node.get("conditie") or {}
        # Niet alleen de uitkomst maar ook de waarden waarop hij besloot: een
        # `als` die achteraf alleen "nee" zegt, is niet na te rekenen.
        waar, uitleg = expressies.evalueer_uitgelegd(conditie, context)
        _log_stap(db, run, node, volgnummer, status="ok", iteratie=iteratie, item=item,
                  invoer=expressies.beschrijf_uitkomst(uitleg),
                  uitvoer="ja" if waar else "nee", tak="ja" if waar else "nee",
                  resultaat=uitleg,
                  duur_ms=int((time.monotonic() - begin) * 1000))
        return {"status": "ok", "waar": waar, "uitvoer": "ja" if waar else "nee",
                "uitleg": uitleg}

    if soort == "wacht":
        await asyncio.sleep(int(node.get("seconden") or 30))
        _log_stap(db, run, node, volgnummer, status="ok", iteratie=iteratie, item=item,
                  invoer=f"{node.get('seconden')} seconden", uitvoer="gewacht",
                  duur_ms=int((time.monotonic() - begin) * 1000))
        return {"status": "ok", "uitvoer": ""}

    if soort == "shell":
        commando = expressies.vul_in(node.get("commando") or "", context)
        rij = _log_stap(db, run, node, volgnummer, status="running", iteratie=iteratie,
                        item=item, invoer=commando)
        try:
            from services.mcp.tool_execution_service import ToolExecutionService
            res = await ToolExecutionService(db).execute_builtin_shell(
                lab_id=run.lab_id, command=commando,
                timeout=float(node.get("timeout") or 120), worker_id=run.worker_id,
                intent="workflow")
            uitvoer = str(res.get("output") or "")
            code = int(res.get("exit_code") or 0)
            status = "ok" if code == 0 else "fout"
            _werk_stap_bij(db, rij, status=status, uitvoer=uitvoer, exit_code=code,
                           duur_ms=int((time.monotonic() - begin) * 1000))
            return {"status": status, "uitvoer": uitvoer, "exit_code": code, "json": None}
        except Exception as exc:  # noqa: BLE001
            _werk_stap_bij(db, rij, status="fout", error=str(exc)[:2000],
                           duur_ms=int((time.monotonic() - begin) * 1000))
            return {"status": "fout", "uitvoer": "", "error": str(exc)[:500]}

    # agent
    opdracht = expressies.vul_in(node.get("prompt") or "", context)
    rol = (node.get("rol") or "").strip()
    if rol:
        opdracht = f"{rol}\n\n{opdracht}"
    rij = _log_stap(db, run, node, volgnummer, status="running", iteratie=iteratie,
                    item=item, invoer=opdracht)
    antwoord, stappen, gebruik, sessie, fout = await _agent_beurt(
        db, run, node, opdracht)
    duur = int((time.monotonic() - begin) * 1000)
    if fout:
        _werk_stap_bij(db, rij, status="fout", error=fout, stappen=stappen, duur_ms=duur)
        return {"status": "fout", "uitvoer": antwoord, "error": fout, "json": None}

    gestructureerd = _als_json(antwoord) if node.get("json_schema") else None
    if sessie and not node.get("verse_sessie"):
        run.cli_session_id = sessie
    totalen["input_tokens"] += int(gebruik.get("input_tokens") or 0)
    totalen["output_tokens"] += int(gebruik.get("output_tokens") or 0)
    totalen["cost_usd"] = round(totalen.get("cost_usd", 0.0)
                                + float(gebruik.get("cost_usd") or 0.0), 4)
    _werk_stap_bij(db, rij, status="ok", uitvoer=antwoord, stappen=stappen,
                   resultaat=gestructureerd, duur_ms=duur,
                   input_tokens=gebruik.get("input_tokens"),
                   output_tokens=gebruik.get("output_tokens"),
                   cost_usd=gebruik.get("cost_usd"))
    return {"status": "ok", "uitvoer": antwoord, "json": gestructureerd}


async def _agent_beurt(db: Session, run: WorkflowRun, node: Dict[str, Any],
                       opdracht: str):
    """Eén beurt van de agent, met alles wat eruit komt: het antwoord, de
    tool-aanroepen (de redenatie), het verbruik en de sessie."""
    from services.agent.chat_agent import ChatAgent

    antwoord, stappen, gebruik, sessie, fout = "", [], {}, None, None
    hervat = None if node.get("verse_sessie") else run.cli_session_id
    try:
        async for ev in ChatAgent(db).run_stream_events(
                lab_id=run.lab_id, user_input=opdracht,
                model=node.get("model"), resume_session_id=hervat,
                json_schema=node.get("json_schema"),
                thread_id=run.thread_id, is_background=True,
                lab_worker_id=run.worker_id):
            soort = ev.get("kind")
            if soort == "answer":
                antwoord = ev.get("text") or ""
            elif soort in ("thinking", "tool"):
                stappen.append(ev)
            elif soort == "session":
                sessie = ev.get("id")
            elif soort == "usage":
                gebruik = ev
    except Exception as exc:  # noqa: BLE001
        fout = str(exc)[:2000]
    return antwoord, stappen, gebruik, sessie, fout


def _als_json(tekst: str) -> Optional[Any]:
    """Gestructureerde uitvoer uit het antwoord halen. Mislukt dat, dan is het
    gewoon tekst — geen reden om de activiteit te laten falen."""
    ruw = (tekst or "").strip()
    if ruw.startswith("```"):
        ruw = ruw.strip("`")
        ruw = ruw.split("\n", 1)[1] if "\n" in ruw else ruw
    try:
        return json.loads(ruw)
    except Exception:  # noqa: BLE001
        return None


# ── het spoor ───────────────────────────────────────────────────────────────

def _log_stap(db: Session, run: WorkflowRun, node: Dict[str, Any], volgnummer: int, *,
              status: str, invoer: Optional[str] = None, uitvoer: Optional[str] = None,
              iteratie: Optional[int] = None, item: Any = None,
              tak: Optional[str] = None, duur_ms: Optional[int] = None,
              resultaat: Any = None) -> WorkflowRunStep:
    rij = WorkflowRunStep(
        run_id=run.id, node_id=node["id"], naam=node["naam"], soort=node["type"],
        volgnummer=volgnummer, iteratie=iteratie,
        item=(json.dumps(item, ensure_ascii=False)[:2000] if item is not None else None),
        status=status, invoer=(invoer or None)[:20000] if invoer else None,
        uitvoer=(uitvoer or None), tak=tak, duur_ms=duur_ms,
        resultaat_json=(resultaat if isinstance(resultaat, dict) else None),
        created_at=_now_iso(),
        finished_at=_now_iso() if status != "running" else None)
    db.add(rij)
    db.commit()
    return rij


def _werk_stap_bij(db: Session, rij: WorkflowRunStep, *, status: str,
                   uitvoer: Optional[str] = None, error: Optional[str] = None,
                   stappen: Optional[List[Dict[str, Any]]] = None,
                   resultaat: Any = None, exit_code: Optional[int] = None,
                   duur_ms: Optional[int] = None,
                   input_tokens: Optional[int] = None,
                   output_tokens: Optional[int] = None,
                   cost_usd: Optional[float] = None) -> None:
    rij.status = status
    if uitvoer is not None:
        rij.uitvoer = uitvoer
    if error is not None:
        rij.error = error
    if stappen is not None:
        rij.stappen_json = stappen
    if resultaat is not None:
        rij.resultaat_json = resultaat if isinstance(resultaat, dict) else {"waarde": resultaat}
    if exit_code is not None:
        rij.exit_code = exit_code
    rij.duur_ms = duur_ms
    rij.input_tokens = input_tokens
    rij.output_tokens = output_tokens
    rij.cost_usd = cost_usd
    rij.finished_at = _now_iso()
    db.commit()
