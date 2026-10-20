"""services/lab/branch_git.py — git-werk aan een branch-lab, vanuit LabX.

Een branch-lab IS een werkkopie van een echte branch. Tot nu toe kon je er
alles mee behalve het enige waar het uiteindelijk om gaat: je werk eruit
krijgen. Dat moest via de terminal in het lab, met een token dat daar niet
hoort te staan.

VIER KEUZES DIE DE REST VERKLAREN:

- **Alles draait NAAST de lab-container, niet erin.** Dezelfde reden als bij
  het klonen: zo komt de sleutel of het token nooit in de omgeving van de
  agent. De wegwerpcontainer ziet het volume en verdwijnt daarna.

- **Je kiest de bestanden.** Een lab bouwt: er liggen logbestanden,
  `node_modules`-restanten en een `.env` die je net hebt ingevuld. `git add
  -A` zou die meenemen, en bij een `.env` is dat niet "rommel in de diff"
  maar een geheim in een publieke repo. Daarom een lijst met vinkjes, en
  daarom staat een `.env` standaard UIT.

- **De paden gaan via een bestand de container in, niet via de shell.** Ze
  komen uit een formulier en kunnen alles bevatten. `--pathspec-from-file`
  met NUL-scheiding is de enige vorm waarin een bestandsnaam met een spatie,
  een aanhalingsteken of een nieuwe regel niet opeens een commando wordt.

- **Eerst de index leegmaken.** `git reset` vóór het toevoegen, zodat wat er
  toevallig al gestaged stond niet stilletjes meelift in een commit waarvan
  jij dacht dat je hem had samengesteld.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.cluster import Cluster
from services.lab import git_url as _git
from services.lab.branch_naam import geldig

log = get_logger(__name__)


def _nu() -> str:
    return datetime.now(timezone.utc).isoformat()

#: Hoe lang een git-bewerking mag duren. Een push van een grote branch over
#: een trage lijn haalt dertig seconden niet.
GIT_TIJD = 600.0
#: Hoeveel bestanden er in de lijst komen. Meer dan dit is geen keuze meer
#: maar een scrollbak, en het duidt meestal op een map die genegeerd hoort te
#: worden.
MAX_BESTANDEN = 2000
#: Bestanden die standaard UIT staan, ook al zijn ze gewijzigd. Een `.env`
#: hoort niet in een repo, en één keer per ongeluk meesturen is één keer te
#: veel: het geheim is daarna openbaar, ook na een revert.
GEHEIM_ACHTIG = (".env", ".pem", ".key", ".p12", ".pfx", "id_rsa", ".npmrc", ".netrc")
#: En deze zijn van LabX zelf. Ze horen in de werkkopie, niet in de repo.
VAN_LABX = (".labx-start.log", ".labx-start.pid", ".labx-stdin")


def _is_gevoelig(pad: str) -> bool:
    kaal = pad.rsplit("/", 1)[-1].lower()
    if kaal in VAN_LABX or pad in VAN_LABX:
        return True
    return any(kaal == s or kaal.startswith(s) or kaal.endswith(s) for s in GEHEIM_ACHTIG)


class BranchGit:
    """Status opvragen en committen+pushen voor één branch-lab."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── status ───────────────────────────────────────────────────────────────

    async def status(self, lab_id: str) -> Dict[str, Any]:
        lab, c = self._lab(lab_id)
        uit = await self._git(lab, c, _STATUS_SCRIPT, lezen=True)
        return self._lees_status(uit.get("output") or "", lab)

    def _lees_status(self, ruw: str, lab) -> Dict[str, Any]:
        """De uitvoer van één script uit elkaar trekken.

        Eén script en niet vier aanroepen: elke aanroep is een container, en
        vier containers starten om vier regels tekst op te halen maakt het
        scherm traag zonder dat het iets preciezer wordt.
        """
        delen = ruw.split("\x1e")
        def deel(i: int) -> str:
            return delen[i] if i < len(delen) else ""

        branch = deel(0).strip()
        laatste = deel(1).strip()
        teller = deel(2).strip().split()
        vooruit = int(teller[0]) if len(teller) == 2 and teller[0].isdigit() else 0
        achter = int(teller[1]) if len(teller) == 2 and teller[1].isdigit() else 0
        op_remote = deel(3).strip() == "ja"

        bestanden: List[Dict[str, Any]] = []
        for ingang in deel(4).split("\x00"):
            if len(ingang) < 4:
                continue
            code, pad = ingang[:2], ingang[3:]
            if not pad:
                continue
            bestanden.append({
                "pad": pad,
                "code": code,
                "soort": _soort(code),
                # Niet "wat is er gebeurd" maar "wil je dit meesturen".
                # Precies die twee lopen uiteen bij een .env, en dat is het
                # enige geval waarin een vinkje iets ERGS voorkomt.
                "standaard_aan": not _is_gevoelig(pad),
                "gevoelig": _is_gevoelig(pad),
            })
            if len(bestanden) >= MAX_BESTANDEN:
                break

        return {
            "lab_id": lab.id,
            "branch": branch or (lab.branch or ""),
            "titel": lab.feature_titel or "",
            "beschrijving": lab.feature_beschrijving or "",
            "laatste_commit": laatste,
            "vooruit": vooruit,
            "achter": achter,
            "op_remote": op_remote,
            "bestanden": bestanden,
            "schoon": not bestanden,
            "afgekapt": len(bestanden) >= MAX_BESTANDEN,
        }

    # ── committen en pushen ──────────────────────────────────────────────────

    async def push(self, lab_id: str, *, bericht: str,
                   bestanden: Optional[List[str]] = None,
                   alles: bool = False) -> Dict[str, Any]:
        """De gekozen bestanden committen en de branch pushen.

        Zonder bestanden en zonder `alles` wordt er niets gecommit -- dan is
        dit alleen een push van wat er al lag. Dat is met opzet: "ik ben
        vergeten iets aan te vinken" mag geen lege commit opleveren.
        """
        lab, c = self._lab(lab_id)
        branch = (lab.branch or "").strip()
        if not geldig(branch):
            raise HTTPException(
                status_code=400,
                detail=f"'{branch}' is geen geldige branchnaam om naar te pushen.")
        if not c.repo_url:
            raise HTTPException(
                status_code=400,
                detail=("Dit cluster heeft geen repo-URL, dus er is geen remote om naar "
                        "te pushen. Zet hem onder Instellingen → Repo."))

        gekozen = [p for p in (bestanden or []) if p and "\x00" not in p]
        bericht = (bericht or "").strip()
        if (gekozen or alles) and not bericht:
            raise HTTPException(status_code=400,
                                detail="Een commit heeft een bericht nodig.")

        env: Dict[str, str] = {"TAK": branch}
        if bericht:
            env["COMMIT_BERICHT"] = bericht[:2000]
        if alles:
            env["ALLES"] = "1"
        elif gekozen:
            env["PADEN_B64"] = base64.b64encode(
                "\x00".join(gekozen).encode("utf-8")).decode()

        uit = await self._git(lab, c, _PUSH_SCRIPT, env=env)
        log.infox("Branch gepusht", lab=lab.id, branch=branch,
                  bestanden=len(gekozen) if not alles else -1,
                  exitcode=uit.get("exit_code"))
        return {
            "ok": int(uit.get("exit_code") or 0) == 0,
            "exit_code": uit.get("exit_code"),
            "branch": branch,
            "uitvoer": uit.get("output") or "",
        }

    # ── van branch wisselen ──────────────────────────────────────────────────

    async def branches(self, lab_id: str) -> Dict[str, Any]:
        """Welke branches er in deze werkkopie te kiezen zijn.

        Lokaal én op de remote, want je wilt juist naar een branch kunnen die
        je hier nog niet hebt -- dat is het hele punt van een lab waarin je
        andermans werk nakijkt.
        """
        lab, c = self._lab(lab_id)
        uit = await self._git(lab, c, _BRANCHES_SCRIPT)
        return self._lees_branches(uit.get("output") or "", lab)

    def _lees_branches(self, ruw: str, lab) -> Dict[str, Any]:
        delen = ruw.split("\x1e")

        def deel(i: int) -> str:
            return delen[i] if i < len(delen) else ""

        huidig = deel(0).strip()
        lokaal = [r.strip() for r in deel(1).splitlines() if r.strip()]
        extern = []
        for r in deel(2).splitlines():
            r = r.strip()
            # `origin/HEAD -> origin/main` is geen branch om naartoe te gaan.
            # En let op: `%(refname:short)` kort `refs/remotes/origin/HEAD` af
            # tot kaal "origin" -- dat zag er in de lijst uit als een branch
            # met die naam, en dat is er geen.
            if not r or "->" in r or r in ("origin", "origin/HEAD", "HEAD"):
                continue
            extern.append(r[len("origin/"):] if r.startswith("origin/") else r)
        alles = []
        for naam in lokaal + [b for b in extern if b not in lokaal]:
            if naam in [x["naam"] for x in alles]:
                continue
            alles.append({"naam": naam, "huidig": naam == huidig,
                          "lokaal": naam in lokaal, "op_remote": naam in extern})
        return {"huidig": huidig or (lab.branch or ""), "branches": alles}

    async def checkout(self, lab_id: str, *, branch: str,
                       weggooien: bool = False) -> Dict[str, Any]:
        """Deze werkkopie op een andere branch zetten.

        Standaard WEIGERT hij bij ongecommit werk. Dat is geen betutteling:
        `git checkout` zou zulke wijzigingen meeslepen naar de andere branch,
        en dan staat je halve werk ergens waar het niet hoort zonder dat het
        ergens gemeld is. Met `weggooien` zeg je dat je het kwijt wilt -- en
        dan is het ook echt weg.

        `Lab.branch` gaat mee. Dat moet, want de rest van LabX zoekt het lab
        van een ticket op via die branch; zouden ze uiteenlopen, dan werkt de
        agent in een werkkopie die bij een ander ticket hoort.
        """
        lab, c = self._lab(lab_id)
        branch = (branch or "").strip()
        if not geldig(branch):
            raise HTTPException(status_code=400,
                                detail=f"'{branch}' is geen geldige branchnaam.")
        if branch == (lab.branch or ""):
            return {"ok": True, "branch": branch, "uitvoer": "Je staat al op die branch."}

        from models.lab import Lab

        botsing = (self.db.query(Lab)
                   .filter(Lab.cluster_id == lab.cluster_id, Lab.branch == branch,
                           Lab.id != lab.id).first())
        if botsing is not None:
            raise HTTPException(
                status_code=409,
                detail=(f"'{botsing.name}' staat al op '{branch}'. Twee labs op dezelfde "
                        f"branch werken langs elkaar heen in dezelfde code — gebruik dat "
                        f"lab, of kies een andere branch."))

        if not weggooien:
            staat = await self.status(lab_id)
            vuil = [b["pad"] for b in staat["bestanden"]]
            if vuil:
                raise HTTPException(
                    status_code=409,
                    detail=("Er staat ongecommit werk in deze werkkopie: "
                            + ", ".join(vuil[:8])
                            + (f" en nog {len(vuil) - 8}" if len(vuil) > 8 else "")
                            + ". Commit en push het eerst, of wissel mét weggooien — "
                              "dan is het weg."))

        env = {"TAK": branch}
        if weggooien:
            env["WEGGOOIEN"] = "1"
        uit = await self._git(lab, c, _CHECKOUT_SCRIPT, env=env)
        gelukt = int(uit.get("exit_code") or 0) == 0
        if gelukt:
            lab.branch = branch
            lab.updated_at = _nu()
            self.db.commit()
        log.infox("Branch-lab van branch gewisseld", lab=lab.id, naar=branch,
                  weggegooid=bool(weggooien), gelukt=gelukt)
        return {"ok": gelukt, "branch": branch if gelukt else (lab.branch or ""),
                "exit_code": uit.get("exit_code"), "uitvoer": uit.get("output") or ""}

    async def weggooien(self, lab_id: str) -> Dict[str, Any]:
        """Alles wat niet gecommit is, weg.

        Inclusief nieuwe bestanden die nog nergens in staan -- juist die zijn
        het makkelijkst te vergeten en het pijnlijkst om kwijt te raken.
        Daarom geeft `status` eerst de lijst, zodat het scherm kan laten zien
        wat er verdwijnt voordat je ja zegt.
        """
        lab, c = self._lab(lab_id)
        vooraf = await self.status(lab_id)
        uit = await self._git(lab, c, _WEGGOOI_SCRIPT)
        log.warningx("Ongecommit werk weggegooid", lab=lab.id,
                     bestanden=len(vooraf["bestanden"]))
        return {"ok": int(uit.get("exit_code") or 0) == 0,
                "weggegooid": [b["pad"] for b in vooraf["bestanden"]],
                "uitvoer": uit.get("output") or ""}

    async def bijwerken(self, lab_id: str, *, basis: str = "") -> Dict[str, Any]:
        """De remote ophalen en de basis in deze branch halen.

        Een branch-lab kloont één keer en daarna nooit meer. Werk je er drie
        dagen aan, dan zit er de `main` van drie dagen geleden onder -- en dat
        merk je pas bij de merge.
        """
        lab, c = self._lab(lab_id)
        env = {"BASIS": (basis or "main").strip()}
        uit = await self._git(lab, c, _BIJWERK_SCRIPT, env=env)
        return {"ok": int(uit.get("exit_code") or 0) == 0,
                "uitvoer": uit.get("output") or ""}

    # ── uitvoeren ────────────────────────────────────────────────────────────

    async def _git(self, lab, c: Cluster, script: str, *,
                   env: Optional[Dict[str, str]] = None,
                   lezen: bool = False) -> Dict[str, Any]:
        from services.lab.clusters import ClusterService
        from services.lab.lab_service import LabService, kloon_image

        svc = ClusterService(self.db)
        toegang = svc.toegang_voor(c)
        naam = (c.repo_url or "").rstrip("/").split("/")[-1].removesuffix(".git") or "repo"
        omgeving = {"DEST": naam, **(env or {}), **toegang.omgeving}
        if c.repo_url:
            omgeving["REPO_URL"] = _git_url(c.repo_url, toegang)

        rt = LabService(self.db).runtime_voor(lab)
        heel = toegang.aanloop + script if not lezen else script
        return await rt.run_wegwerp(
            image=await kloon_image(rt, lab.image),
            cmd=["sh", "-c", heel],
            netwerk=None,
            env=omgeving,
            volumes=None,
            timeout=GIT_TIJD,
            volume=lab.volume_name)

    def _lab(self, lab_id: str):
        from models.lab import Lab

        lab = self.db.get(Lab, lab_id)
        if lab is None or not lab.cluster_id:
            raise HTTPException(status_code=404,
                                detail="Dit is geen branch-lab van een cluster.")
        c = self.db.get(Cluster, lab.cluster_id)
        if c is None:
            raise HTTPException(status_code=404, detail="Cluster niet gevonden")
        return lab, c


def _git_url(repo_url: str, toegang) -> str:
    return _git.naar_ssh(repo_url) if toegang.soort == "ssh" else _git.naar_https(repo_url).url


def _soort(code: str) -> str:
    """Wat er met dit bestand gebeurd is, in gewone woorden."""
    if code == "??":
        return "nieuw"
    if "D" in code:
        return "verwijderd"
    if "A" in code:
        return "toegevoegd"
    if "R" in code:
        return "hernoemd"
    return "gewijzigd"


#: \x1e scheidt de stukken, \x00 de bestandsnamen. Beide komen in een pad niet
#: voor, dus er valt niets te ontsnappen -- in tegenstelling tot een nieuwe
#: regel, die in een bestandsnaam wél mag.
_STATUS_SCRIPT = (
    'cd "/workspace/$DEST" 2>/dev/null || { echo "geen werkkopie"; exit 1; }; '
    'git rev-parse --abbrev-ref HEAD; printf "\\036"; '
    'git log -1 --pretty=format:"%h %s (%ar)" 2>/dev/null; printf "\\036"; '
    'git rev-list --left-right --count "@{upstream}...HEAD" 2>/dev/null '
    '  | awk \'{print $2, $1}\'; printf "\\036"; '
    'if git ls-remote --exit-code --heads origin '
    '     "$(git rev-parse --abbrev-ref HEAD)" >/dev/null 2>&1; '
    '  then echo ja; else echo nee; fi; printf "\\036"; '
    'git status --porcelain=v1 -z --untracked-files=all'
)

#: Eerst de index leeg, dan precies wat jij koos, dan pas committen. En
#: pushen gebeurt ook als er niets te committen viel: "alleen even pushen wat
#: er al lag" is een geldige wens.
_PUSH_SCRIPT = (
    'set -e; cd "/workspace/$DEST"; '
    'git config user.email "lab@labx.local"; '
    'git config user.name "LabX"; '
    'git reset -q; '
    'if [ -n "$ALLES" ]; then '
    '  git add -A; '
    'elif [ -n "$PADEN_B64" ]; then '
    '  printf %s "$PADEN_B64" | base64 -d > /tmp/labx-paden; '
    '  git add -A --pathspec-from-file=/tmp/labx-paden --pathspec-file-nul; '
    'fi; '
    'if [ -n "$COMMIT_BERICHT" ]; then '
    '  if git diff --cached --quiet; then echo "niets te committen"; '
    '  else git commit -q -m "$COMMIT_BERICHT" && echo "gecommit"; fi; '
    'fi; '
    'echo "-- push naar $TAK"; '
    'git push --set-upstream "${REPO_URL:-origin}" "HEAD:refs/heads/$TAK" 2>&1; '
    'git log -1 --pretty=format:"nu op %h %s"'
)


#: De branches die er zijn. `\x1e` scheidt de stukken.
_BRANCHES_SCRIPT = (
    'cd "/workspace/$DEST" 2>/dev/null || { echo "geen werkkopie"; exit 1; }; '
    'git fetch --quiet origin 2>/dev/null; '
    'git rev-parse --abbrev-ref HEAD; printf "\\036"; '
    'git for-each-ref --format="%(refname:short)" refs/heads; printf "\\036"; '
    'git for-each-ref --format="%(refname:short)" refs/remotes/origin'
)

#: Van branch wisselen. Eerst eventueel opruimen, dan pas wisselen -- en de
#: branch van de remote halen als we hem hier nog niet hebben.
_CHECKOUT_SCRIPT = (
    'set -e; cd "/workspace/$DEST"; '
    'if [ -n "$WEGGOOIEN" ]; then '
    '  git reset --hard -q; git clean -fdq; echo "ongecommit werk weggegooid"; '
    'fi; '
    'git fetch --quiet origin 2>/dev/null || true; '
    'if git show-ref --verify --quiet "refs/heads/$TAK"; then '
    '  git checkout -q "$TAK"; '
    'elif git ls-remote --exit-code --heads origin "$TAK" >/dev/null 2>&1; then '
    '  git checkout -q -b "$TAK" --track "origin/$TAK"; '
    'else '
    '  git checkout -q -b "$TAK"; '
    'fi; '
    'echo "nu op $(git rev-parse --abbrev-ref HEAD)"; '
    'git log -1 --pretty=format:"%h %s"'
)

#: Alles wat niet gecommit is, weg. Ook nieuwe bestanden (`-fd`): juist die
#: vergeet je, en juist die zijn niet terug te halen.
_WEGGOOI_SCRIPT = (
    'set -e; cd "/workspace/$DEST"; '
    'git reset --hard -q; '
    'git clean -fdq; '
    'echo "werkkopie teruggezet op $(git rev-parse --abbrev-ref HEAD)"; '
    'git status --short | head -5'
)

#: De basis ophalen en erin halen. Een merge en geen rebase: een rebase
#: herschrijft commits die misschien al gepusht zijn, en dat is geen keuze
#: die een knop voor je hoort te maken.
_BIJWERK_SCRIPT = (
    'set -e; cd "/workspace/$DEST"; '
    'git fetch origin 2>&1 | tail -3; '
    'echo "-- $(git rev-list --count HEAD..origin/$BASIS) commit(s) achter op $BASIS"; '
    'git merge --no-edit "origin/$BASIS" 2>&1 | tail -10'
)
