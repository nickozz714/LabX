"""
services/lab/hostmetrics.py

Hoe staat de machine ervoor, en kan er nog een werker bij?

**Waarom dit uit /proc komt en niet uit `docker stats`.** De backend draait zelf
in een container, maar `/proc/meminfo`, `/proc/stat` en `/proc/loadavg` zijn in
Docker NIET geïsoleerd: je leest daar de cijfers van de HOST. Dat is normaal een
valkuil — het is de reden dat JVM's jarenlang het verkeerde geheugenplafond
kozen — maar hier is het precies wat we willen. `docker stats` zou alleen
containers tellen, en dan mist elke andere gebruiker van de machine.

**Waarom load average en niet CPU-percentage alleen.** Een momentopname van het
CPU-gebruik zegt weinig over of je er nog een lab bij kunt zetten: 100% met één
proces is prima, 100% met dertig wachtende processen is een machine die
stilstaat. De load average telt wie er staat te WACHTEN, en gedeeld door het
aantal kernen is dat de enige eerlijke maat voor "zit hier nog rek in".

**Toegezegd versus gebruikt.** Een lab claimt geheugen en CPU via zijn
container-limieten. Die som kan de machine ruim overstijgen zonder dat er nu
iets misgaat — tot alle labs tegelijk gaan werken. Beide getallen staan er
daarom naast elkaar: wat er NU op staat, en waar je voor getekend hebt.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any, Dict, List, Optional

from component_logging import get_logger

log = get_logger(__name__)

# Vanaf hier is het krap, en vanaf hier is het mis. Geen exacte wetenschap,
# wel bewust: onder 85% geheugen merkt niemand iets, boven 95% begint de kernel
# te swappen of processen te doden, en dan valt een lab om zonder duidelijke
# reden. Voor load geldt 1.0 per kern als de klassieke grens — dan is elke kern
# precies bezet en staat er nog niemand te wachten.
GRENS_GEHEUGEN_KRAP = 0.85
GRENS_GEHEUGEN_VOL = 0.95
GRENS_LOAD_KRAP = 1.0
GRENS_LOAD_VOL = 1.5


def _meminfo() -> Dict[str, int]:
    uit: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "rt", encoding="utf-8") as fh:
            for regel in fh:
                sleutel, _, rest = regel.partition(":")
                getal = rest.strip().split()
                if getal and getal[0].isdigit():
                    uit[sleutel] = int(getal[0]) * 1024      # kB -> bytes
    except Exception as exc:  # noqa: BLE001
        log.warningx("Kon /proc/meminfo niet lezen", error=str(exc)[:200])
    return uit


def _cpu_tijden() -> Optional[tuple]:
    try:
        with open("/proc/stat", "rt", encoding="utf-8") as fh:
            velden = fh.readline().split()
        if velden and velden[0] == "cpu":
            getallen = [int(v) for v in velden[1:8]]
            idle = getallen[3] + getallen[4]           # idle + iowait
            return sum(getallen), idle
    except Exception as exc:  # noqa: BLE001
        log.warningx("Kon /proc/stat niet lezen", error=str(exc)[:200])
    return None


async def _cpu_bezet() -> Optional[float]:
    """Het aandeel niet-idle CPU over een kort venster.

    Twee metingen met een pauze ertussen; één meting geeft het gemiddelde sinds
    het opstarten van de machine en dat is op een server die weken draait een
    getal zonder betekenis.
    """
    eerste = _cpu_tijden()
    if eerste is None:
        return None
    await asyncio.sleep(0.25)
    tweede = _cpu_tijden()
    if tweede is None:
        return None
    d_totaal = tweede[0] - eerste[0]
    d_idle = tweede[1] - eerste[1]
    if d_totaal <= 0:
        return None
    return max(0.0, min(1.0, 1.0 - d_idle / d_totaal))


def _loadavg() -> List[float]:
    try:
        with open("/proc/loadavg", "rt", encoding="utf-8") as fh:
            delen = fh.read().split()
        return [float(d) for d in delen[:3]]
    except Exception:  # noqa: BLE001
        return []


def _kernen() -> int:
    try:
        return int(os.cpu_count() or 1)
    except Exception:  # noqa: BLE001
        return 1


async def _gpu() -> List[Dict[str, Any]]:
    """GPU's, als er een nvidia-smi is. Geen GPU is geen fout — de meeste
    machines waar LabX op draait hebben er geen, en dan hoort het scherm dat
    gewoon te zeggen in plaats van een lege grafiek te tonen."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.warningx("nvidia-smi gaf geen antwoord", error=str(exc)[:200])
        return []
    kaarten = []
    for regel in out.decode("utf-8", "replace").splitlines():
        delen = [d.strip() for d in regel.split(",")]
        if len(delen) < 5:
            continue
        try:
            kaarten.append({
                "naam": delen[0],
                "geheugen_totaal": int(float(delen[1])) * 1024 * 1024,
                "geheugen_gebruikt": int(float(delen[2])) * 1024 * 1024,
                "bezet": float(delen[3]) / 100.0,
                "temperatuur": float(delen[4]),
            })
        except ValueError:
            continue
    return kaarten


def _schijf(pad: str = "/data") -> Optional[Dict[str, int]]:
    """De schijf waar de labvolumes en de database op staan. Niet de hele host:
    daar kijkt de container niet in, en doen alsof van wel is erger dan het
    weglaten."""
    try:
        totaal, gebruikt, vrij = shutil.disk_usage(pad)
        return {"totaal": totaal, "gebruikt": gebruikt, "vrij": vrij, "pad": pad}
    except Exception:  # noqa: BLE001
        return None


def toezeggingen(db: Any) -> Dict[str, Any]:
    """Wat de labs bij elkaar CLAIMEN, of ze nu draaien of niet.

    Een gestopt lab kost niets, dus telt alleen wat er loopt. De som van de
    limieten mag de machine best overstijgen — containers gebruiken zelden hun
    plafond — maar als iedereen tegelijk aan het werk gaat, is dat precies wat
    er gebeurt, en dan wil je van tevoren geweten hebben dat je overboekt zat.
    """
    from models.lab import Lab
    from models.lab_worker import LabWorker

    labs = db.query(Lab).all()
    geheugen = cpu = 0.0
    draaiend = 0
    per_lab: List[Dict[str, Any]] = []
    for lab in labs:
        if (lab.status or "") != "running":
            continue
        # Elke werker is een eigen container met dezelfde limieten.
        werkers = (db.query(LabWorker)
                   .filter(LabWorker.lab_id == lab.id,
                           LabWorker.status.in_(("running", "pending"))).count()) or 1
        lab_mem = float(lab.mem_limit_mb or 0) * 1024 * 1024 * werkers
        lab_cpu = float(lab.cpu_limit or 0) * werkers
        geheugen += lab_mem
        cpu += lab_cpu
        draaiend += 1
        per_lab.append({"id": lab.id, "naam": lab.name, "werkers": werkers,
                        "geheugen": lab_mem, "cpu": lab_cpu})
    return {"labs_draaiend": draaiend, "geheugen": geheugen, "cpu": cpu,
            "per_lab": sorted(per_lab, key=lambda x: -x["geheugen"])}


def _waarschuwingen(mem: Dict[str, Any], load_per_kern: Optional[float],
                    claim: Dict[str, Any], kernen: int,
                    schijf: Optional[Dict[str, int]]) -> List[Dict[str, str]]:
    """Wat er mis is, in gewone taal en met wat je eraan kunt doen.

    Een waarschuwing zonder handelingsperspectief is ruis; daarom staat bij elk
    punt wat de volgende stap is.
    """
    uit: List[Dict[str, str]] = []
    deel = mem.get("deel_gebruikt")
    if deel is not None and deel >= GRENS_GEHEUGEN_VOL:
        uit.append({"ernst": "hoog", "onderwerp": "geheugen",
                    "tekst": f"Het geheugen is voor {deel:.0%} in gebruik. De kernel gaat "
                             "swappen of processen afbreken; een lab dat omvalt doet dat hier "
                             "zonder duidelijke reden. Stop een lab of verlaag het aantal werkers."})
    elif deel is not None and deel >= GRENS_GEHEUGEN_KRAP:
        uit.append({"ernst": "midden", "onderwerp": "geheugen",
                    "tekst": f"Het geheugen is voor {deel:.0%} in gebruik. Er kan nog iets bij, "
                             "maar niet veel — kijk hieronder welk lab het meeste claimt."})

    if load_per_kern is not None and load_per_kern >= GRENS_LOAD_VOL:
        uit.append({"ernst": "hoog", "onderwerp": "cpu",
                    "tekst": f"De load is {load_per_kern:.2f} per kern ({kernen} kernen). Er staan "
                             "meer processen te wachten dan de machine kan bedienen; alles wordt "
                             "trager, ook wat al draait. Zet er nu niets bij."})
    elif load_per_kern is not None and load_per_kern >= GRENS_LOAD_KRAP:
        uit.append({"ernst": "midden", "onderwerp": "cpu",
                    "tekst": f"De load is {load_per_kern:.2f} per kern ({kernen} kernen). Elke kern "
                             "is bezet. Een werker erbij gaat ten koste van wat er nu draait."})

    # Swap vertelt iets wat het geheugenpercentage verzwijgt. De server stond
    # op 13-09-2026 op 74% geheugen — ogenschijnlijk ruim — met de swap voor
    # 100% vol. Dat betekent dat de kernel al pagina's naar de schijf heeft
    # moeten duwen: de krapte is er geweest en kan zo weer terugkomen. Alleen
    # naar het momentane percentage kijken mist dat volledig.
    swap_totaal = mem.get("swap_totaal") or 0
    swap_deel = (mem.get("swap_gebruikt") or 0) / swap_totaal if swap_totaal else 0
    if swap_deel >= 0.90:
        uit.append({"ernst": "midden", "onderwerp": "swap",
                    "tekst": f"De swap is voor {swap_deel:.0%} in gebruik. Het geheugen mag er nu "
                             "ruim uitzien, maar de kernel heeft al pagina's naar de schijf moeten "
                             "duwen — dat is honderden malen trager en het gebeurt zodra het weer "
                             "druk wordt. Kijk of er een lab minder werkers aankan."})

    totaal = mem.get("totaal") or 0
    if totaal and claim["geheugen"] > totaal:
        uit.append({"ernst": "midden", "onderwerp": "overboeking",
                    "tekst": f"De draaiende labs claimen samen {claim['geheugen'] / 1e9:.1f} GB "
                             f"terwijl de machine er {totaal / 1e9:.1f} heeft. Dat gaat goed zolang "
                             "ze niet allemaal tegelijk werken — maar als dat wél gebeurt, is dit "
                             "waar het misgaat."})
    if kernen and claim["cpu"] > kernen * 2:
        uit.append({"ernst": "laag", "onderwerp": "overboeking",
                    "tekst": f"De labs claimen samen {claim['cpu']:.1f} CPU op {kernen} kernen. "
                             "Ruim overboekt; prima bij wachtend werk, merkbaar zodra er echt "
                             "gerekend wordt."})
    if schijf and schijf["totaal"]:
        vrij_deel = schijf["vrij"] / schijf["totaal"]
        if vrij_deel < 0.10:
            uit.append({"ernst": "hoog", "onderwerp": "schijf",
                        "tekst": f"Nog {schijf['vrij'] / 1e9:.1f} GB vrij op {schijf['pad']} "
                                 f"({vrij_deel:.0%}). Hier staan de labvolumes én de database; "
                                 "vol betekent dat schrijven stopt."})
    return uit


async def meten(db: Any) -> Dict[str, Any]:
    """Het volledige beeld: wat de machine heeft, wat ervan op is, wat de labs
    claimen, en wat daar mis mee is."""
    mi = _meminfo()
    totaal = mi.get("MemTotal") or 0
    beschikbaar = mi.get("MemAvailable")
    if beschikbaar is None:
        # Oudere kernels kennen MemAvailable niet; dan is vrij + cache de beste
        # benadering — puur MemFree onderschat wat er echt beschikbaar is enorm.
        beschikbaar = (mi.get("MemFree") or 0) + (mi.get("Cached") or 0) + (mi.get("Buffers") or 0)
    gebruikt = max(0, totaal - beschikbaar)

    geheugen = {"totaal": totaal, "beschikbaar": beschikbaar, "gebruikt": gebruikt,
                "deel_gebruikt": (gebruikt / totaal) if totaal else None,
                "swap_totaal": mi.get("SwapTotal") or 0,
                "swap_gebruikt": max(0, (mi.get("SwapTotal") or 0) - (mi.get("SwapFree") or 0))}

    kernen = _kernen()
    load = _loadavg()
    load_per_kern = (load[0] / kernen) if (load and kernen) else None
    claim = toezeggingen(db)
    schijf = _schijf()

    return {
        "geheugen": geheugen,
        "cpu": {"kernen": kernen, "bezet": await _cpu_bezet(),
                "load": load, "load_per_kern": load_per_kern},
        "gpu": await _gpu(),
        "schijf": schijf,
        "labs": claim,
        "waarschuwingen": _waarschuwingen(geheugen, load_per_kern, claim, kernen, schijf),
    }


def past_er_nog_een_bij(db: Any, *, geheugen_mb: int, cpu: float) -> Dict[str, Any]:
    """Kan er een container bij met deze limieten?

    Wordt gevraagd vóór het opschalen. Geeft bewust een OORDEEL plus een reden
    en geen kaal getal: de aanroeper moet kunnen kiezen tussen tegenhouden en
    waarschuwen, en de gebruiker moet kunnen lezen waarom.
    """
    mi = _meminfo()
    totaal = mi.get("MemTotal") or 0
    beschikbaar = mi.get("MemAvailable") or 0
    nodig = int(geheugen_mb) * 1024 * 1024
    kernen = _kernen()
    load = _loadavg()
    load_per_kern = (load[0] / kernen) if (load and kernen) else None

    if totaal and nodig > beschikbaar:
        return {"ok": False, "ernst": "hoog",
                "reden": (f"Deze werker vraagt {nodig / 1e9:.1f} GB en er is nog "
                          f"{beschikbaar / 1e9:.1f} GB beschikbaar. Starten zou de machine over "
                          "de rand duwen.")}
    if totaal and (totaal - beschikbaar + nodig) / totaal >= GRENS_GEHEUGEN_VOL:
        return {"ok": False, "ernst": "hoog",
                "reden": (f"Met deze werker erbij zit het geheugen op "
                          f"{((totaal - beschikbaar + nodig) / totaal):.0%}. Dat is de zone waarin "
                          "de kernel processen gaat afbreken.")}
    if load_per_kern is not None and load_per_kern >= GRENS_LOAD_VOL:
        return {"ok": True, "ernst": "midden",
                "reden": (f"Het geheugen kan het hebben, maar de load is {load_per_kern:.2f} per "
                          f"kern. Deze werker start wel en wordt traag — en maakt de rest ook trager.")}
    return {"ok": True, "ernst": "geen", "reden": ""}
