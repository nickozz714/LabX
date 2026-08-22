"""schemas/azure_profile.py — Pydantic DTOs for Azure profiles, ported from
ND3X-public/src/schemas/azure_profile.py minus org/project scoping."""
from __future__ import annotations

from typing import Any, Dict, Optional

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


class AzureProfileUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    files: Optional[Dict[str, str]] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token: Optional[str] = None


class AzureProfileRead(BaseModel):
    id: int
    name: str
    kind: str
    description: Optional[str] = None
    has_secret: bool
    identity: Optional[Dict[str, Any]] = None
    created_at: str
    updated_at: str


class AzureProfileSyncRequest(BaseModel):
    target: str  # host | lab
    lab_id: Optional[str] = None
    az_dir: str = "/root/.azure"
