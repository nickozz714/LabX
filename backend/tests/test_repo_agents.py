"""De agents die in de repo zelf staan, draaien met het gereedschap van het lab.

Een repo brengt vaak zijn eigen agents mee in `.claude/agents/*.md`. LabX
vond ze niet, om een saaie reden: de Claude-CLI draait op de SERVER en zoekt
subagents relatief aan zijn eigen werkmap, terwijl de repo in het lab staat.

De oplossing verplaatst de CLI NIET. We lezen het bestand uit het lab en
geven de inhoud mee als instructie van een gewone run, zodat het gereedschap
dat van het lab blijft.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.repo_agents import (  # noqa: E402
    KOP, MAP, _omschrijving, _repomap, _splits_frontmatter,
)

VOORBEELD = """---
name: pr-decomposer
description: Methodically decomposes an oversized "spec" PR into an ordered
  stack of small, reviewable PRs.
tools: Read, Edit, Bash(git:*), Bash(gh pr view:*)
---

You decompose oversized pull requests and then build the result.
"""


def test_de_frontmatter_gaat_eraf():
    """Die beschrijft hem voor Claude Code en gaat over tools die hier anders
    heten; meesturen zou alleen verwarring opleveren."""
    kop, tekst = _splits_frontmatter(VOORBEELD)

    assert "name: pr-decomposer" in kop
    assert tekst.startswith("You decompose")
    assert "tools:" not in tekst


def test_een_bestand_zonder_frontmatter_blijft_heel():
    kop, tekst = _splits_frontmatter("Gewoon een instructie.\n")

    assert kop == ""
    assert tekst == "Gewoon een instructie."


def test_de_omschrijving_wordt_een_regel():
    """In de frontmatter staat hij over drie regels; in een keuzelijst wil je
    er één."""
    uit = _omschrijving(VOORBEELD)

    assert uit.startswith("Methodically decomposes")
    assert "\n" not in uit


def test_de_kop_zegt_waar_hij_is_en_waarmee_hij_werkt():
    """Niet om hem te sturen -- zijn instructie is van het team -- maar om
    het misverstand voor te zijn dat zijn gereedschap ontbreekt."""
    tekst = KOP.format(map="/workspace/Intelligenthive", geheim="ih-token")

    assert "/workspace/Intelligenthive" in tekst
    assert "gereedschap van het lab" in tekst
    assert "GH_TOKEN={{secret:ih-token}}" in tekst, (
        "de verwijzing, niet de waarde")


def test_de_kop_noemt_de_extra_als_gh_ontbreekt():
    """Anders gaat een agent een omweg zoeken voor iets wat met één vinkje
    op te lossen is."""
    assert "lab-extra" in KOP and "GitHub CLI" in KOP


def test_de_repomap_komt_uit_de_repo_url():
    class C:
        repo_url = "https://github.com/beeminds-data/Intelligenthive.git"

    assert _repomap(C()) == "/workspace/Intelligenthive"


def test_zonder_repo_url_valt_hij_terug_op_workspace():
    class C:
        repo_url = None

    assert _repomap(C()) == "/workspace"


@pytest.mark.parametrize("naam", ["../../etc/passwd", "a b", "x;rm -rf /", ""])
def test_een_agentnaam_mag_geen_pad_of_commando_worden(naam):
    """Die string gaat een shellcommando in."""
    import asyncio

    from fastapi import HTTPException

    from services.lab.repo_agents import RepoAgents

    with pytest.raises(HTTPException) as fout:
        asyncio.run(RepoAgents(None).lees("lab-1", naam))
    assert fout.value.status_code == 400


def test_de_map_is_waar_claude_code_hem_verwacht():
    assert MAP == ".claude/agents"


def test_het_paneel_haalt_niets_op_bij_het_openen():
    """De lijst komt uit het LAB: een wegwerpcontainer starten, bij een lab op
    een andere machine ook nog over SSH. Dat is seconden werk voor iemand die
    alleen de Git-knop wilde. Hetzelfde geldt voor het PR-paneel, dat bij
    GitHub langsgaat."""
    wortel = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components"
    for naam in ("RepoAgents.tsx", "PullRequest.tsx"):
        bron = (wortel / naam).read_text(encoding="utf-8")
        assert "useEffect" not in bron, (
            f"{naam} hoort pas te laden als de gebruiker erom vraagt")
