"""
services/skills/cli_skills.py

LabX-skills wegschrijven als ECHTE Claude Code-skills, zodat de agent ze kan
vinden in plaats van ze alleen in zijn systeemprompt te krijgen.

**Het probleem.** Een skill in LabX was tekst: zijn instructies werden bij elke
beurt in de systeemprompt geplakt. Wie er een SKILL.md in plakte (met
frontmatter en al) zag hem dus wel voorbijkomen als losse tekst, maar de agent
kon hem niet vinden als hij zocht — er bestond geen skills-map, en de
`Skill`-tool stond niet eens in de lijst van toegestane tools. "Ik heb een
skill toegevoegd en de chat kan hem niet vinden" was dus precies wat er stond
te gebeuren.

**Wat dit doet.** Elke skill die voor een gesprek geldt, krijgt een map met een
`SKILL.md` in de skills-map van de CLI. Staat er al frontmatter in de
instructies (iemand plakte een echte SKILL.md), dan blijft die staan zoals hij
is; anders maken we er zelf een kop bij van de naam en de beschrijving.

**Wat we NIET aanraken.** Alleen mappen met ons eigen merkteken (`.labx`).
Een skill die iemand met de hand in die map heeft gezet, is niet van ons en
blijft staan — ook als LabX hem niet kent.

**Waarom skills met `usage_scope = "lab"` hier niet bij zitten.** De
skills-map van de CLI geldt voor de hele instantie, niet per lab: een bestand
dat er staat, is in elk gesprek te vinden. Een lab-skill hoort juist alleen te
gelden in een lab dat hem toelaat, en die blijft daarom de weg van de
systeemprompt volgen (zie chat_agent._enabled_domain_skill_names). Zodra de
CLI een skills-map per project kent, kan dat gelijkgetrokken worden.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from component_logging import get_logger

log = get_logger(__name__)

# De skills-map van de CLI. Die staat in de home van het proces dat de CLI
# draait — dat is de backend-container, niet het lab.
def skills_dir() -> Path:
    thuis = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(thuis) / "skills"


# Ons merkteken. Zonder dit zouden we bij het opruimen ook skills weghalen die
# iemand zelf in die map heeft gezet.
MERKTEKEN = ".labx"


def slug(naam: str) -> str:
    kaal = re.sub(r"[^a-z0-9]+", "-", (naam or "").lower()).strip("-")
    return kaal or "skill"


def _heeft_frontmatter(tekst: str) -> bool:
    return (tekst or "").lstrip().startswith("---")


def skill_bestand(naam: str, beschrijving: str, instructies: str) -> str:
    """De inhoud van SKILL.md.

    Plakte iemand een echte SKILL.md in het instructieveld, dan is die al
    compleet — er een tweede kop omheen zetten maakt hem juist kapot."""
    body = (instructies or "").strip()
    if _heeft_frontmatter(body):
        return body + "\n"
    # De beschrijving is wat de agent ziet als hij zoekt; zonder dat weet hij
    # niet wanneer deze skill aan de beurt is.
    omschrijving = (beschrijving or naam).replace("\n", " ").strip()
    return (f"---\nname: {slug(naam)}\ndescription: {omschrijving}\n---\n\n"
            f"# {naam}\n\n{body}\n")


def _van_ons(map_: Path) -> bool:
    return (map_ / MERKTEKEN).exists()


def sync_cli_skills(db: Session) -> Dict[str, Any]:
    """De skills-map gelijktrekken met wat er in LabX staat.

    Draait bij het opstarten en na elke wijziging aan een skill. Idempotent:
    een ongewijzigde skill wordt niet opnieuw geschreven, zodat een tijdstempel
    niet elke keer verspringt."""
    from models.skill import Skill

    doel = skills_dir()
    rijen: List[Skill] = (db.query(Skill)
                          .filter(Skill.is_enabled == True, Skill.is_system == False)  # noqa: E712
                          .all())
    gewenst = {}
    for s in rijen:
        scope = (getattr(s, "usage_scope", None) or "beide").lower()
        if scope == "lab":
            # Hoort bij een lab, niet bij de instantie — zie de moduletekst.
            continue
        gewenst[slug(s.name)] = skill_bestand(s.name, s.description, s.instructions)

    geschreven, verwijderd = 0, 0
    try:
        doel.mkdir(parents=True, exist_ok=True)
        for naam, inhoud in gewenst.items():
            map_ = doel / naam
            map_.mkdir(parents=True, exist_ok=True)
            (map_ / MERKTEKEN).write_text("Beheerd door LabX; met de hand wijzigen "
                                          "wordt overschreven.\n", encoding="utf-8")
            bestand = map_ / "SKILL.md"
            if bestand.exists() and bestand.read_text(encoding="utf-8") == inhoud:
                continue
            bestand.write_text(inhoud, encoding="utf-8")
            geschreven += 1
        for map_ in doel.iterdir():
            if not map_.is_dir() or map_.name in gewenst:
                continue
            if not _van_ons(map_):
                continue        # niet van ons: afblijven
            shutil.rmtree(map_, ignore_errors=True)
            verwijderd += 1
    except Exception as exc:  # noqa: BLE001
        # Bewust ALLES: een skills-map die niet te schrijven is mag nooit een
        # beurt of een herstart tegenhouden, en dat geldt net zo goed voor een
        # pad dat niet eens een geldig pad is (dat geeft een ValueError, geen
        # OSError). De instructies gaan sowieso mee in de prompt.
        log.warningx("Skills-map bijwerken overgeslagen", pad=str(doel), error=str(exc)[:200])
        return {"ok": False, "fout": str(exc)[:300]}

    if geschreven or verwijderd:
        log.infox("CLI-skills bijgewerkt", pad=str(doel),
                  geschreven=geschreven, verwijderd=verwijderd, totaal=len(gewenst))
    return {"ok": True, "geschreven": geschreven, "verwijderd": verwijderd,
            "totaal": len(gewenst), "pad": str(doel)}
