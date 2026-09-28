"""Welke build er draait.

Aanleiding: na een uitrol stond de nieuwe interface op de server, maar liet de
browser nog de oude zien. Dat is van buitenaf niet te zien — om erachter te
komen moest je in devtools naar de bestandsnaam van een bundel turen.

Het nummer alleen lost dat niet op; de VERGELIJKING doet dat. Het scherm weet
welke versie in zijn eigen bundel gebakken is en legt die naast wat de server
zegt.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from routers.system_router import version  # noqa: E402


def test_de_versie_komt_uit_de_omgeving(monkeypatch):
    monkeypatch.setenv("LABX_VERSION", "v0.7.0")
    assert version() == {"versie": "v0.7.0"}


def test_zonder_versie_heet_het_dev(monkeypatch):
    monkeypatch.delenv("LABX_VERSION", raising=False)
    assert version() == {"versie": "dev"}


def test_het_endpoint_vraagt_geen_inlog():
    """Het scherm vraagt hem ook op het inlogscherm, en er staat niets in wat
    niet al in de tag op GitHub staat."""
    from routers.system_router import router

    route = next(r for r in router.routes if getattr(r, "path", "") == "/system/version")
    afhankelijkheden = [str(d) for d in (route.dependant.dependencies or [])]
    assert not any("require_user" in d for d in afhankelijkheden)


def test_de_dockerfiles_bakken_de_versie_in():
    """Zonder deze build-args staat er overal "dev" en zegt het scherm niets."""
    wortel = Path(__file__).resolve().parents[2]
    frontend = (wortel / "frontend" / "Dockerfile").read_text()
    backend = (wortel / "backend" / "Dockerfile").read_text()
    assert "ARG LABX_VERSION" in frontend and "VITE_LABX_VERSION" in frontend
    assert "ARG LABX_VERSION" in backend and "ENV LABX_VERSION" in backend

    workflow = (wortel / ".github" / "workflows" / "images.yml").read_text()
    # Beide images moeten hem meekrijgen; anders vergelijkt het scherm een
    # echte versie met "dev" en waarschuwt het ten onrechte.
    assert workflow.count("LABX_VERSION=") >= 2
