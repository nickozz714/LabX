"""services/lab/basisbranch.py — waar is deze branch van afgetakt?

Dit bestaat omdat het antwoord "main" bijna goed is, en bijna goed hier het
slechtste soort fout oplevert: een diff van honderden bestanden die er
geloofwaardig uitziet. Werk je op een `release/2026.09`, dan staat alles wat
sinds die release op main kwam ineens in jouw pull request.

De agent van de repo zelf zegt het al:

    Trunk means the spec PR's own base branch -- main or the active
    release/YYYY.MM. Read it with `gh pr view --json baseRefName`;
    never assume main.

Dus in deze volgorde:

1. **Wat jij kiest.** Altijd leidend; dit is uiteindelijk een feit dat jij
   weet en wij niet.
2. **De basis van de open pull request.** Dat is waar de branch heen gaat, en
   dat is per definitie het antwoord.
3. **De standaardbranch van de repo** -- als terugval, en dan zeggen we er
   bij dat het een aanname is.

Wat hier met opzet NIET gebeurt: raden uit de historie. Gemeten op
23-10-2026: zes verschillende branches deelden hetzelfde vertakkingspunt,
omdat er ooit een release in deze branch gemerged was. Een gok met zes
even goede kandidaten is geen gok maar een muntje, en hij ziet er in het
scherm precies zo zeker uit als een echt antwoord.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from component_logging import get_logger

log = get_logger(__name__)


def bepaal(gh, branch: str, gekozen: Optional[str] = None) -> Dict[str, Any]:
    """De basis, met erbij waar hij vandaan komt."""
    if (gekozen or "").strip():
        return {"basis": gekozen.strip(), "bron": "jouw keuze", "zeker": True}

    try:
        pr = gh.pr_voor_branch(branch)
    except Exception as exc:  # noqa: BLE001 — geen PR kunnen lezen is geen storing
        log.warningx("Basis niet uit de pull request te halen",
                     branch=branch, reden=str(exc)[:160])
        pr = None

    if pr:
        basis = ((pr.get("base") or {}).get("ref") or "").strip()
        if basis:
            return {"basis": basis,
                    "bron": f"de open pull request #{pr.get('number')}",
                    "zeker": True}

    return {"basis": gh.standaardbranch(),
            "bron": "de standaardbranch van de repo — er is geen open pull "
                    "request om het aan af te lezen",
            "zeker": False}
