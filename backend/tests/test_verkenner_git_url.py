"""Repo-URL's omzetten naar de vorm waarmee LabX kan klonen.

Dit stond in CLUSTERS.md als blokker voor het eerste branch-lab: de remote van
IntelligentHive is een SSH-adres, en LabX kloont over HTTPS met een token.
Zo'n URL ongemoeid doorgeven levert een sleutelfout op in een container die
geen sleutels heeft -- en dat is een foutmelding waar niemand de oorzaak in
leest.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.git_url import normaliseer  # noqa: E402


def test_scp_adres_wordt_https():
    uit = normaliseer("git@github.com:beeminds-data/Intelligenthive.git")
    assert uit.url == "https://github.com/beeminds-data/Intelligenthive.git"
    assert uit.omgezet is True
    assert "SSH" in uit.melding, "stil omzetten is hoe iemand een uur kwijt is"


def test_ssh_url_met_poort_wordt_https():
    assert normaliseer("ssh://git@github.com:22/org/repo.git").url == "https://github.com/org/repo.git"


def test_https_blijft_https_en_meldt_niets():
    uit = normaliseer("https://github.com/org/repo.git")
    assert uit.url == "https://github.com/org/repo.git" and uit.omgezet is False and uit.melding == ""


def test_een_andere_host_dan_github_gaat_net_zo_goed():
    assert normaliseer("git@gitlab.intern.nl:team/app.git").url == "https://gitlab.intern.nl/team/app.git"


def test_lege_invoer_blijft_leeg():
    assert normaliseer("   ").url == ""
