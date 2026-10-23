"""services/lab/repo_agents.py — de agents die in de repo zelf staan.

Een repo brengt vaak zijn eigen agents mee, in `.claude/agents/*.md`: de
regels van dát team, voor dát project, door henzelf onderhouden. In LabX
werden ze niet gevonden, en wel om een saaie reden: de Claude-CLI draait op
de SERVER en zoekt subagents relatief aan zijn eigen werkmap. De repo staat
in het lab.

Dit haalt die kloof weg zonder de CLI te verplaatsen. We lezen het bestand
uit het lab en geven de inhoud mee als instructie van een gewone LabX-run.
Het gereedschap blijft dus dat van het lab -- `lab__shell_exec` in de
container, met git, az en (met de extra erbij) gh. Dat is precies de grens
die we wilden: de agent kan alles wat het LAB kan, en niets daarbuiten.

Wat dat betekent voor een agent die native tools declareert (`Read`,
`Bash(gh pr:*)`): die namen bestaan hier niet, maar wat hij ermee WIL doen
wel. Hij leest in zijn eigen instructie dat hij `gh pr view` moet draaien, en
draait dat via de shell van het lab. Daarom staat er een kopje boven zijn
tekst dat dit uitlegt -- niet om hem te sturen, maar om het misverstand voor
te zijn dat zijn gereedschap ontbreekt.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

#: Waar Claude Code subagents verwacht. Relatief aan de repo in het lab.
MAP = ".claude/agents"

#: De lijst ophalen: namen, en de eerste regels van de beschrijving.
_LIJST_SCRIPT = (
    'cd "/workspace/$DEST" 2>/dev/null || { echo "geen werkkopie"; exit 1; }; '
    'for f in ' + MAP + '/*.md; do '
    '  [ -f "$f" ] || continue; '
    '  printf "%s\\036" "$(basename "$f" .md)"; '
    '  sed -n "1,40p" "$f" | tr "\\n" "\\037"; '
    '  printf "\\035"; '
    'done'
)

_LEES_SCRIPT = (
    'cd "/workspace/$DEST" 2>/dev/null || { echo "geen werkkopie"; exit 1; }; '
    'cat "' + MAP + '/$NAAM.md"'
)

#: Wat er boven de tekst van de agent komt te staan.
#:
#: Kort, en alleen over het verschil dat hij niet kan weten: waar hij is en
#: waarmee hij werkt. Zijn eigen instructie blijft ongemoeid -- die is van het
#: team en niet van ons.
KOP = """Je draait in LabX, in een werkkopie van deze repository.

Je hebt geen lokale shell: gebruik het gereedschap van het lab. Elk commando
dat hieronder staat beschreven -- git, gh, az, yarn -- draai je daarmee, in
`{map}`. Werkt `gh` niet, dan is de lab-extra "GitHub CLI" nog niet
geïnstalleerd; meld dat en ga niet op zoek naar een omweg.

Heb je een token nodig voor `gh`, schrijf dan `GH_TOKEN={{{{secret:{geheim}}}}}`
in je commando. LabX vult de waarde in zonder dat hij op een commandoregel
belandt; typ hem nooit zelf uit.

Hieronder staat je opdracht, zoals die in de repository is vastgelegd.

---

"""


class RepoAgents:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _git(self):
        from services.lab.branch_git import BranchGit

        return BranchGit(self.db)

    async def lijst(self, lab_id: str) -> List[Dict[str, Any]]:
        """Welke agents deze repo meebrengt."""
        from services.lab.pull_requests import PullRequests

        lab, c = PullRequests(self.db)._lab(lab_id)
        uit = await self._git()._git(lab, c, _LIJST_SCRIPT, lezen=True)
        ruw = str(uit.get("output") or "")
        agents: List[Dict[str, Any]] = []
        for blok in ruw.split("\x1d"):
            if "\x1e" not in blok:
                continue
            naam, kop = blok.split("\x1e", 1)
            naam = naam.strip()
            if not naam:
                continue
            agents.append({"naam": naam,
                           "omschrijving": _omschrijving(kop.replace("\x1f", "\n"))})
        return sorted(agents, key=lambda a: a["naam"])

    async def lees(self, lab_id: str, naam: str) -> Dict[str, Any]:
        """De tekst van één agent, zonder frontmatter."""
        from services.lab.pull_requests import PullRequests

        if not re.fullmatch(r"[A-Za-z0-9._-]+", naam or ""):
            # Geen pad uit een naam laten komen: deze string gaat een
            # shellcommando in.
            raise HTTPException(status_code=400, detail="Onbruikbare agentnaam.")
        lab, c = PullRequests(self.db)._lab(lab_id)
        uit = await self._git()._git(lab, c, _LEES_SCRIPT, env={"NAAM": naam},
                                     lezen=True)
        ruw = str(uit.get("output") or "")
        if not ruw.strip() or "geen werkkopie" in ruw:
            raise HTTPException(status_code=404,
                                detail=f"'{naam}' staat niet in {MAP} van deze repo.")
        kop, tekst = _splits_frontmatter(ruw)
        return {"naam": naam, "frontmatter": kop, "instructie": tekst,
                "omschrijving": _omschrijving(ruw)}

    async def start(self, lab_id: str, *, naam: str, opdracht: str = "") -> Dict[str, Any]:
        """Een repo-agent draaien als gewone LabX-run, in dit lab.

        Als gewone run en niet als iets aparts: dan krijg je het spoor, de
        guards, het afbreken en het doorpraten die daar al omheen zitten.
        """
        from services.agent import background_runs
        from services.lab.pull_requests import PullRequests

        lab, c = PullRequests(self.db)._lab(lab_id)
        agent = await self.lees(lab_id, naam)
        prompt = KOP.format(map=_repomap(c), geheim=(c.repo_token_geheim or "github"))
        prompt += agent["instructie"]
        if opdracht.strip():
            prompt += f"\n\n---\n\n## Wat er nu gevraagd wordt\n\n{opdracht.strip()}"

        thread = _thread_voor(self.db, lab, naam)
        run = background_runs.start(
            self.db, thread_id=thread.id, lab_id=lab.id, history=[], prompt=prompt)
        log.infox("Repo-agent gestart", lab=lab.name, agent=naam, run=run.id)
        return {"ok": True, "run_id": run.id, "thread_id": thread.id, "agent": naam}


# ── hulpjes ──────────────────────────────────────────────────────────────────

def _repomap(c) -> str:
    naam = (c.repo_url or "").rstrip("/").split("/")[-1].removesuffix(".git")
    return f"/workspace/{naam}" if naam else "/workspace"


def _splits_frontmatter(ruw: str) -> tuple[str, str]:
    """De YAML-kop eraf. Die beschrijft hém voor Claude Code; de agent zelf
    heeft er niets aan en het zou alleen maar over tools gaan die hier anders
    heten."""
    tekst = ruw.lstrip()
    if not tekst.startswith("---"):
        return "", ruw.strip()
    deel = tekst.split("---", 2)
    if len(deel) < 3:
        return "", ruw.strip()
    return deel[1].strip(), deel[2].strip()


def _omschrijving(ruw: str) -> str:
    """De `description:` uit de frontmatter, op één regel."""
    m = re.search(r"^description:\s*(.+?)(?=^\w+:|\Z)", ruw, re.S | re.M)
    if not m:
        return ""
    return " ".join(m.group(1).split())[:300]


def _thread_voor(db: Session, lab, naam: str):
    """Eén gesprek per agent per lab, zodat je kunt doorpraten na de
    goedkeuringspauze die zo'n agent vaak inbouwt."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from models.thread import Thread

    titel = f"{naam} · {lab.branch or lab.name}"[:255]
    bestaand = (db.query(Thread)
                .filter(Thread.lab_id == lab.id, Thread.title == titel)
                .first())
    if bestaand is not None:
        return bestaand
    nu = datetime.now(timezone.utc).isoformat()
    rij = Thread(id=str(uuid4()), title=titel, lab_id=lab.id,
                 created_at=nu, updated_at=nu)
    db.add(rij)
    db.commit()
    return rij
