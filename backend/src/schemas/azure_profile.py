"""schemas/azure_profile.py — Pydantic DTOs for Azure profiles, ported from
ND3X-public/src/schemas/azure_profile.py minus org/project scoping."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AzureProfileCreate(BaseModel):
    name: str
    kind: str = "msal_bundle"
    description: Optional[str] = None
    files: Optional[Dict[str, str]] = None          # msal_bundle
    tenant_id: Optional[str] = None                  # service_principal
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token: Optional[str] = None                       # bearer
    # entra_app: de app-registratie waar de gebruiker zich bij aanmeldt. Geen
    # secret nodig — een device-code-login is een public client.
    scopes: Optional[List[str]] = None


class AzureProfileUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    files: Optional[Dict[str, str]] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token: Optional[str] = None
    scopes: Optional[List[str]] = None


class AzureProfileRead(BaseModel):
    id: int
    name: str
    kind: str
    description: Optional[str] = None
    has_secret: bool
    identity: Optional[Dict[str, Any]] = None
    # Alleen voor entra_app: "er staat iets versleuteld" is daar niet hetzelfde
    # als "je bent ingelogd" — het profiel bestaat al zodra de app-gegevens
    # erin staan, en pas na de device-code-login is er een verversingstoken.
    logged_in: Optional[bool] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    scopes: Optional[List[str]] = None
    created_at: str
    updated_at: str


class AzureProfileSyncRequest(BaseModel):
    target: str  # host | lab
    lab_id: Optional[str] = None
    az_dir: str = "/root/.azure"
