"""Rondgang-vastheid van de Jira-vertaling.

De bug die dit bestand bewaakt: LabX schreef Markdown terug als een reeks kale
alinea's, waardoor `# *Sales document:*` in Jira veranderde in `- Sales
document:` — koppen weg, vet weg, lijst geen lijst meer. Bij elke sync opnieuw.

De eis is daarom rondgang, niet leesbaarheid: wat erin gaat moet er hetzelfde
uitkomen, en een tweede rondgang mag niets meer veranderen.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.boards.sync.adf import to_adf, to_markdown  # noqa: E402

GEVALLEN = [
    "# Kop een\n\n## Kop twee\n\n- punt\n- nog een punt",
    "**Vet**, _cursief_, `code`, ~~door~~ en een [link](https://example.test/x).",
    "1. eerste\n2. tweede",
    "- buiten\n  - binnen\n  - ook binnen\n- weer buiten",
    "```python\nprint('hallo')\n```",
    "> citaat\n> tweede regel",
    "| Kop | Twee |\n| --- | --- |\n| a | b |",
    "|  |  |\n| --- | --- |\n| a | b |",          # tabel zonder kop
    "- [ ] open\n- [x] klaar",
    "Regel een\nRegel twee in dezelfde alinea",
    "---",
    "![plaatje](bijlage:abc123)",
    "**_vet en cursief_**",
]


@pytest.mark.parametrize("markdown", GEVALLEN)
def test_rondgang_verandert_niets(markdown):
    assert to_markdown(to_adf(markdown)) == markdown


@pytest.mark.parametrize("markdown", GEVALLEN)
def test_tweede_rondgang_is_stabiel(markdown):
    een = to_markdown(to_adf(markdown))
    assert to_markdown(to_adf(een)) == een


def test_kop_blijft_kop():
    """Precies de regressie: een kop mag geen opsommingsteken worden."""
    adf = to_adf("# *Sales document:*\n\n## Sales header\n\n- Order type")
    soorten = [n["type"] for n in adf["content"]]
    assert soorten == ["heading", "heading", "bulletList"]
    assert adf["content"][0]["attrs"]["level"] == 1
    assert to_markdown(adf).splitlines()[0].startswith("# ")


def test_vet_overleeft_de_rondgang():
    adf = to_adf("**RFC ticket nummer: #INC-22949**")
    marks = adf["content"][0]["content"][0]["marks"]
    assert {m["type"] for m in marks} == {"strong"}


def test_harde_spatie_is_geen_alineagrens():
    """Jira staat vol met alinea's die uit één \\xa0 bestaan. Wie die als lege
    regel leest, plakt de alinea's eromheen aan elkaar."""
    md = "Eerste\n\n \n\nTweede"
    doc = to_adf(md)
    teksten = [n.get("type") for n in doc["content"]]
    assert teksten == ["paragraph", "paragraph", "paragraph"]


def test_lege_tekst_geeft_geldig_document():
    for leeg in (None, "", "   "):
        doc = to_adf(leeg)
        assert doc["type"] == "doc" and doc["version"] == 1 and doc["content"]


def test_platte_tekst_blijft_leesbaar():
    assert to_markdown({"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "gewoon"}]}]}) == "gewoon"
