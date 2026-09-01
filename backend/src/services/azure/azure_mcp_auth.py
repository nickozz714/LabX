"""
services/azure/azure_mcp_auth.py

Resolves which AzureProfile (if any) a HOST MCP-server call should
authenticate as, and turns that profile into whatever the server's
transport actually needs:

- stdio servers (Azure MCP Server, Fabric MCP, Fabric RTI, Dev Box — every
  official Microsoft server that spawns the local `az`/Azure-SDK session
  rather than taking a header) get an isolated `AZURE_CONFIG_DIR`
  materialized from the profile, so two labs using different identities
  never share (or race on) one mutable host `~/.azure` session.
- http/sse servers get a live `Authorization: Bearer ...` header: minted
  fresh for a service_principal profile (client-credentials flow), used
  as-is for a bearer profile. An msal_bundle can't be turned into a fresh
  Bearer token without an interactive refresh flow, so it's stdio-only.

Voor een SYNC (de toolslijst ophalen) geldt een eigen volgorde: eerst
server.sync_azure_profile_id, dan pas de gewone standaard. Zie resolve_profile.

Resolution order: the ACTIVE LAB's own azure_profile_id wins — a chat is
bound to one lab, so "which identity" is naturally a per-lab choice. The
server's own azure_profile_id is the fallback for host-only calls with no
lab bound (a sync/test call from the MCP-servers page).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from component_logging import get_logger
from models.azure_profile import AzureProfile
from models.mcp_server import MCPServer

log = get_logger(__name__)

_PROFILE_DIR_ROOT = Path(os.environ.get("LABX_DATA_DIR", "/data")) / "azure_mcp_profiles"


def resolve_profile(db: Session, server: MCPServer, lab_id: Optional[str],
                    *, purpose: str = "runtime") -> Optional[AzureProfile]:
    """Welke Azure-identiteit deze aanroep gebruikt.

    purpose="sync" is het ophalen van de toolslijst: dat doet LabX zelf, zonder
    lab en zonder gebruiker, dus daarvoor geldt eerst het aparte
    sync-profiel. purpose="runtime" is echt werk: dan wint het lab, want een
    aanroep hoort te draaien met de rechten van het lab waarin hij plaatsvindt,
    en pas als dat lab niets heeft (of er is geen lab — een session-scope
    server) valt hij terug op de standaard van de server. Het sync-profiel doet
    daar niet aan mee: het is een dienstidentiteit, geen werkidentiteit.
    """
    profile_id = None
    if purpose == "sync":
        profile_id = server.sync_azure_profile_id or server.azure_profile_id
    else:
        if lab_id:
            from models.lab import Lab
            lab = db.get(Lab, lab_id)
            if lab and lab.azure_profile_id:
                profile_id = lab.azure_profile_id
        if not profile_id:
            profile_id = server.azure_profile_id
    if not profile_id:
        return None
    return db.get(AzureProfile, profile_id)


def _decrypt_payload(profile: AzureProfile) -> Dict[str, Any]:
    from utils.crypto import decrypt
    if not profile.secret_encrypted:
        return {}
    try:
        return json.loads(decrypt(profile.secret_encrypted))
    except Exception as exc:  # noqa: BLE001 — a bad/rotated key must not crash the call
        log.warningx("azure_mcp_auth: kon profiel-secret niet ontsleutelen", profile=profile.name,
                     error=str(exc)[:200])
        return {}


async def stdio_env_for_profile(profile: AzureProfile) -> Dict[str, str]:
    """An isolated AZURE_CONFIG_DIR for a stdio Azure/Fabric MCP subprocess,
    materialized from the profile so it never touches the backend's own host
    az session or another profile's."""
    payload = _decrypt_payload(profile)
    az_dir = _PROFILE_DIR_ROOT / str(profile.id)
    az_dir.mkdir(parents=True, exist_ok=True)

    if profile.kind == "msal_bundle":
        for fname in ("msal_token_cache.json", "azureProfile.json", "service_principal_entries.json"):
            if payload.get(fname):
                (az_dir / fname).write_text(payload[fname], encoding="utf-8")
        return {"AZURE_CONFIG_DIR": str(az_dir)}

    if profile.kind == "service_principal":
        # az writes its own token cache into AZURE_CONFIG_DIR on login; reuse
        # the same dir across calls so az can refresh instead of relogging in
        # every time — only actually log in if there's no cached session yet.
        if not (az_dir / "azureProfile.json").exists():
            import asyncio
            import shutil
            if not shutil.which("az"):
                log.warningx("azure_mcp_auth: 'az' CLI ontbreekt, kan service_principal niet inloggen")
                return {"AZURE_CONFIG_DIR": str(az_dir)}
            proc = await asyncio.create_subprocess_exec(
                "az", "login", "--service-principal",
                "-u", payload.get("client_id", ""), "-p", payload.get("client_secret", ""),
                "--tenant", payload.get("tenant_id", ""),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "AZURE_CONFIG_DIR": str(az_dir)},
            )
            _out, err = await proc.communicate()
            if proc.returncode != 0:
                log.warningx("azure_mcp_auth: service-principal az-login mislukt",
                             profile=profile.name, error=err.decode("utf-8", "replace")[:300])
        return {"AZURE_CONFIG_DIR": str(az_dir)}

    # bearer: no az-CLI session possible from a raw token.
    log.warningx("azure_mcp_auth: 'bearer'-profiel kan geen az-CLI-sessie leveren voor een stdio-server "
                "— gebruik hiervoor een msal_bundle- of service_principal-profiel", profile=profile.name)
    return {}


async def bearer_header_for_profile(profile: AzureProfile, *,
                                    scope: str = "https://management.azure.com/.default") -> Dict[str, str]:
    payload = _decrypt_payload(profile)
    if profile.kind == "bearer" and payload.get("token"):
        return {"Authorization": f"Bearer {payload['token']}"}
    if profile.kind == "service_principal":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://login.microsoftonline.com/{payload['tenant_id']}/oauth2/v2.0/token",
                    data={"client_id": payload["client_id"], "client_secret": payload["client_secret"],
                         "scope": scope, "grant_type": "client_credentials"},
                )
                resp.raise_for_status()
                token = resp.json().get("access_token") or ""
            return {"Authorization": f"Bearer {token}"} if token else {}
        except Exception as exc:  # noqa: BLE001
            log.warningx("azure_mcp_auth: kon geen SP-token minten voor http/sse-mcp", profile=profile.name,
                         error=str(exc)[:200])
            return {}
    log.warningx("azure_mcp_auth: 'msal_bundle'-profiel kan niet gemint worden tot een Bearer-header voor "
                "een http/sse-server — gebruik een service_principal-profiel of een stdio-server.",
                profile=profile.name)
    return {}
