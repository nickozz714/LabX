"""services/lab/github.py — de GitHub-API, op één plek.

Waarom apart: pull requests, hun beschrijving en hun comments praten alle
drie met dezelfde API, met hetzelfde token en met dezelfde foutgevallen. Dat
drie keer uitschrijven levert drie keer een andere foutmelding op voor
dezelfde oorzaak -- en bij een token is juist de foutmelding het halve werk.

Wat hier NIET gebeurt: het token ergens opslaan of teruggeven. Het komt uit
de kluis op het moment dat het nodig is, gaat in een header, en verdwijnt
weer. Een clusterrij kent alleen de NAAM van het geheim.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from component_logging import get_logger

log = get_logger(__name__)

API = "https://api.github.com"


def repo_deel(repo_url: str) -> str:
    """`https://github.com/org/repo.git` of `git@github.com:org/repo.git`
    -> `org/repo`. Leeg als het geen GitHub is."""
    url = (repo_url or "").strip()
    if "github.com" not in url:
        return ""
    deel = url.split("github.com", 1)[1]
    return deel.lstrip(":/").removesuffix(".git").strip("/")


class GitHub:
    """Praat met één repository, met één token."""

    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self._token = token

    # ── de verbinding ────────────────────────────────────────────────────────

    def _vraag(self, pad: str, *, methode: str = "GET",
               body: Optional[Dict[str, Any]] = None,
               params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{API}{pad}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        verzoek = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "labx",
            },
            method=methode)
        try:
            with urllib.request.urlopen(verzoek, timeout=30) as a:
                return json.loads(a.read() or b"null")
        except urllib.error.HTTPError as fout:
            raise HTTPException(status_code=_vertaal(fout.code),
                                detail=_uitleg(fout, self.repo)) from fout
        except urllib.error.URLError as fout:
            raise HTTPException(
                status_code=502,
                detail=f"Geen contact met GitHub: {fout.reason}") from fout

    def rechten(self) -> Dict[str, Any]:
        """Wat dit token bij deze repo mag, zonder iets te veranderen.

        Drie losse vragen, want ze falen op verschillende plekken en de
        oplossing verschilt per geval:

        - ziet het token de repo? (nee = niet toegevoegd aan Repository
          access, of nog niet goedgekeurd)
        - mag het pushen? (dat is de repo-rol, niet de tokenscope)
        - mag het bij pull requests? (403 = de permissie Pull requests staat
          uit, of de wijziging wacht nog op goedkeuring)

        Of het token ook pull requests mag SCHRIJVEN is van buitenaf niet te
        zien: GitHub vertelt dat niet, en het uitproberen zou een echte pull
        request aanmaken. Dus zeggen we wat we weten en niet meer.
        """
        uit: Dict[str, Any] = {"repo": self.repo, "soort_token": "",
                               "ziet_repo": False, "mag_pushen": False,
                               "pr_lezen": False, "reden": "", "advies": ""}
        try:
            data = self._vraag(f"/repos/{self.repo}") or {}
            uit["ziet_repo"] = True
            uit["mag_pushen"] = bool((data.get("permissions") or {}).get("push"))
        except HTTPException as fout:
            uit["reden"] = str(fout.detail)
            uit["advies"] = ("Voeg deze repository toe aan 'Repository access' van het "
                             "token. Staat hij er al in, dan wacht het token nog op "
                             "goedkeuring van de organisatie.")
            return uit

        try:
            self._vraag(f"/repos/{self.repo}/pulls", params={"state": "open", "per_page": 1})
            uit["pr_lezen"] = True
        except HTTPException as fout:
            uit["reden"] = str(fout.detail)
            uit["advies"] = ("Zet op dit token 'Pull requests: Read and write'. Vereist "
                             "de organisatie goedkeuring, dan blijven je bestaande "
                             "rechten werken terwijl het verzoek in de wachtrij staat -- "
                             "en blijft dit op nee staan tot een beheerder het "
                             "goedkeurt.")
            return uit

        uit["advies"] = ("Lezen lukt. Of het token pull requests ook mag MAKEN, blijkt "
                         "pas bij de eerste; GitHub vertelt dat niet vooraf.")
        return uit

    # ── pull requests ────────────────────────────────────────────────────────

    def pr_voor_branch(self, branch: str) -> Optional[Dict[str, Any]]:
        """De open PR van deze branch, als die er is.

        Eerst kijken en dan pas maken: een tweede PR voor dezelfde branch kan
        niet, en "er bestaat er al een" is geen fout maar een antwoord.
        """
        rijen = self._vraag(f"/repos/{self.repo}/pulls",
                            params={"head": f"{self.repo.split('/')[0]}:{branch}",
                                    "state": "open", "per_page": 1})
        return rijen[0] if rijen else None

    def maak_pr(self, *, branch: str, naar: str, titel: str,
                beschrijving: str, concept: bool = False) -> Dict[str, Any]:
        return self._vraag(f"/repos/{self.repo}/pulls", methode="POST", body={
            "title": titel, "head": branch, "base": naar,
            "body": beschrijving, "draft": bool(concept),
        })

    def commits_van_pr(self, nummer: int) -> List[Dict[str, Any]]:
        return self._vraag(f"/repos/{self.repo}/pulls/{nummer}/commits",
                           params={"per_page": 100})

    def vergelijk(self, basis: str, branch: str) -> Dict[str, Any]:
        """Wat zit er in deze branch dat nog niet in de basis zit."""
        return self._vraag(
            f"/repos/{self.repo}/compare/"
            f"{urllib.parse.quote(basis)}...{urllib.parse.quote(branch)}")

    def standaardbranch(self) -> str:
        return str((self._vraag(f"/repos/{self.repo}") or {}).get("default_branch") or "main")

    # ── comments ─────────────────────────────────────────────────────────────

    def comments(self, nummer: int) -> List[Dict[str, Any]]:
        """Alles wat er op een PR gezegd is, in één lijst.

        GitHub houdt drie soorten uit elkaar en jij niet: een algemene
        opmerking, een review met een oordeel, en een opmerking bij een
        regel code. Voor "wat moet ik nog doen" is dat één stroom, dus
        voegen we ze samen en sorteren op tijd.
        """
        uit: List[Dict[str, Any]] = []
        for rij in self._vraag(f"/repos/{self.repo}/issues/{nummer}/comments",
                               params={"per_page": 100}) or []:
            uit.append(_comment(rij, soort="opmerking"))
        for rij in self._vraag(f"/repos/{self.repo}/pulls/{nummer}/comments",
                               params={"per_page": 100}) or []:
            uit.append(_comment(rij, soort="bij code",
                                plek=f"{rij.get('path')}:{rij.get('line') or ''}"))
        for rij in self._vraag(f"/repos/{self.repo}/pulls/{nummer}/reviews",
                               params={"per_page": 100}) or []:
            if (rij.get("body") or "").strip() or rij.get("state") != "COMMENTED":
                uit.append(_comment(rij, soort=_oordeel(rij.get("state"))))
        return sorted(uit, key=lambda r: r["op"] or "")


def _comment(rij: Dict[str, Any], *, soort: str, plek: str = "") -> Dict[str, Any]:
    return {
        "id": f"{soort}:{rij.get('id')}",
        "auteur": ((rij.get("user") or {}).get("login") or "onbekend"),
        "soort": soort,
        "plek": plek,
        "tekst": (rij.get("body") or "").strip(),
        "op": rij.get("submitted_at") or rij.get("created_at") or "",
        "url": rij.get("html_url") or "",
    }


def _oordeel(staat: Optional[str]) -> str:
    return {"APPROVED": "goedgekeurd",
            "CHANGES_REQUESTED": "wijzigingen gevraagd",
            "DISMISSED": "ingetrokken"}.get(staat or "", "review")


def _vertaal(code: int) -> int:
    """Een fout van GitHub is geen serverfout van ons, op 5xx na."""
    return code if code in (401, 403, 404, 409, 422) else 502


def _uitleg(fout: urllib.error.HTTPError, repo: str) -> str:
    """Zeggen wat eraan te doen is, niet alleen wat er misging."""
    ruw = ""
    try:
        ruw = (json.loads(fout.read() or b"{}") or {}).get("message", "")
    except Exception:  # noqa: BLE001
        pass
    if fout.code == 404:
        return (f"Het token ziet '{repo}' niet. Meestal is de repository niet "
                f"toegevoegd aan 'Repository access' van het token, of wacht het "
                f"token nog op goedkeuring van de organisatie.")
    if fout.code in (401, 403):
        return ("Het token wordt geweigerd. Voor pull requests heeft het "
                "'Pull requests: Read and write' nodig, naast toegang tot de "
                "repository.")
    if fout.code == 422:
        return f"GitHub wil dit niet: {ruw or 'onbewerkbaar verzoek'}."
    return f"GitHub antwoordde {fout.code}{f': {ruw}' if ruw else ''}."
