"""Waar is deze branch van afgetakt?

"main" is bijna altijd goed, en bijna goed is hier het slechtste soort fout:
werk je op een `release/2026.09`, dan staat alles wat sinds die release op
main kwam ineens in jouw pull request — een diff van honderden bestanden die
er geloofwaardig uitziet.

De agent van de repo zelf zegt het al: never assume main.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.basisbranch import bepaal  # noqa: E402


class _GH:
    def __init__(self, pr=None, standaard="main", stuk=False):
        self._pr, self._standaard, self._stuk = pr, standaard, stuk

    def pr_voor_branch(self, _branch):
        if self._stuk:
            raise RuntimeError("403")
        return self._pr

    def standaardbranch(self):
        return self._standaard


def test_jouw_keuze_wint_altijd():
    """Dit is een feit dat jij weet en wij niet."""
    uit = bepaal(_GH(pr={"number": 7, "base": {"ref": "main"}}),
                 "feature/x", "release/2026.09")

    assert uit["basis"] == "release/2026.09"
    assert uit["zeker"] is True


def test_de_open_pull_request_beslist():
    """Daar gaat de branch heen; dat is per definitie het antwoord."""
    uit = bepaal(_GH(pr={"number": 7, "base": {"ref": "release/2026.09"}}), "feature/x")

    assert uit["basis"] == "release/2026.09"
    assert uit["zeker"] is True
    assert "#7" in uit["bron"]


def test_zonder_pull_request_is_het_een_zichtbare_aanname():
    """Dan valt hij terug op de standaardbranch, maar niet stilletjes."""
    uit = bepaal(_GH(standaard="main"), "feature/x")

    assert uit["basis"] == "main"
    assert uit["zeker"] is False
    assert "geen open pull request" in uit["bron"]


def test_geen_pr_kunnen_lezen_is_geen_storing():
    """Het token mag misschien niet bij pull requests; dan werkt de rest
    gewoon door met de standaardbranch."""
    uit = bepaal(_GH(stuk=True, standaard="main"), "feature/x")

    assert uit["basis"] == "main"
    assert uit["zeker"] is False


def test_er_wordt_niet_uit_de_historie_geraden():
    """Gemeten: zes branches deelden hetzelfde vertakkingspunt omdat er ooit
    een release in deze branch gemerged was. Een gok met zes even goede
    kandidaten ziet er in het scherm precies zo zeker uit als een antwoord."""
    import inspect

    import services.lab.basisbranch as mod

    bron = inspect.getsource(mod)
    assert "merge-base" not in bron and "rev-list" not in bron
