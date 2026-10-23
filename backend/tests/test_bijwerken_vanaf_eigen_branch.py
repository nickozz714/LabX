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


def test_het_tellen_vergelijkt_met_de_eigen_branch_op_de_server():
    """Dat is de vergelijking die ertoe doet voor een push."""
    assert "refs/remotes/origin/$B...HEAD" in git._STATUS_SCRIPT


def test_de_push_zet_geen_url_als_upstream():
    """Dit was de stille oorzaak onder alles.

    `git push --set-upstream "$REPO_URL"` zet de URL neer waar de naam
    `origin` hoort. Daarna geeft `@{upstream}` "not stored as a
    remote-tracking branch" en valt elke telling terug op nul -- het scherm
    zegt 0 vooruit en 0 achter terwijl er 65 commits klaarstaan en drie van
    jou nog niet gepusht zijn. Een stand die altijd nul is, lijkt op "bij".
    """
    # Let op de vorm: `--set-upstream-to=origin/...` mag juist wel, en dat
    # bevat dezelfde letters. Het gaat om de variant op de push zelf.
    assert "push --set-upstream" not in git._PUSH_SCRIPT


def test_de_push_herstelt_de_tracking_wel():
    """Zonder upstream werkt `git status` in het lab zelf ook niet meer;
    daarom zetten we hem op de NAAM, en werken we de remote-tracking ref bij
    die na een geslaagde push per definitie gelijk is aan HEAD."""
    assert 'git update-ref "refs/remotes/origin/$TAK" HEAD' in git._PUSH_SCRIPT
    assert 'git branch --set-upstream-to="origin/$TAK"' in git._PUSH_SCRIPT


def test_het_tellen_hangt_niet_van_de_tracking_af():
    """Een lab dat al bestaat heeft die kapotte upstream nog. De telling moet
    daar doorheen werken, anders blijft het scherm liegen tot iemand opnieuw
    pusht."""
    assert "@{upstream}" not in git._STATUS_SCRIPT
    assert "refs/remotes/origin/$B...HEAD" in git._STATUS_SCRIPT
