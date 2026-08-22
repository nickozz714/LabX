"""services/azure/az_login_service.py — host-side (LabX backend container)
az-login import, simplified from ND3X's az_login_service. Writes the token
bundle to AZURE_CONFIG_DIR (or ~/.azure) so `az` calls made by the backend
process itself (not a lab) use this identity. Requires the `az` CLI to be
present in the backend image if a service-principal login is requested —
this is a thinner surface than ND3X's, which is fine for a POC: sync-to-lab
(the common case) doesn't need it at all."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from component_logging import get_logger

log = get_logger(__name__)

AZ_BUNDLE_FILES = ("msal_token_cache.json", "azureProfile.json", "service_principal_entries.json")


def _az_dir() -> Path:
    return Path(os.environ.get("AZURE_CONFIG_DIR") or (Path.home() / ".azure"))


def import_token_files(*, msal_token_cache: str, azure_profile: str,
                       service_principal_entries: Optional[str] = None) -> Dict[str, Any]:
    d = _az_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "msal_token_cache.json").write_text(msal_token_cache, encoding="utf-8")
    (d / "azureProfile.json").write_text(azure_profile, encoding="utf-8")
    if service_principal_entries:
        (d / "service_principal_entries.json").write_text(service_principal_entries, encoding="utf-8")
    return {"az_dir": str(d), "written": True}


async def start_service_principal(*, tenant_id: str, client_id: str, client_secret: str) -> Dict[str, Any]:
    import shutil
    if not shutil.which("az"):
        return {"ok": False,
               "hint": "Azure CLI ('az') ontbreekt in het LabX-backend-image — installeer 'm of "
                       "sync dit profiel naar een lab in plaats van de host."}
    proc = await asyncio.create_subprocess_exec(
        "az", "login", "--service-principal", "-u", client_id, "-p", client_secret,
        "--tenant", tenant_id,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        return {"ok": False, "error": (err or out).decode("utf-8", "replace")[:800]}
    return {"ok": True, "az_dir": str(_az_dir())}
