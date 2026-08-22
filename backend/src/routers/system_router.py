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


@router.get("/docker", dependencies=[Depends(require_user)])
async def docker_status():
    return await DockerRuntime().diagnose()
