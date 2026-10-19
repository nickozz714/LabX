"""services/boards/cluster_werk.py — wat een agent in een branch-lab moet weten.

Werken in een cluster-branch-lab is iets anders dan werken in een gewoon lab,
en de opdracht hoorde dat verschil niet te verzwijgen.

In een GEWOON lab is /workspace het werk. Je kijkt rond, je vindt wat er is.

In een BRANCH-LAB staat er een uitgecheckte repo op een branch die bij dít
ticket hoort, naast vaste diensten die op naam bereikbaar zijn, met een
startscript dat het geheel aan de praat houdt. Een agent die dat niet weet
begint met `ls /workspace`, raadt waar de code staat, en komt er pas na
twintig shell-stappen achter dat hij in een monorepo zit. Gemeten op het
IntelligentHive-cluster: honderd stappen om de code te vinden, en daarna
`error_max_turns` voordat het werk af was.

DRIE DINGEN DIE HIER IN GAAN, EN WAAROM:

- **Het pad.** De werkkopie, de branch, en dat hij op die branch moet blijven.
  Dat is één regel die twintig zoekstappen bespaart.

- **De vaste diensten.** Op naam, met hun poort. Anders gaat een agent een
  database zoeken die er wél is, of erger: er zelf een starten.

- **De instructiebestanden van de REPO.** `AGENTS.md`, `CLAUDE.md`,
  `CONTRIBUTING.md`, de `docs/`-map: daar staat vaak precies hoe dit project
  wil dat je iets opzet of stijlt. Wij noemen alleen de PADEN -- inhoud zou
  de helft van de context opeten, en de agent kan ze zelf lezen zodra hij
  weet dat ze bestaan.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

#: Bestanden die een repo gebruikt om te zeggen hoe eraan gewerkt wordt.
#: Op naam, niet op inhoud: wat erin staat verschilt per project, dát ze er
#: zijn is wat de agent moet weten.
INSTRUCTIEBESTANDEN = (
    "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "CONVENTIONS.md",
    "ARCHITECTURE.md", "STYLEGUIDE.md", "README.md", ".cursorrules",
)
#: Hoe diep er gezocht wordt naar instructiebestanden onder de repo. Twee
#: niveaus vangt `components/api/AGENTS.md` en stopt vóór node_modules-diepte.
ZOEKDIEPTE = 2
#: Zoveel paden komen er hoogstens in de opdracht. Meer is geen kaart meer.
MAX_PADEN = 25


def cluster_kop(db: Session, lab, ticket) -> str:
    """De clusterspecifieke kop boven een ticket-opdracht, of "" als dit geen
    branch-lab is. Een gewoon lab verandert hierdoor niet."""
    if lab is None or not getattr(lab, "cluster_id", None):
        return ""

    from models.cluster import Cluster, ClusterDienst

    c = db.get(Cluster, lab.cluster_id)
    if c is None:
        return ""

    map_ = _werkmap(c)
    regels: List[str] = [
        "### Waar je werkt — dit is een cluster-branch-lab",
        "",
        f"Dit is geen leeg lab. Er staat een uitgecheckte werkkopie klaar:",
        "",
        f"- **Werkkopie:** `/workspace/{map_}` — begin daar, niet in `/workspace`.",
        f"- **Branch:** `{lab.branch or '(onbekend)'}` — die is voor dit ticket "
        f"aangemaakt. Blijf erop; maak geen nieuwe branch en wissel niet.",
    ]
    if c.repo_url:
        regels.append(f"- **Herkomst:** {c.repo_url}")

    diensten = (db.query(ClusterDienst)
                .filter(ClusterDienst.cluster_id == c.id).all())
    if diensten:
        regels.append("")
        regels.append("**Vaste diensten van dit cluster** — op naam bereikbaar vanuit "
                      "dit lab, dus start ze niet zelf:")
        for d in diensten:
            poorten = ", ".join(str(p) for p in (d.poorten or [])) or "geen"
            kaal = _kale_naam(c.naam, d.naam)
            alias = f"`{d.naam}`" + (f" (ook `{kaal}`)" if kaal != d.naam else "")
            regels.append(f"- {alias} — poort {poorten} — {d.image}")

    if c.omgeving:
        regels.append("")
        regels.append("**Omgevingsvariabelen die dit lab al heeft:** "
                      + ", ".join(f"`{k}`" for k in sorted(c.omgeving)))

    if c.start_script:
        regels.append("")
        regels.append("De applicatie wordt door LabX gestart met het startscript van het "
                      "cluster; je hoeft hem niet zelf op te starten. Heb je een herstart "
                      "nodig na een wijziging die niet door een devserver wordt opgepikt, "
                      "zeg dat dan in je eindantwoord.")

    return "\n".join(regels)


async def instructiebestanden(lab_svc, lab, repo_map: str) -> List[str]:
    """De instructiebestanden die ín de repo staan, als paden.

    Eén shell-aanroep, want twintig losse zoekopdrachten van de agent zelf
    kosten twintig beurten -- en precies daar liep het eerder op vast.
    """
    namen = " -o ".join(f"-name {n!r}" for n in INSTRUCTIEBESTANDEN)
    cmd = (f'cd "/workspace/{repo_map}" 2>/dev/null || exit 0; '
           f'find . -maxdepth {ZOEKDIEPTE} \\( {namen} \\) '
           f'-not -path "*/node_modules/*" -not -path "*/.git/*" 2>/dev/null '
           f'| sed "s|^\\./||" | sort | head -{MAX_PADEN}')
    try:
        uit = await lab_svc.exec_command(lab.id, cmd, timeout=30.0)
    except Exception as exc:  # noqa: BLE001 — geen lijst is geen reden om niets te doen
        log.warningx("Instructiebestanden niet op te halen", lab_id=lab.id,
                     error=str(exc)[:200])
        return []
    tekst = (uit or {}).get("output", "") if isinstance(uit, dict) else str(uit or "")
    return [r.strip() for r in tekst.splitlines() if r.strip() and not r.startswith("/")]


def instructieblok(paden: List[str], repo_map: str) -> str:
    """De gevonden instructiebestanden als blok voor de opdracht."""
    if not paden:
        return ""
    regels = [
        "**Deze repo schrijft zelf voor hoe eraan gewerkt wordt.** Lees wat van "
        "toepassing is vóór je begint te bouwen — hier staat meestal hoe dit "
        "project iets opzet, benoemt en stijlt:",
        "",
    ]
    regels += [f"- `/workspace/{repo_map}/{p}`" for p in paden[:MAX_PADEN]]
    regels.append("")
    regels.append("Botst zo'n afspraak met wat er in het ticket staat, volg dan het "
                  "ticket en noem het verschil in je eindantwoord.")
    return "\n".join(regels)


def _werkmap(c) -> str:
    """De map waarin de repo is uitgecheckt — zoals `_kloon_branch` hem noemt."""
    if c.repo_url:
        return (c.repo_url.rstrip("/").split("/")[-1].removesuffix(".git")) or "repo"
    from services.lab.clusters import _veilige_naam

    return _veilige_naam(c.naam) or "project"


def _kale_naam(clusternaam: str, dienstnaam: str) -> str:
    from services.lab.clusters import _voorvoegsel

    voorvoegsel = f"{_voorvoegsel(clusternaam)}-"
    return dienstnaam[len(voorvoegsel):] if dienstnaam.startswith(voorvoegsel) else dienstnaam
