"""Een daemon die een fout geeft is niet een daemon zonder containers.

Gemeten op 21-10-2026: een MacBook kwam uit slaap, ssh werkte al maar Docker
Desktop nog niet. `docker ps -a` gaf een fout, `list_managed` maakte daar een
lege lijst van, en reconcile concludeerde dat alle containers weg waren: drie
labs op "stopped" met hun container-id weggegooid, terwijl die containers
gewoon draaiden. Daarna strandde elke start op "The container name is already
in use" -- een fout over LabX' eigen administratie.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _runtime(monkeypatch, antwoorden):
    """Een DockerRuntime waarvan de CLI geeft wat de test voorschrijft."""
    from services.lab.docker_runtime import DockerRuntime

    rt = DockerRuntime()

    async def nep(*args, timeout=0, **kw):
        return antwoorden(list(args))

    monkeypatch.setattr(rt, "_run_cli", nep)
    return rt


def test_een_mislukte_lijst_komt_omhoog(monkeypatch):
    """"Ik kon het niet vragen" moet omhoog, zodat reconcile die machine
    overslaat in plaats van hem leeg te verklaren."""
    rt = _runtime(monkeypatch,
                  lambda a: (1, "", "Cannot connect to the Docker daemon"))

    with pytest.raises(RuntimeError) as fout:
        asyncio.run(rt.list_managed())
    assert "Cannot connect" in str(fout.value)


def test_een_geslaagde_lege_lijst_blijft_gewoon_leeg(monkeypatch):
    """Het verschil is precies dit: niets gevonden is een antwoord."""
    rt = _runtime(monkeypatch, lambda a: (0, "", ""))

    assert asyncio.run(rt.list_managed()) == []


def test_een_draaiende_naamgenoot_wordt_weer_in_gebruik_genomen(monkeypatch):
    """De naam is van het lab, dus die container IS het lab. Een tweede
    maken kan niet en weggooien zou het werk weggooien."""
    gedaan = []

    def antwoorden(a):
        gedaan.append(a[0])
        if a[0] == "ps":
            return (0, "abc123\trunning\n", "")
        return (0, "", "")

    rt = _runtime(monkeypatch, antwoorden)

    async def ok(*a, **k):
        gedaan.append(a[0] if a else "?")
        return "nieuw111"

    monkeypatch.setattr(rt, "_cli_ok", ok)
    monkeypatch.setattr(rt, "ensure_network", lambda *a, **k: _klaar())

    uit = asyncio.run(rt.run_container(
        name="labx-lab-1", image="node:22", volume="v", cpu_limit=1,
        mem_limit_mb=512, allow_network=True))

    assert uit == "abc123"
    assert "run" not in gedaan, "er mag geen tweede container bij komen"
    assert "rm" not in gedaan, "en het werk mag niet weggegooid worden"


def test_een_stilstaande_naamgenoot_gaat_weg_en_dan_pas_een_nieuwe(monkeypatch):
    gedaan = []

    def antwoorden(a):
        gedaan.append(a[0])
        if a[0] == "ps":
            return (0, "oud999\texited\n", "")
        return (0, "nieuw111\n", "")

    rt = _runtime(monkeypatch, antwoorden)

    async def ok(*a, **k):
        gedaan.append(a[0] if a else "?")
        return "nieuw111"

    monkeypatch.setattr(rt, "_cli_ok", ok)
    monkeypatch.setattr(rt, "ensure_network", lambda *a, **k: _klaar())

    asyncio.run(rt.run_container(
        name="labx-lab-1", image="node:22", volume="v", cpu_limit=1,
        mem_limit_mb=512, allow_network=True))

    assert gedaan.index("rm") < gedaan.index("run"), "eerst opruimen, dan starten"


async def _niets():
    return ""


def _klaar():
    return _niets()
