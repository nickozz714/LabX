"""routers/system_router.py — health + Docker diagnostics. GET /system/docker
is the direct fix for issue 1 ("geeft aan dat er geen Docker aanwezig is"):
instead of a blind 503, the UI gets {cli_present, daemon_up, in_container,
socket_mounted, docker_host, hint} to act on."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from authentication import require_user
from services.lab.docker_runtime import DockerRuntime

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/version")
def version():
    """Welke build hier draait.

    Zonder inlog, want het scherm vraagt hem ook op het inlogscherm — en er
    staat niets in wat niet al in de tag op GitHub staat.

    Het punt is de VERGELIJKING: de interface weet welke versie in haar eigen
    bundel gebakken is, en kan die naast deze leggen. Wijken ze af, dan kijk je
    naar een oude pagina en zegt het scherm dat, in plaats van dat je in
    devtools naar een bestandsnaam moet turen."""
    import os

    return {"versie": os.environ.get("LABX_VERSION") or "dev"}


@router.get("/docker", dependencies=[Depends(require_user)])
async def docker_status():
    return await DockerRuntime().diagnose()
