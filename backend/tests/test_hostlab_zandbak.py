"""De zandbak van de hostagent: blijft het écht in die map?

Dit is het hart van het hostlab. Een container wordt begrensd door de
container; een hostlab door niets anders dan dit profiel. Als deze tests
slagen is "het MOET in die map blijven" een belofte die je kunt waarmaken; zo
niet, dan is het een afspraak waar je op vertrouwt zonder reden.

Daarom draaien ze ECHTE commando's. Een test die alleen het profiel-tekstje
vergelijkt, bewijst niets: de vraag is niet of er het juiste in staat maar of
de kernel zich eraan houdt.

Alleen op macOS -- sandbox-exec bestaat nergens anders. Elders worden ze
overgeslagen in plaats van stilzwijgend "geslaagd".
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "host-agent"))

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="sandbox-exec bestaat alleen op macOS")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Een projectmap plus een geheim ernaast, allebei in een neptehuis.

    Het "thuis" wordt verlegd naar tmp_path: het profiel sluit de thuismap af,
    en een test die de echte thuismap gebruikt zou dus op de ene machine iets
    anders doen dan op de andere.
    """
    thuis = tmp_path / "thuis"
    (thuis / "project").mkdir(parents=True)
    (thuis / "project" / "binnen.txt").write_text("binnen\n")
    (thuis / "geheim.txt").write_text("niet lezen\n")
    (thuis / ".ssh").mkdir()
    (thuis / ".ssh" / "id_ed25519").write_text("privesleutel\n")
    monkeypatch.setenv("HOME", str(thuis))
    return thuis / "project"


def _run(project: Path, commando: str):
    import labx_host_agent as agent

    return agent.voer_uit(project, commando, timeout=30)


# ── wat moet kunnen ──────────────────────────────────────────────────────────

def test_binnen_de_projectmap_werkt_gewoon(project):
    code, uit, _ = _run(project, "cat binnen.txt && echo nieuw > nieuw.txt && ls")
    assert code == 0, uit
    assert "binnen" in uit
    assert (project / "nieuw.txt").exists()


def test_de_werkmap_is_het_project(project):
    code, uit, _ = _run(project, "pwd")
    assert code == 0
    assert str(project) in uit


# ── wat niet mag kunnen ──────────────────────────────────────────────────────

def test_buiten_de_map_lezen_kan_niet(project):
    code, uit, _ = _run(project, "cat ../geheim.txt")
    assert code != 0
    assert "not permitted" in uit.lower()
    assert "niet lezen" not in uit


def test_een_symlink_is_geen_achterdeur(project):
    """De test die ertoe doet. Een filter op commandotekst ziet hier een pad
    binnen de map en laat het door; de kernel kijkt naar waar de link heen
    wijst."""
    code, uit, _ = _run(
        project, "ln -sf ../geheim.txt ./sluiproute.txt && cat ./sluiproute.txt")
    assert code != 0
    assert "niet lezen" not in uit


def test_een_pad_in_een_variabele_helpt_ook_niet(project):
    code, uit, _ = _run(project, 'D=$HOME; cat "$D/geheim.txt"')
    assert code != 0
    assert "niet lezen" not in uit


def test_de_sleutels_blijven_dicht(project):
    code, uit, _ = _run(project, "cat ~/.ssh/id_ed25519")
    assert code != 0
    assert "privesleutel" not in uit


def test_buiten_de_map_schrijven_kan_niet(project):
    code, uit, _ = _run(project, "echo stiekem > ../stiekem.txt")
    assert code != 0
    assert not (project.parent / "stiekem.txt").exists()


# ── de Azure-sessie ──────────────────────────────────────────────────────────

def test_az_kijkt_in_de_projectmap_en_niet_in_je_thuismap(project):
    """Dit is wat een hostlab bij één klant houdt. Zonder dit zou de agent bij
    alle tenants kunnen waar jij ooit bent ingelogd."""
    code, uit, _ = _run(project, 'echo "$AZURE_CONFIG_DIR"')
    assert code == 0
    assert uit.strip() == str(project / ".azure")


def test_de_azure_sessie_van_de_host_is_niet_te_lezen(project, monkeypatch):
    thuis = Path(project).parent
    (thuis / ".azure").mkdir()
    (thuis / ".azure" / "msal_token_cache.json").write_text("tokens van andere klanten")

    code, uit, _ = _run(project, "cat ~/.azure/msal_token_cache.json")
    assert code != 0
    assert "andere klanten" not in uit


# ── het token lekt niet naar het commando ────────────────────────────────────

def test_het_agent_token_gaat_niet_mee_de_omgeving_in(project, monkeypatch):
    """Een commando dat `env` draait, mag het geheim van de agent niet zien."""
    monkeypatch.setenv("LABX_HOST_AGENT_TOKEN", "dit-is-geheim")
    code, uit, _ = _run(project, "env")
    assert code == 0
    assert "dit-is-geheim" not in uit


# ── het profiel zelf ─────────────────────────────────────────────────────────

def test_de_projectmap_komt_na_het_dichtzetten(project):
    """Volgorde is hier betekenis: in een sandbox-profiel wint de LAATSTE
    regel. Staat het openzetten van het project vóór het dichtzetten van de
    thuismap, dan sluit je je eigen project af."""
    import labx_host_agent as agent

    profiel = agent.zandbak_profiel(Path(project))
    assert profiel.index("(deny") < profiel.index("(allow file-read* file-write* (subpath")


def test_sandbox_exec_bestaat_eigenlijk():
    """Zonder dit draait een hostlab nergens op; beter hier merken dan bij het
    eerste commando van een nachtrun."""
    assert subprocess.run(["which", "sandbox-exec"],
                          capture_output=True).returncode == 0
