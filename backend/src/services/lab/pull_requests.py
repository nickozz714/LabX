"""services/lab/pull_requests.py — van branch-lab naar pull request.

Drie dingen die bij elkaar horen en daarom hier samen staan:

1. **Een PR maken** vanaf de branch van dit lab.
2. **De beschrijving vullen** uit wat er al is: de commits in de branch en
   het ticket dat eraan hangt. Een PR-omschrijving die je zelf nog moet
   typen, blijft leeg -- en dan staat er bij de review niets om op terug te
   vallen.
3. **De comments terughalen** en ze als opmerking op dat ticket zetten,
   zodat feedback uit de review in dezelfde lijst staat als de rest van het
   werk.

Het ticket vinden gaat via de branch: een bord hangt aan een cluster en een
ticket draagt de branch waaraan gewerkt wordt. Geen extra veld, geen tweede
administratie die uit de pas kan lopen.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from services.lab.github import GitHub, repo_deel

log = get_logger(__name__)

#: Hoeveel commits er hoogstens in de beschrijving komen. Meer dan dit leest
#: niemand, en dan is de lijst geen samenvatting meer maar een logboek.
MAX_COMMITS = 20


class PullRequests:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── de onderdelen bij elkaar zoeken ──────────────────────────────────────

    def _lab(self, lab_id: str):
        from models.cluster import Cluster
        from models.lab import Lab

        p = self.db.get(Lab, lab_id)
        if p is None or not p.cluster_id:
            raise HTTPException(status_code=404,
                                detail="Dit is geen branch-lab van een cluster.")
        c = self.db.get(Cluster, p.cluster_id)
        if c is None:
            raise HTTPException(status_code=404, detail="Het cluster bestaat niet meer.")
        return p, c

    def _github(self, c) -> GitHub:
        from services.lab.clusters import ClusterService

        repo = repo_deel(c.repo_url or "")
        if not repo:
            raise HTTPException(
                status_code=400,
                detail="Dit cluster wijst niet naar een GitHub-repo; pull requests "
                       "kan LabX hier niet maken.")
        pat = ClusterService(self.db)._pat(c)
        if not pat:
            raise HTTPException(
                status_code=400,
                detail="Dit cluster heeft geen token in de kluis. Een deploy key kan "
                       "wel pushen maar geen pull request maken; daar is een token "
                       "voor nodig met 'Pull requests: Read and write'.")
        return GitHub(repo, pat)

    def ticket_van(self, lab) -> Optional[Any]:
        """Het ticket dat aan de branch van dit lab hangt."""
        from models.board import Board, Ticket

        if not lab.branch:
            return None
        borden = [b.id for b in self.db.query(Board)
                  .filter(Board.cluster_id == lab.cluster_id).all()]
        if not borden:
            return None
        return (self.db.query(Ticket)
                .filter(Ticket.board_id.in_(borden), Ticket.branch == lab.branch)
                .first())

    def rechten(self, lab_id: str) -> Dict[str, Any]:
        """Mag dit cluster pull requests maken? En zo nee, waar zit het vast.

        Zonder dit is het antwoord op "waarom lukt het niet" een 403, en een
        403 kan drie dingen betekenen die elk ergens anders opgelost worden.
        """
        _, c = self._lab(lab_id)
        return self._github(c).rechten()

    # ── de beschrijving ──────────────────────────────────────────────────────

    def voorstel(self, lab_id: str) -> Dict[str, Any]:
        """Wat er in de PR zou komen, zonder hem te maken.

        Eerst laten zien en dan pas versturen: een titel en een beschrijving
        zijn het enige wat een reviewer van je krijgt voordat hij de diff
        opent, en die wil je kunnen bijschaven.
        """
        lab, c = self._lab(lab_id)
        gh = self._github(c)
        branch = (lab.branch or "").strip()
        if not branch:
            raise HTTPException(status_code=400, detail="Dit lab heeft geen branch.")

        bestaand = gh.pr_voor_branch(branch)
        # De standaardbranch van de repo, want een cluster legt geen basis
        # vast -- en GitHub weet het zelf het beste.
        naar = gh.standaardbranch()
        ticket = self.ticket_van(lab)

        commits: List[Dict[str, str]] = []
        try:
            vgl = gh.vergelijk(naar, branch)
            for rij in (vgl.get("commits") or [])[-MAX_COMMITS:]:
                regel = ((rij.get("commit") or {}).get("message") or "").splitlines()
                commits.append({"sha": (rij.get("sha") or "")[:7],
                                "titel": regel[0] if regel else ""})
        except HTTPException as fout:
            # Geen commits kunnen ophalen maakt het voorstel armer, niet stuk.
            log.warningx("Commits voor het PR-voorstel niet op te halen",
                         lab=lab.name, reden=str(fout.detail)[:160])

        return {
            "branch": branch,
            "naar": naar,
            "bestaat_al": bool(bestaand),
            "pr_nummer": (bestaand or {}).get("number") or lab.pr_nummer,
            "pr_url": (bestaand or {}).get("html_url") or "",
            "titel": _titel(ticket, branch, commits),
            "beschrijving": _beschrijving(ticket, commits),
            "ticket": (ticket.key if ticket else ""),
            "commits": commits,
        }

    # ── maken ────────────────────────────────────────────────────────────────

    def maak(self, lab_id: str, *, titel: str = "", beschrijving: str = "",
             naar: str = "", concept: bool = False) -> Dict[str, Any]:
        lab, c = self._lab(lab_id)
        gh = self._github(c)
        branch = (lab.branch or "").strip()

        bestaand = gh.pr_voor_branch(branch)
        if bestaand:
            # Geen fout: je wilde een PR voor deze branch en die is er.
            lab.pr_nummer = bestaand.get("number")
            self.db.commit()
            return {"ok": True, "nieuw": False, "nummer": bestaand.get("number"),
                    "url": bestaand.get("html_url"),
                    "melding": "Er stond al een open pull request voor deze branch."}

        voorstel = self.voorstel(lab_id)
        pr = gh.maak_pr(
            branch=branch,
            naar=(naar or voorstel["naar"]),
            titel=(titel.strip() or voorstel["titel"]),
            beschrijving=(beschrijving.strip() or voorstel["beschrijving"]),
            concept=concept)
        lab.pr_nummer = pr.get("number")
        self.db.commit()
        log.infox("Pull request aangemaakt", lab=lab.name, branch=branch,
                  nummer=pr.get("number"))
        return {"ok": True, "nieuw": True, "nummer": pr.get("number"),
                "url": pr.get("html_url"), "melding": "Pull request aangemaakt."}

    # ── comments ─────────────────────────────────────────────────────────────

    def comments(self, lab_id: str) -> Dict[str, Any]:
        lab, c = self._lab(lab_id)
        nummer = lab.pr_nummer
        if not nummer:
            gevonden = self._github(c).pr_voor_branch((lab.branch or "").strip())
            nummer = (gevonden or {}).get("number")
            if nummer:
                lab.pr_nummer = nummer
                self.db.commit()
        if not nummer:
            return {"pr_nummer": None, "comments": [], "ticket": "",
                    "melding": "Deze branch heeft nog geen pull request."}

        ticket = self.ticket_van(lab)
        return {"pr_nummer": nummer,
                "ticket": (ticket.key if ticket else ""),
                "comments": self._github(c).comments(int(nummer)),
                "melding": ""}

    def naar_ticket(self, lab_id: str, *, ids: List[str]) -> Dict[str, Any]:
        """Gekozen comments als opmerking op het ticket zetten.

        Met de bron erbij -- auteur, soort en de plek in de code -- want een
        losse zin uit een review is over twee dagen niet meer te plaatsen.
        """
        from services.boards.board_service import BoardService

        lab, _ = self._lab(lab_id)
        ticket = self.ticket_van(lab)
        if ticket is None:
            raise HTTPException(
                status_code=404,
                detail=f"Geen ticket gevonden met branch '{lab.branch}' op een bord "
                       f"van dit cluster.")
        uit = self.comments(lab_id)
        gekozen = [k for k in uit["comments"] if k["id"] in set(ids)]
        if not gekozen:
            raise HTTPException(status_code=400, detail="Niets gekozen om door te zetten.")

        borden = BoardService(self.db)
        for k in gekozen:
            plek = f" ({k['plek']})" if k.get("plek") else ""
            borden.add_comment(
                ticket.id,
                body=(f"**Review op PR #{uit['pr_nummer']}** — {k['auteur']}, "
                      f"{k['soort']}{plek}:\n\n{k['tekst']}\n\n{k['url']}"),
                author="github", kind="comment",
                # Intern: dit komt van de review en hoort bij ons. Een bord
                # dat naar Jira synchroniseert zou het anders terugduwen naar
                # de klant, die het daar al gezegd heeft.
                internal=True)
        log.infox("PR-comments naar het ticket gezet", ticket=ticket.key,
                  aantal=len(gekozen))
        return {"ok": True, "ticket": ticket.key, "aantal": len(gekozen)}


# ── de teksten ───────────────────────────────────────────────────────────────

def _titel(ticket, branch: str, commits: List[Dict[str, str]]) -> str:
    """Het ticket wint, dan de enige commit, dan de branchnaam.

    In die volgorde omdat een reviewer eerst wil weten WAAROM dit er is, en
    dat staat in het ticket. Bij precies één commit is die boodschap meestal
    even goed; bij meer wordt het een willekeurige greep."""
    if ticket is not None and (ticket.title or "").strip():
        return f"{ticket.key}: {ticket.title.strip()}"
    if len(commits) == 1 and commits[0]["titel"]:
        return commits[0]["titel"]
    return branch.split("/")[-1].replace("-", " ").strip() or branch


def _beschrijving(ticket, commits: List[Dict[str, str]]) -> str:
    delen: List[str] = []
    if ticket is not None:
        delen.append(f"## {ticket.key} — {(ticket.title or '').strip()}")
        if (ticket.description or "").strip():
            delen.append(ticket.description.strip())
        if (ticket.acceptance_criteria or "").strip():
            delen.append("### Acceptatiecriteria\n\n"
                         + ticket.acceptance_criteria.strip())
    if commits:
        regels = "\n".join(f"- `{k['sha']}` {k['titel']}" for k in commits if k["titel"])
        if regels:
            delen.append(f"### Commits\n\n{regels}")
    if not delen:
        # Liever een lege PR dan een PR met een verzonnen samenvatting erin.
        return ""
    return "\n\n".join(delen)
