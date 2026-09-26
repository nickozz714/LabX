"""
services/audit/activiteit.py

Wat er in een lab gebeurd is: per beurt van de agent één regel, met het model
dat hem deed, wat erin ging, wat eruit kwam en welke acties hij onderweg
ondernam.

**Waarom dit naast het guard-spoor staat.** Het bestaande audit-scherm gaat
over de data-guard: welke uitvoer is gemaskeerd en waarom. Dat is een scherp
mes voor één vraag, en het antwoord op een andere vraag stond nergens: wat
heeft dit lab vandaag eigenlijk gedaan? Die vraag stel je per klant, hij gaat
over beurten en niet over bytes, en je wilt hem zowel op de dag als op de maand
kunnen stellen.

**Waar het vandaan komt.** Twee bronnen, want beurten ontstaan op twee manieren
en dat hoort in één lijst te eindigen:

- `background_runs` — elke chatbeurt, elke achtergrondtaak en elk ticket dat
  een agent oppakt. De tool-aanroepen en het verbruik staan in `steps`, in
  dezelfde vorm als de chat ze toont.
- `workflow_run_steps` — een activiteit uit een workflow. Die loopt langs
  dezelfde agent maar wordt door de motor zelf geboekt, met per activiteit al
  een invoer, een uitvoer en een prijs.

**Wat een "actie" is.** Een tool-aanroep. `lab__shell_exec` is er een, een
MCP-tool van Zoho is er een, `WebSearch` is er een. Dat is het niveau waarop
je wil kunnen zien wat er gebeurde — niet de losse tekens van een antwoord, en
niet zo grof als "er draaide iets".
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from models.background_run import BackgroundRun
from models.lab import Lab
from models.thread import Thread
from models.workflow import Workflow, WorkflowRun, WorkflowRunStep

# Meer dan dit in één keer terugsturen maakt het scherm traag en is niemand
# aan het lezen; er is een `offset` om verder te bladeren.
MAX_PER_PAGINA = 200


def _iso(waarde: Any) -> str:
    return str(waarde or "")


def _stappen(ruw: Any) -> List[Dict[str, Any]]:
    if isinstance(ruw, list):
        return [x for x in ruw if isinstance(x, dict)]
    if isinstance(ruw, str) and ruw.strip():
        try:
            geladen = json.loads(ruw)
            return [x for x in geladen if isinstance(x, dict)]
        except ValueError:
            return []
    return []


def _acties_uit(stappen: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """De tool-aanroepen uit een reeks chat-gebeurtenissen, geteld per naam.

    Geteld en niet uitgeschreven: een run die twintig keer `lab__shell_exec`
    doet, levert anders twintig regels op waar je niets aan ziet. De losse
    aanroepen blijven in het detail staan."""
    telling = Counter()
    for s in stappen:
        if s.get("kind") == "tool" and s.get("name"):
            telling[str(s["name"])] += 1
    acties = [{"naam": naam, "aantal": aantal} for naam, aantal in telling.most_common()]
    return acties, sum(telling.values())


def _knip(tekst: Any, lengte: int = 4000) -> Optional[str]:
    t = str(tekst or "")
    if not t:
        return None
    return t if len(t) <= lengte else t[:lengte] + f"\n… ({len(t) - lengte} tekens meer)"


def _verloop_uit(stappen: List[Dict[str, Any]], max_regels: int = 120) -> List[Dict[str, Any]]:
    """De beurt van begin tot eind: wat de agent dacht en wat hij opvroeg.

    De telling per tool zegt DAT er een shell-commando was; hier staat welk
    commando, met welke parameters, en wat hij ertussenin overwoog. Dat is waar
    de vraag "welke data is opgevraagd" beantwoord wordt — bij de invoer van de
    aanroep, niet bij de naam ervan.

    Afgekapt op een aantal regels: een beurt van een uur heeft er honderden, en
    het scherm is geen logbestand."""
    uit: List[Dict[str, Any]] = []
    for s in stappen:
        soort = s.get("kind")
        if soort == "thinking":
            tekst = str(s.get("text") or "").strip()
            if tekst:
                uit.append({"soort": "denken", "tekst": _knip(tekst, 1500)})
        elif soort == "tool" and s.get("name"):
            uit.append({"soort": "actie", "naam": str(s["name"]),
                        "invoer": _knip(_leesbaar(s.get("input")), 1200)})
        if len(uit) >= max_regels:
            uit.append({"soort": "afgekapt",
                        "tekst": f"… meer dan {max_regels} stappen; de rest staat in het gesprek"})
            break
    return uit


def _leesbaar(invoer: Any) -> str:
    """De argumenten van een tool-aanroep als tekst.

    Een shell-commando is het interessantst als kaal commando en niet als
    `{"command": "..."}` — dat is precies het veld waar je naar kijkt."""
    if invoer is None:
        return ""
    if isinstance(invoer, str):
        return invoer
    if isinstance(invoer, dict):
        for sleutel in ("command", "commando", "query", "prompt", "path", "url"):
            if isinstance(invoer.get(sleutel), str) and invoer[sleutel].strip():
                rest = {k: v for k, v in invoer.items() if k != sleutel}
                staart = f"   ({json.dumps(rest, ensure_ascii=False)})" if rest else ""
                return f"{invoer[sleutel]}{staart}"
    try:
        return json.dumps(invoer, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(invoer)


def _verbruik_uit(stappen: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Tokens, kosten en duur staan als `usage`-gebeurtenis in dezelfde lijst.

    Opgeteld en niet "de laatste": een beurt die door een limiet in tweeën
    viel, heeft er twee."""
    uit = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for s in stappen:
        if s.get("kind") != "usage":
            continue
        uit["input_tokens"] += int(s.get("input_tokens") or 0)
        uit["output_tokens"] += int(s.get("output_tokens") or 0)
        uit["cost_usd"] += float(s.get("cost_usd") or 0.0)
    uit["cost_usd"] = round(uit["cost_usd"], 4)
    return uit


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── de lijst ────────────────────────────────────────────────────────────

    def gebeurtenissen(self, *, lab_id: Optional[str] = None,
                       van: Optional[str] = None, tot: Optional[str] = None,
                       bron: Optional[str] = None,
                       limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Beurten uit beide bronnen, op tijd door elkaar.

        Door elkaar en niet in twee lijsten: wie wil weten wat een lab deed,
        vraagt niet eerst of het een chat of een workflow was."""
        rijen: List[Dict[str, Any]] = []
        if bron in (None, "", "agent"):
            rijen += self._uit_runs(lab_id, van, tot)
        if bron in (None, "", "workflow"):
            rijen += self._uit_workflows(lab_id, van, tot)
        rijen.sort(key=lambda r: r["ts"], reverse=True)
        totaal = len(rijen)
        limit = max(1, min(int(limit or 50), MAX_PER_PAGINA))
        return {"totaal": totaal, "items": rijen[offset:offset + limit]}

    def _labnamen(self) -> Dict[str, str]:
        return {l.id: l.name for l in self.db.query(Lab.id, Lab.name).all()}

    def _uit_runs(self, lab_id, van, tot) -> List[Dict[str, Any]]:
        namen = self._labnamen()
        q = (self.db.query(BackgroundRun, Thread.lab_id, Thread.title)
             .join(Thread, Thread.id == BackgroundRun.thread_id))
        if lab_id:
            q = q.filter(Thread.lab_id == lab_id)
        if van:
            q = q.filter(BackgroundRun.created_at >= van)
        if tot:
            q = q.filter(BackgroundRun.created_at <= tot)
        uit = []
        for run, lab, titel in q.order_by(BackgroundRun.created_at.desc()).limit(500).all():
            stappen = _stappen(run.steps)
            acties, aantal = _acties_uit(stappen)
            uit.append({
                "id": run.id,
                "bron": "taak" if run.mode == "background" else "chat",
                "ts": _iso(run.started_at or run.created_at),
                "lab_id": lab,
                "lab_naam": namen.get(lab or "", "—"),
                "werker": run.lab_worker_id,
                "model": run.model or "—",
                "titel": titel or "Gesprek",
                "status": run.status,
                "invoer": _knip(run.prompt),
                "uitvoer": _knip(run.answer or run.error),
                "acties": acties,
                "acties_totaal": aantal,
                "verloop": _verloop_uit(stappen),
                "duur_ms": _duur(run.started_at, run.finished_at),
                **_verbruik_uit(stappen),
                "thread_id": run.thread_id,
            })
        return uit

    def _uit_workflows(self, lab_id, van, tot) -> List[Dict[str, Any]]:
        namen = self._labnamen()
        wfnamen: Dict[int, str] = {}
        # Welk model een activiteit gebruikte staat op de node; staat daar
        # niets, dan draaide hij op de standaard van het lab. Zonder deze
        # opzoeking zou er "—" staan bij precies de vraag die gesteld wordt.
        modellen: Dict[Tuple[int, str], str] = {}
        for w in self.db.query(Workflow).all():
            wfnamen[w.id] = w.name
            for n in (w.nodes_json or []):
                if isinstance(n, dict) and n.get("model"):
                    modellen[(w.id, str(n.get("id")))] = str(n["model"])
        labmodellen = {l.id: (l.model or None)
                       for l in self.db.query(Lab.id, Lab.model).all()}
        q = (self.db.query(WorkflowRunStep, WorkflowRun)
             .join(WorkflowRun, WorkflowRun.id == WorkflowRunStep.run_id))
        if lab_id:
            q = q.filter(WorkflowRun.lab_id == lab_id)
        if van:
            q = q.filter(WorkflowRunStep.created_at >= van)
        if tot:
            q = q.filter(WorkflowRunStep.created_at <= tot)
        uit = []
        for stap, run in q.order_by(WorkflowRunStep.created_at.desc()).limit(500).all():
            stappen = _stappen(stap.stappen_json)
            acties, aantal = _acties_uit(stappen)
            uit.append({
                "id": f"wf-{stap.id}",
                "bron": "workflow",
                "ts": _iso(stap.created_at),
                "lab_id": run.lab_id,
                "lab_naam": namen.get(run.lab_id or "", "—"),
                "werker": run.worker_id,
                "model": (modellen.get((run.workflow_id, stap.node_id))
                          or labmodellen.get(run.lab_id or "") or "standaard"),
                "titel": f"{wfnamen.get(run.workflow_id, 'Workflow')} · {stap.naam}",
                "status": stap.status,
                "invoer": _knip(stap.invoer),
                "uitvoer": _knip(stap.uitvoer or stap.error),
                "acties": acties,
                "acties_totaal": aantal,
                "verloop": _verloop_uit(stappen),
                "duur_ms": stap.duur_ms,
                "input_tokens": int(stap.input_tokens or 0),
                "output_tokens": int(stap.output_tokens or 0),
                "cost_usd": round(float(stap.cost_usd or 0.0), 4),
                "iteratie": stap.iteratie,
                "run_id": stap.run_id,
            })
        return uit

    # ── de staafjes ─────────────────────────────────────────────────────────

    def aggregatie(self, *, lab_id: Optional[str] = None, periode: str = "dag",
                   aantal: int = 14) -> Dict[str, Any]:
        """Per dag, week of maand: hoeveel beurten, welke acties, wat het kostte.

        De emmers staan vast en worden vooruit gevuld, ook als er niets
        gebeurde: een gat in de reeks hoort als leeg vakje te verschijnen en
        niet als een dag die ontbreekt — anders liegt de grafiek over het
        tempo."""
        periode = periode if periode in ("dag", "week", "maand") else "dag"
        aantal = max(1, min(int(aantal or 14), 60))
        nu = datetime.now(timezone.utc)
        emmers = _emmers(nu, periode, aantal)
        van = emmers[0]["van"]

        alles = self.gebeurtenissen(lab_id=lab_id, van=van.isoformat(),
                                    limit=MAX_PER_PAGINA, offset=0)
        # De aggregatie gaat over ALLES in de periode, niet over de pagina die
        # het scherm toont; daarom hier opnieuw ophalen zonder limiet.
        rijen = (self._uit_runs(lab_id, van.isoformat(), None)
                 + self._uit_workflows(lab_id, van.isoformat(), None))

        per_actie: Counter = Counter()
        for emmer in emmers:
            emmer.update({"beurten": 0, "acties": 0, "cost_usd": 0.0,
                          "input_tokens": 0, "output_tokens": 0, "fouten": 0})
        for r in rijen:
            try:
                ts = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            emmer = next((e for e in emmers if e["van"] <= ts < e["tot"]), None)
            if emmer is None:
                continue
            emmer["beurten"] += 1
            emmer["acties"] += int(r.get("acties_totaal") or 0)
            emmer["cost_usd"] = round(emmer["cost_usd"] + float(r.get("cost_usd") or 0.0), 4)
            emmer["input_tokens"] += int(r.get("input_tokens") or 0)
            emmer["output_tokens"] += int(r.get("output_tokens") or 0)
            if str(r.get("status")) in ("failed", "error", "fout"):
                emmer["fouten"] += 1
            for a in r.get("acties") or []:
                per_actie[a["naam"]] += int(a["aantal"])

        return {
            "periode": periode,
            "emmers": [{"label": e["label"], "van": e["van"].isoformat(),
                        "beurten": e["beurten"], "acties": e["acties"],
                        "fouten": e["fouten"], "cost_usd": e["cost_usd"],
                        "input_tokens": e["input_tokens"],
                        "output_tokens": e["output_tokens"]} for e in emmers],
            "top_acties": [{"naam": n, "aantal": a} for n, a in per_actie.most_common(12)],
            "totaal_beurten": sum(e["beurten"] for e in emmers),
            "totaal_acties": sum(e["acties"] for e in emmers),
            "totaal_kosten": round(sum(e["cost_usd"] for e in emmers), 4),
            "in_lijst": alles["totaal"],
        }


def _duur(start: Any, eind: Any) -> Optional[int]:
    if not start or not eind:
        return None
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(eind).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((b - a).total_seconds() * 1000))


def _emmers(nu: datetime, periode: str, aantal: int) -> List[Dict[str, Any]]:
    """De vakjes van de grafiek, oudste eerst."""
    uit: List[Dict[str, Any]] = []
    if periode == "dag":
        begin = nu.replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(aantal - 1, -1, -1):
            van = begin - timedelta(days=i)
            uit.append({"van": van, "tot": van + timedelta(days=1),
                        "label": van.strftime("%d-%m")})
    elif periode == "week":
        begin = (nu - timedelta(days=nu.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for i in range(aantal - 1, -1, -1):
            van = begin - timedelta(weeks=i)
            uit.append({"van": van, "tot": van + timedelta(weeks=1),
                        "label": f"wk {van.isocalendar().week}"})
    else:
        begin = nu.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        maanden = []
        jaar, maand = begin.year, begin.month
        for _ in range(aantal):
            maanden.append((jaar, maand))
            maand -= 1
            if maand == 0:
                jaar, maand = jaar - 1, 12
        for jaar, maand in reversed(maanden):
            van = datetime(jaar, maand, 1, tzinfo=timezone.utc)
            tot = (datetime(jaar + 1, 1, 1, tzinfo=timezone.utc) if maand == 12
                   else datetime(jaar, maand + 1, 1, tzinfo=timezone.utc))
            uit.append({"van": van, "tot": tot, "label": van.strftime("%b %y")})
    return uit
