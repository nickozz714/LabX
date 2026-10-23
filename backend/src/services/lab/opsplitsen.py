"""services/lab/opsplitsen.py — één branch in meerdere kleine PR's.

Het probleem: aan een feature-branch zit na een week werk van alles vast dat
los van elkaar te beoordelen is -- een migratie, een stuk UI, een opgeruimde
helper. Als één pull request is dat een muur waar een reviewer doorheen moet;
als drie is het drie keer tien minuten.

**Dit is een voorstel, geen knop die het stilletjes doet.** Wat bij elkaar
hoort, is een oordeel: het hangt af van wat er los van elkaar kán draaien, en
dat weet een mens beter dan een regel. Het model groepeert, jij ziet de
verdeling met de reden erbij, en pas daarna worden er branches gemaakt.

Hoe het splitsen werkt, en waarom zo:

- **Per bestand, niet per commit.** Commits op een feature-branch lopen door
  elkaar heen -- "wip", "fix typo", een merge van main. De eindtoestand van
  een bestand is wel eenduidig.
- **Elke branch begint bij de basis**, niet bij de vorige. Dan staan ze naast
  elkaar in plaats van op elkaar, en kan er eentje afvallen zonder de rest
  mee te nemen.
- **Wat niemand toewees, blijft achter.** Geen restgroep die stiekem alles
  opvangt: als het model iets niet kan plaatsen, hoor jij dat te zien.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

#: Hoeveel bestanden er hoogstens aan het model worden voorgelegd. Daarboven
#: is een automatische verdeling toch niet te vertrouwen en kun je beter zelf
#: kijken -- en wordt de prompt een boek.
MAX_BESTANDEN = 300

_PROMPT = """Je verdeelt de wijzigingen van een feature-branch over meerdere
kleinere pull requests.

De branch heet `{branch}` en gaat naar `{basis}`. Dit zijn de gewijzigde
bestanden, met hoeveel regels erbij en eraf zijn gegaan:

{bestanden}

{commits}

Verdeel ze in 2 tot 5 groepen die LOS VAN ELKAAR te beoordelen en te mergen
zijn. Dat laatste is de harde eis: een groep die niet zonder een andere kan
bouwen, is geen eigen pull request.

Houd aan:
- Bij elkaar wat samen één verandering vormt, ook als het in verschillende
  mappen staat. Een route, zijn test en zijn type horen bij elkaar.
- Een opgeruimde helper of een hernoeming die overal doorwerkt, is vaak een
  eigen eerste groep: die gaat als eerste erin en de rest bouwt erop.
- Kun je een bestand niet plaatsen, laat het dan WEG. Niet in een restgroep
  duwen.

Antwoord met alleen JSON, zonder tekst eromheen:

{{"groepen": [
  {{"branch": "<naam zonder spaties, kort, in de stijl van de bestaande>",
    "titel": "<één regel, wat deze PR doet>",
    "reden": "<waarom dit los kan>",
    "bestanden": ["pad/een.ts", "pad/twee.ts"]}}
]}}"""



#: Het script dat één groep tot een eigen branch maakt.
#:
#: Waarom `git checkout <branch> -- <paden>` en geen cherry-pick: commits op
#: een feature-branch lopen door elkaar heen en raken vaak meer bestanden dan
#: deze groep. De EINDTOESTAND van een bestand is wel eenduidig, en dat is
#: precies wat in de pull request hoort te staan.
#:
#: `git checkout -B` en niet `-b`: draai je het opnieuw na een mislukte poging,
#: dan bestaat de branch al en is "bestaat al" geen fout maar het begin.
_DEEL_SCRIPT = (
    'set -e; cd "/workspace/$DEST"; '
    'git config user.email "lab@labx.local"; '
    'git config user.name "LabX"; '
    'git fetch --quiet origin 2>/dev/null || true; '
    'HUIDIG=$(git rev-parse --abbrev-ref HEAD); '
    'git checkout -B "$NIEUW" "origin/$BASIS" >/dev/null 2>&1; '
    'printf %s "$PADEN_B64" | base64 -d > /tmp/labx-deel; '
    # Geen `--` ervoor: dan leest git `--pathspec-from-file` als een pad in
    # plaats van als optie, en haalt hij een bestand met die naam op.
    'git checkout "$HUIDIG" --pathspec-from-file=/tmp/labx-deel --pathspec-file-nul; '
    'git add -A --pathspec-from-file=/tmp/labx-deel --pathspec-file-nul; '
    'if git diff --cached --quiet; then echo "LEEG"; else '
    '  git commit -q -m "$BERICHT"; '
    '  git push "${REPO_URL:-origin}" "HEAD:refs/heads/$NIEUW" 2>&1 | tail -3; '
    '  git update-ref "refs/remotes/origin/$NIEUW" HEAD; '
    '  echo "GEPUSHT $(git rev-parse --short HEAD)"; '
    'fi; '
    'git checkout -q "$HUIDIG"'
)

class Opsplitsen:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ── het voorstel ─────────────────────────────────────────────────────────

    async def voorstel(self, lab_id: str, basis: str = "") -> Dict[str, Any]:
        """Wat er te verdelen valt, en hoe het model het zou doen."""
        from services.lab.basisbranch import bepaal
        from services.lab.pull_requests import PullRequests

        prs = PullRequests(self.db)
        lab, c = prs._lab(lab_id)
        gh = prs._github(c)
        branch = (lab.branch or "").strip()
        # De branch waarvan JIJ bent afgetakt, niet de standaardbranch: met
        # de verkeerde basis verdeelt hij honderden bestanden die niet van
        # jou zijn. Zie basisbranch.py.
        gekozen = bepaal(gh, branch, basis)
        basis = gekozen["basis"]

        vgl = gh.vergelijk(basis, branch)
        bestanden = [
            {"pad": r.get("filename", ""),
             "erbij": int(r.get("additions") or 0),
             "eraf": int(r.get("deletions") or 0),
             "status": r.get("status", "")}
            for r in (vgl.get("files") or [])
        ]
        if not bestanden:
            raise HTTPException(
                status_code=400,
                detail=f"Er zit geen verschil tussen {branch} en {basis}; "
                       f"dan valt er niets te verdelen.")
        if len(bestanden) > MAX_BESTANDEN:
            raise HTTPException(
                status_code=400,
                detail=(f"{len(bestanden)} gewijzigde bestanden is te veel om "
                        f"betrouwbaar te verdelen. Splits eerst met de hand, of "
                        f"werk in kleinere branches."))

        commits = [((r.get("commit") or {}).get("message") or "").splitlines()[0]
                   for r in (vgl.get("commits") or [])]
        groepen = await self._groepeer(branch, basis, bestanden, commits)

        toegewezen = {p for g in groepen for p in g["bestanden"]}
        return {
            "branch": branch,
            "basis": basis,
            "basis_bron": gekozen["bron"],
            "basis_zeker": gekozen["zeker"],
            "bestanden": bestanden,
            "groepen": groepen,
            # Expliciet, want dit is de plek waar een verdeling stil kan
            # mislukken: een bestand dat niemand kreeg, merk je anders pas
            # als de PR's al open staan.
            "niet_toegewezen": [b["pad"] for b in bestanden
                                if b["pad"] not in toegewezen],
        }

    async def _groepeer(self, branch: str, basis: str,
                        bestanden: List[Dict[str, Any]],
                        commits: List[str]) -> List[Dict[str, Any]]:
        regels = "\n".join(
            f"- {b['pad']} (+{b['erbij']}/-{b['eraf']}, {b['status']})"
            for b in bestanden)
        commitregels = ("De commits op deze branch:\n"
                        + "\n".join(f"- {t}" for t in commits[:30])) if commits else ""
        ruw = await self._vraag(_PROMPT.format(
            branch=branch, basis=basis, bestanden=regels, commits=commitregels))

        data = _lees_json(ruw)
        bekend = {b["pad"] for b in bestanden}
        groepen: List[Dict[str, Any]] = []
        for g in (data.get("groepen") or []):
            paden = [p for p in (g.get("bestanden") or []) if p in bekend]
            if not paden:
                continue
            groepen.append({
                "branch": _veilige_tak(str(g.get("branch") or ""), branch),
                "titel": str(g.get("titel") or "").strip(),
                "reden": str(g.get("reden") or "").strip(),
                "bestanden": paden,
            })
        if not groepen:
            raise HTTPException(
                status_code=502,
                detail="Het model gaf geen bruikbare verdeling terug. Probeer het "
                       "opnieuw, of verdeel met de hand.")
        return groepen


    # ── uitvoeren ────────────────────────────────────────────────────────────

    async def maak(self, lab_id: str, *, groepen: List[Dict[str, Any]],
                   basis: str = "", pr: bool = False) -> Dict[str, Any]:
        """De goedgekeurde verdeling omzetten in branches.

        Eén voor één, en een groep die misgaat stopt de rest niet: je hebt
        meer aan drie van de vier branches plus een duidelijke melding dan aan
        niets en een traceback.
        """
        import base64

        from services.lab.branch_git import BranchGit
        from services.lab.pull_requests import PullRequests

        prs = PullRequests(self.db)
        lab, c = prs._lab(lab_id)
        git = BranchGit(self.db)
        from services.lab.basisbranch import bepaal

        # Dezelfde basis als in het voorstel: anders bouw je iets anders dan
        # wat je hebt goedgekeurd.
        basis = bepaal(prs._github(c), lab.branch or "", basis)["basis"]

        uitslag: List[Dict[str, Any]] = []
        for g in groepen:
            tak = _veilige_tak(str(g.get("branch") or ""), lab.branch or "")
            paden = [str(p) for p in (g.get("bestanden") or []) if str(p).strip()]
            if not paden:
                uitslag.append({"branch": tak, "ok": False,
                                "melding": "Geen bestanden in deze groep."})
                continue
            bericht = (str(g.get("titel") or "").strip()
                       or f"Deel van {lab.branch}")
            try:
                uit = await git._git(lab, c, _DEEL_SCRIPT, env={
                    "NIEUW": tak,
                    "BASIS": basis,
                    "BERICHT": bericht,
                    "PADEN_B64": base64.b64encode(
                        "\0".join(paden).encode()).decode(),
                })
                tekst = str(uit.get("output") or "")
                gelukt = "GEPUSHT" in tekst
                uitslag.append({
                    "branch": tak, "ok": gelukt, "bestanden": len(paden),
                    "melding": ("Branch gemaakt en gepusht." if gelukt
                                else "Niets gewijzigd ten opzichte van de basis."
                                if "LEEG" in tekst else tekst[-300:]),
                })
            except Exception as exc:  # noqa: BLE001 — één groep mag de rest niet breken
                log.warningx("Deelbranch maken mislukte", branch=tak,
                             error=str(exc)[:200])
                uitslag.append({"branch": tak, "ok": False,
                                "melding": str(exc)[:300]})

        gelukt = [r for r in uitslag if r["ok"]]
        log.infox("Branch opgesplitst", lab=lab.name, bron=lab.branch,
                  gemaakt=len(gelukt), gevraagd=len(groepen))
        return {"ok": bool(gelukt), "basis": basis, "resultaat": uitslag}

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
            # Geen gereedschap: het model deelt bestanden in, het raakt de
            # repo niet aan. Dat doet LabX, in het lab, na jouw akkoord.
            enable_tool_search=False,
        )
        return (await provider.chat(prompt)).text


def _veilige_tak(naam: str, bron: str) -> str:
    """Een branchnaam waar git geen bezwaar tegen maakt.

    Met het voorvoegsel van de bronbranch erbij als het model er geen koos:
    `feature/x` wordt dan `feature/x-migratie` en niet een losse `migratie`
    die tussen de hoofdbranches gaat zwerven.
    """
    schoon = "".join(ch if (ch.isalnum() or ch in "-_/") else "-"
                     for ch in naam.strip().lower()).strip("-/")
    schoon = "-".join(deel for deel in schoon.split("-") if deel)
    if not schoon:
        schoon = "deel"
    if "/" not in schoon and "/" in bron:
        schoon = f"{bron.rsplit('/', 1)[0]}/{schoon}"
    return schoon[:100]


def _lees_json(ruw: str) -> Dict[str, Any]:
    """Het model antwoordt soms met een codeblok eromheen."""
    tekst = (ruw or "").strip()
    if "```" in tekst:
        deel = tekst.split("```")[1]
        tekst = deel[4:].strip() if deel.lower().startswith("json") else deel.strip()
    begin, eind = tekst.find("{"), tekst.rfind("}")
    if begin == -1 or eind == -1:
        return {}
    try:
        return json.loads(tekst[begin:eind + 1])
    except json.JSONDecodeError:
        return {}
