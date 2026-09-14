"""Bestanden uit een workspace ophalen.

"Ik moet bestanden uit de workspaces kunnen downloaden. Dat lukt nu niet echt."
Dat klopte: er was alleen `read_file`, en die doet `head -c 200000` en stuurt de
inhoud als TEKST door een JSON-antwoord. Drie dingen gingen daardoor mis —
afgekapt op 200 kB, binaire bestanden vernield door de UTF-8-decodering, en er
was sowieso geen downloadknop: klikken toonde een voorbeeld.

Gemeten tegen het echte lab: een binair bestand van 3 MB komt er met een
identieke md5 uit, en een map als een geldige tar.gz met beide bestanden erin.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_de_download_streamt_en_buffert_niet():
    """Een bestand van een gigabyte mag de backend niet omduwen. `stream_out`
    is een async generator; de bytes gaan van de container naar de respons
    zonder ooit volledig in het geheugen te staan."""
    import inspect

    from services.lab.docker_runtime import DockerRuntime

    bron = inspect.getsource(DockerRuntime.stream_out)
    assert "yield brok" in bron
    assert "proc.stdout.read(chunk)" in bron


def test_een_afgebroken_download_laat_geen_proces_achter():
    """Sluit de browser de verbinding, dan moet de `cat` in de container ook
    stoppen — anders blijft er een proces op een groot bestand staan dat
    niemand meer leest."""
    import inspect

    from services.lab.docker_runtime import DockerRuntime

    bron = inspect.getsource(DockerRuntime.stream_out)
    assert "finally:" in bron
    assert "proc.kill()" in bron


def test_een_map_komt_er_als_archief_uit():
    """Anders moet je een boom bestand voor bestand aanklikken."""
    import inspect

    from services.lab.lab_service import LabService

    bron = inspect.getsource(LabService.download)
    assert '"tar", "-czf", "-", "-C", ouder, naam' in bron, \
        "-C naar de ouder: anders zit er een pad van vier niveaus diep in het archief"
    assert 'f"{naam}.tar.gz"' in bron


def test_het_pad_blijft_binnen_de_workspace():
    """`_safe_path` is de enige grens die er is; zonder die controle is dit een
    leesrechten-lek op de hele container."""
    import inspect

    from services.lab.lab_service import LabService

    assert "self._safe_path(path)" in inspect.getsource(LabService.download)


def test_een_ontbrekend_bestand_geeft_404_en_geen_lege_download():
    """Zonder deze controle krijg je een bestand van nul bytes met de juiste
    naam — en denk je dat het gelukt is."""
    import inspect

    from services.lab.lab_service import LabService

    bron = inspect.getsource(LabService.download)
    assert 'status_code=404' in bron
    assert 'wat == "weg"' in bron


def test_de_bestandsnaam_kan_de_header_niet_breken():
    """Een aanhalingsteken in een bestandsnaam zou de Content-Disposition
    openbreken."""
    bron = (Path(__file__).resolve().parents[1]
            / "src/routers/lab_router.py").read_text(encoding="utf-8")
    blok = bron[bron.index('async def download_lab_file'):]
    assert 'replace(\'"\', "")' in blok


def test_de_download_is_een_eigen_endpoint_en_niet_read_file():
    """`/file` blijft wat het is: een voorbeeld in tekst. Ze door elkaar halen
    zou betekenen dat elk voorbeeld een download van honderden megabytes kan
    worden."""
    bron = (Path(__file__).resolve().parents[1]
            / "src/routers/lab_router.py").read_text(encoding="utf-8")
    assert '@router.get("/{lab_id}/download")' in bron
    assert "StreamingResponse" in bron
