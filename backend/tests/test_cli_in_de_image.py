"""De Claude-CLI moet bij het BOUWEN al blijken te werken.

Gemeten op 22-10-2026: npm voert postinstall-scripts niet meer uit zonder
toestemming, en juist dat script van deze CLI haalt de native binary op.
De image bouwde netjes door, npm zei niets, en elke agent-run faalde met

    Error: claude native binary not installed.

Elk ticket, op elk bord, in elk soort lab -- en pas zichtbaar een halve dag
nadat de image klaar was. Deze twee regels in de Dockerfile houden dat tegen;
deze test houdt die regels op hun plek.
"""
from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parents[2] / "backend" / "Dockerfile").read_text()


def test_de_postinstall_mag_draaien():
    """Zonder deze vlag installeert npm het pakket zonder de binary."""
    assert "--allow-scripts=@anthropic-ai/claude-code" in DOCKERFILE


def test_de_image_rekent_het_zelf_na():
    """Anders bouwt een kapotte CLI door tot in productie."""
    regels = [r.strip() for r in DOCKERFILE.splitlines()]
    assert any(r.startswith("&& claude --version") for r in regels), (
        "de build moet falen als de CLI niet start")


def test_de_controle_staat_na_de_installatie():
    """Ervoor zou altijd slagen op een image die hem nog niet heeft."""
    assert (DOCKERFILE.index("npm install -g --allow-scripts")
            < DOCKERFILE.index("&& claude --version"))
