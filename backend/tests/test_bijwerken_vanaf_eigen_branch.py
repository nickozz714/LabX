"""Bijwerken hoort standaard vanaf je EIGEN branch te gaan, niet vanaf main.

Gemeten op 23-10-2026. Een branch-lab van drie dagen oud stond 2 commits
vooruit en 65 achter op zijn eigen branch -- collega's hadden doorgewerkt. De
push werd geweigerd ("merge the remote changes"), en de knop Bijwerken haalde
main op: 41 conflicten, een halve merge, en niets meer te committen. Met de
eigen branch waren het er nul, want dat is dezelfde lijn.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import services.lab.branch_git as git  # noqa: E402


def test_zonder_keuze_pakt_hij_de_branch_van_het_lab():
    bron = inspect.getsource(git.BranchGit.bijwerken)
    assert '"BASIS": (basis or lab.branch or "main")' in bron, (
        "main hoort de laatste terugval te zijn, niet de eerste keuze")


def test_main_blijft_mogelijk():
    """Soms wil je main er juist in; dat moet kunnen, als keuze."""
    assert "basis: str = \"\"" in inspect.getsource(git.BranchGit.bijwerken)


def test_de_stand_wordt_eerst_opgehaald():
    """Anders is "achter" een getal van de laatste keer dat iemand toevallig
    fetchte -- en dan zegt het scherm dat je bij bent terwijl er 65 commits
    op je branch staan."""
    assert "git fetch --quiet origin" in git._STATUS_SCRIPT
    assert git._STATUS_SCRIPT.index("git fetch") < git._STATUS_SCRIPT.index("rev-list"), (
        "ophalen moet vóór het tellen")


def test_het_tellen_vergelijkt_met_de_eigen_upstream():
    """@{upstream} is origin/<deze branch>; dat is de vergelijking die ertoe
    doet voor een push."""
    assert "@{upstream}...HEAD" in git._STATUS_SCRIPT
