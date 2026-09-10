"""Bestandsnamen die van buiten komen.

Een naam uit een browser mag alles bevatten: spaties, aanhalingstekens, `../`,
een newline, of niets. Zulke namen belanden in een shell-commando in een
container, dus ze moeten hier al onschadelijk zijn — de tweede verdediging (het
pad als argument in plaats van in de tekst van het commando) zit in
lab_service, maar op één laag vertrouwen is er één te weinig.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.uploads import beschrijf, unieke_naam, veilige_naam  # noqa: E402


@pytest.mark.parametrize("invoer,verwacht", [
    ("rapport.pdf", "rapport.pdf"),
    ("mijn export.csv", "mijn_export.csv"),
    ("C:\\Users\\nick\\ding.xlsx", "ding.xlsx"),
    ("/etc/passwd", "passwd"),
    # Alles vóór de laatste / valt weg — dat is de hele bedoeling van een
    # bestandsNAAM, en het maakt een pad-ontsnapping onmogelijk in plaats van
    # onwaarschijnlijk.
    ("../../../etc/shadow", "shadow"),
    ("naam'; rm -rf /; echo '.txt", "echo_.txt"),
    ("regel\nmet\nnewlines.txt", "regel_met_newlines.txt"),
    ("....", "bestand"),
    ("", "bestand"),
    ("   ", "bestand"),
    (".bashrc", "bashrc"),
])
def test_naam_wordt_veilig(invoer, verwacht):
    assert veilige_naam(invoer) == verwacht


@pytest.mark.parametrize("invoer", [
    "rapport.pdf", "mijn export.csv", "../../etc/shadow", "naam'; rm -rf /; echo '.txt",
    "a b c\td\ne.txt", "«tekens».txt", "", "..",
])
def test_er_blijft_nooit_iets_gevaarlijks_over(invoer):
    naam = veilige_naam(invoer)
    assert naam, "een naam mag nooit leeg worden"
    assert "/" not in naam and "\\" not in naam
    assert not naam.startswith(".")
    assert all(c.isalnum() or c in "._-" for c in naam), naam


def test_lange_naam_houdt_zijn_extensie():
    naam = veilige_naam("x" * 300 + ".csv")
    assert len(naam) <= 120
    assert naam.endswith(".csv")


def test_botsing_krijgt_een_volgnummer():
    """Overschrijven is hier het verkeerde antwoord: twee keer export.csv zijn
    meestal twee verschillende exports."""
    assert unieke_naam("export.csv", []) == "export.csv"
    assert unieke_naam("export.csv", ["export.csv"]) == "export-2.csv"
    assert unieke_naam("export.csv", ["export.csv", "export-2.csv"]) == "export-3.csv"
    assert unieke_naam("LICENSE", ["LICENSE"]) == "LICENSE-2"


def test_beschrijving_noemt_pad_en_grootte():
    tekst = beschrijf([{"name": "a.csv", "path": "/workspace/uploads/a.csv", "bytes": 2048}])
    assert "/workspace/uploads/a.csv" in tekst
    assert "2 kB" in tekst
    # De agent moet weten dat de INHOUD er niet bij zit, anders gaat hij ervan
    # uit dat hij het bestand al gelezen heeft.
    assert "niet in dit bericht" in tekst


def test_geen_bijlagen_geeft_geen_blok():
    for leeg in (None, [], [{}], [{"name": "x"}]):
        assert beschrijf(leeg) == ""
