"""Bestandsbewerkingen in een lab: hernoemen, verplaatsen, verwijderen.

Het gevaar zit niet in de bewerking maar in het PAD. Een naam komt van een
client, en zonder controle sleep je met "../../etc" iets buiten de werkmap --
of verwijder je de werkmap zelf. Beide paden van een verplaatsing gaan daarom
door dezelfde controle, ook het doel: anders kon je iets wel veilig oppakken
maar onveilig neerleggen.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi import HTTPException  # noqa: E402
from services.lab.lab_service import LabService  # noqa: E402


@pytest.mark.parametrize("ruw,verwacht", [
    ("/workspace/data.csv", "/workspace/data.csv"),
    ("data.csv", "/workspace/data.csv"),
    ("/data.csv", "/workspace/data.csv"),
    ("/workspace/map/", "/workspace/map"),
    ("", "/workspace"),
])
def test_een_pad_blijft_binnen_de_werkmap(ruw, verwacht):
    assert LabService._safe_path(ruw) == verwacht


@pytest.mark.parametrize("ruw", [
    "../etc/passwd",
    "/workspace/../etc/passwd",
    "/workspace/map/../../root",
])
def test_omhoog_lopen_wordt_geweigerd(ruw):
    with pytest.raises(HTTPException) as fout:
        LabService._safe_path(ruw)
    assert fout.value.status_code == 400


def test_de_werkmap_zelf_kan_niet_verwijderd_worden():
    """`rm -rf /workspace` is geen bestandsbewerking maar een ongeluk.

    De controle zit vóór de aanroep naar de container, dus hij geldt ook als
    er geen lab draait -- vandaar dat dit zonder container te testen is.
    """
    import asyncio
    import inspect

    bron = inspect.getsource(LabService.verwijder_bestand)
    assert 'target == "/workspace"' in bron
    assert "De werkmap zelf kan niet" in bron
    del asyncio


def test_verplaatsen_controleert_ook_de_BESTEMMING():
    """Anders pak je iets veilig op en leg je het onveilig neer."""
    import inspect

    bron = inspect.getsource(LabService.hernoem_bestand)
    # Beide paden door _safe_path, in één regel.
    assert "self._safe_path(van), self._safe_path(naar)" in bron


def test_paden_gaan_als_argument_naar_de_shell():
    """Een bestandsnaam met een aanhalingsteken erin mag niet uit de quotes
    breken en de rest als commando laten uitvoeren."""
    import inspect

    for functie in (LabService.hernoem_bestand, LabService.verwijder_bestand,
                    LabService.maak_map):
        bron = inspect.getsource(functie)
        assert '"$1"' in bron, f"{functie.__name__} zet het pad in de tekst"
        # Het pad staat ná "sh" in de argumentenlijst, niet in de commandotekst.
        assert '"sh",' in bron
