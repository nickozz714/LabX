"""Eén branch in meerdere kleine pull requests.

Dit is een VOORSTEL en geen knop die het stilletjes doet: wat bij elkaar
hoort is een oordeel. Het model groepeert, de mens ziet de verdeling met de
reden erbij, en pas daarna ontstaan er branches die iemand moet opruimen.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.lab.opsplitsen import (  # noqa: E402
    _DEEL_SCRIPT, _lees_json, _veilige_tak,
)


# ── de branchnaam ────────────────────────────────────────────────────────────

def test_de_naam_erft_het_voorvoegsel_van_de_bron():
    """Anders gaat er een losse `migratie` tussen de hoofdbranches zwerven."""
    assert _veilige_tak("migratie", "feature/data-quality") == "feature/migratie"


def test_een_eigen_voorvoegsel_blijft_staan():
    assert _veilige_tak("chore/opruimen", "feature/x") == "chore/opruimen"


def test_spaties_en_rare_tekens_gaan_eruit():
    """Git weigert ze, en dan faalt het pas bij de push."""
    uit = _veilige_tak("De migratie (deel 1)!", "feature/x")
    assert " " not in uit and "(" not in uit
    assert uit.startswith("feature/")


def test_een_lege_naam_levert_nog_steeds_iets_op():
    assert _veilige_tak("", "feature/x") == "feature/deel"


# ── het antwoord van het model ───────────────────────────────────────────────

def test_json_in_een_codeblok_wordt_gelezen():
    """Het model zet er vaak ```json omheen."""
    ruw = 'Hier is de verdeling:\n```json\n{"groepen": [{"branch": "a"}]}\n```\n'
    assert _lees_json(ruw)["groepen"][0]["branch"] == "a"


def test_onleesbaar_antwoord_geeft_leeg_en_geen_uitzondering():
    """Een kapot antwoord hoort een nette melding op te leveren, geen
    traceback halverwege het verdelen."""
    assert _lees_json("ik weet het niet") == {}


# ── het script ───────────────────────────────────────────────────────────────

def test_elke_branch_begint_bij_de_basis():
    """Niet bij de vorige: dan staan ze naast elkaar in plaats van op elkaar,
    en kan er eentje afvallen zonder de rest mee te nemen."""
    assert 'git checkout -B "$NIEUW" "origin/$BASIS"' in _DEEL_SCRIPT


def test_er_wordt_niet_gecherrypickt():
    """Commits op een feature-branch lopen door elkaar heen en raken vaak
    meer bestanden dan deze groep. De eindtoestand is wel eenduidig."""
    assert "cherry-pick" not in _DEEL_SCRIPT
    assert 'git checkout "$HUIDIG" --pathspec-from-file' in _DEEL_SCRIPT


def test_de_pathspec_optie_staat_niet_achter_een_dubbele_streep():
    """Dan leest git hem als een PAD en haalt hij een bestand met die naam
    op in plaats van de lijst te gebruiken."""
    assert '-- --pathspec-from-file' not in _DEEL_SCRIPT


def test_een_lege_groep_wordt_gemeld_en_niet_gecommit():
    """Een branch zonder verschil met de basis is een lege PR."""
    assert 'if git diff --cached --quiet; then echo "LEEG"' in _DEEL_SCRIPT


def test_het_lab_blijft_op_zijn_eigen_branch_staan():
    """Je bent nog aan het werk; na het opsplitsen hoort je werkkopie te
    staan waar hij stond."""
    assert _DEEL_SCRIPT.rstrip().endswith('git checkout -q "$HUIDIG"')


def test_de_tracking_wordt_meteen_goed_gezet():
    """Dezelfde fout als in de gewone push: een URL als upstream maakt elke
    telling nul."""
    assert 'git update-ref "refs/remotes/origin/$NIEUW" HEAD' in _DEEL_SCRIPT
    assert "push --set-upstream" not in _DEEL_SCRIPT
