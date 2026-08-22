"""
services/azure/azure_profile_service.py

CRUD + verify + sync for named Azure profiles, ported from
ND3X-public/src/services/azure_profile_service.py (single-tenant: no
org_id/project_id scoping). Secrets are Fernet-encrypted at rest and only
decrypted at the moment of a verify or a sync.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from component_logging import get_logger
from models.azure_profile import AZURE_PROFILE_KINDS, AzureProfile
from schemas.azure_profile import AzureProfileCreate, AzureProfileUpdate
from utils.crypto import decrypt, encrypt

log = get_logger(__name__)

AZ_BUNDLE_FILES = ("msal_token_cache.json", "azureProfile.json", "service_principal_entries.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_jwt_claims(token: str) -> Dict[str, Any]:
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not decode token: {exc}"}


class AzureProfileService:
    def __init__(self, db: Session):
        self.db = db

    def list(self) -> List[AzureProfile]:
        return self.db.query(AzureProfile).order_by(AzureProfile.name.asc()).all()

    def get_or_404(self, profile_id: int) -> AzureProfile:
        row = self.db.get(AzureProfile, profile_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Azure-profiel niet gevonden")
        return row

    def to_dict(self, p: AzureProfile) -> Dict[str, Any]:
        identity = None
        if p.identity_json:
            try:
                identity = json.loads(p.identity_json)
            except Exception:  # noqa: BLE001
                identity = None
        return {
            "id": p.id, "name": p.name, "kind": p.kind, "description": p.description,
            "has_secret": bool(p.secret_encrypted), "identity": identity,
            "created_at": p.created_at, "updated_at": p.updated_at,
        }

    def _payload_from_input(self, kind: str, data: Any) -> Dict[str, Any]:
        if kind == "msal_bundle":
            files = {k: v for k, v in (data.files or {}).items() if k in AZ_BUNDLE_FILES and v}
            if not files.get("msal_token_cache.json") or not files.get("azureProfile.json"):
                raise HTTPException(status_code=400, detail=(
                    "msal_bundle vereist minimaal msal_token_cache.json en azureProfile.json."))
            return files
        if kind == "service_principal":
            if not (data.tenant_id and data.client_id and data.client_secret):
                raise HTTPException(status_code=400, detail=(
                    "service_principal vereist tenant_id, client_id en client_secret."))
            return {"tenant_id": data.tenant_id, "client_id": data.client_id, "client_secret": data.client_secret}
        if kind == "bearer":
            if not data.token:
                raise HTTPException(status_code=400, detail="bearer vereist een token.")
            return {"token": data.token}
        raise HTTPException(status_code=400, detail=f"kind moet een van {AZURE_PROFILE_KINDS} zijn")

    def _decrypt(self, p: AzureProfile) -> Dict[str, Any]:
        if not p.secret_encrypted:
            raise HTTPException(status_code=400, detail="Dit profiel heeft geen opgeslagen secret.")
        try:
            return json.loads(decrypt(p.secret_encrypted))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Kon profiel-secret niet ontsleutelen: {exc}")

    def create(self, data: AzureProfileCreate) -> AzureProfile:
        kind = (data.kind or "msal_bundle").strip().lower()
        if kind not in AZURE_PROFILE_KINDS:
            raise HTTPException(status_code=400, detail=f"kind moet een van {AZURE_PROFILE_KINDS} zijn")
        payload = self._payload_from_input(kind, data)
        now = _now_iso()
        row = AzureProfile(name=data.name.strip(), kind=kind, description=data.description,
                           secret_encrypted=encrypt(json.dumps(payload)),
                           created_at=now, updated_at=now)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update(self, profile_id: int, data: AzureProfileUpdate) -> AzureProfile:
        row = self.get_or_404(profile_id)
        if data.name is not None:
            row.name = data.name.strip()
        if data.description is not None:
            row.description = data.description
        if any(v is not None for v in (data.files, data.tenant_id, data.client_id, data.client_secret, data.token)):
            payload = self._payload_from_input(row.kind, data)
            row.secret_encrypted = encrypt(json.dumps(payload))
            row.identity_json = None
        row.updated_at = _now_iso()
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete(self, profile_id: int) -> None:
        row = self.get_or_404(profile_id)
        self.db.delete(row)
        self.db.commit()

    def capture_from_host(self, *, name: str, description: Optional[str] = None) -> AzureProfile:
        import os
        from pathlib import Path
        host_dir = Path(os.environ.get("AZURE_CONFIG_DIR") or (Path.home() / ".azure"))
        files: Dict[str, str] = {}
        for fname in AZ_BUNDLE_FILES:
            fp = host_dir / fname
            if fp.exists():
                try:
                    files[fname] = fp.read_text(encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
        if not files.get("msal_token_cache.json") or not files.get("azureProfile.json"):
            raise HTTPException(status_code=400, detail="Geen host az-sessie gevonden. Log eerst in met 'az login'.")
        data = AzureProfileCreate(name=name, kind="msal_bundle", description=description, files=files)
        return self.create(data)

    async def verify(self, profile_id: int) -> Dict[str, Any]:
        row = self.get_or_404(profile_id)
        payload = self._decrypt(row)
        identity: Dict[str, Any] = {"kind": row.kind}
        if row.kind == "bearer":
            claims = _decode_jwt_claims(payload.get("token") or "")
            identity.update({k: claims.get(k) for k in ("tid", "upn", "appid", "aud", "name") if claims.get(k)})
            if claims.get("error"):
                identity["error"] = claims["error"]
        elif row.kind == "service_principal":
            identity.update({"tenant_id": payload.get("tenant_id"), "client_id": payload.get("client_id")})
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"https://login.microsoftonline.com/{payload['tenant_id']}/oauth2/v2.0/token",
                        data={"client_id": payload["client_id"], "client_secret": payload["client_secret"],
                             "scope": "https://management.azure.com/.default",
                             "grant_type": "client_credentials"},
                    )
                    resp.raise_for_status()
                    token = resp.json().get("access_token") or ""
                claims = _decode_jwt_claims(token)
                identity.update({k: claims.get(k) for k in ("tid", "appid", "aud") if claims.get(k)})
            except Exception as exc:  # noqa: BLE001 — verify is best-effort
                identity["error"] = f"kon geen SP-token minten: {exc}"
        else:  # msal_bundle
            try:
                prof = json.loads(payload.get("azureProfile.json") or "{}")
                subs = prof.get("subscriptions") or []
                active = next((s for s in subs if s.get("isDefault")), (subs[0] if subs else {}))
                user = (active.get("user") or {})
                identity.update({
                    "account": user.get("name"), "tenant_id": active.get("tenantId"),
                    "subscription": active.get("name"), "subscription_id": active.get("id"),
                })
            except Exception as exc:  # noqa: BLE001
                identity["error"] = f"kon azureProfile.json niet parsen: {exc}"
        row.identity_json = json.dumps(identity)
        self.db.commit()
        return identity

    async def sync(self, profile_id: int, *, target: str, lab_id: Optional[str] = None,
                   az_dir: str = "/root/.azure") -> Dict[str, Any]:
        row = self.get_or_404(profile_id)
        payload = self._decrypt(row)
        if target == "host":
            return await self._sync_to_host(row, payload)
        if target == "lab":
            if not lab_id:
                raise HTTPException(status_code=400, detail="lab_id is verplicht voor een lab-sync.")
            return await self._sync_to_lab(row, payload, lab_id, az_dir)
        raise HTTPException(status_code=400, detail="target moet 'host' of 'lab' zijn.")

    async def _sync_to_host(self, row: AzureProfile, payload: Dict[str, Any]) -> Dict[str, Any]:
        from services.azure.az_login_service import import_token_files, start_service_principal
        if row.kind == "msal_bundle":
            res = import_token_files(
                msal_token_cache=payload["msal_token_cache.json"],
                azure_profile=payload["azureProfile.json"],
                service_principal_entries=payload.get("service_principal_entries.json"))
            return {"ok": True, "target": "host", "detail": res}
        if row.kind == "service_principal":
            res = await start_service_principal(
                tenant_id=payload["tenant_id"], client_id=payload["client_id"],
                client_secret=payload["client_secret"])
            return {"ok": bool(res.get("ok")), "target": "host", "detail": res}
        raise HTTPException(status_code=400, detail=(
            "Een 'bearer'-profiel kan niet naar de host gesynct worden (geen login). "
            "Gebruik het als opgeslagen credential."))

    async def _sync_to_lab(self, row: AzureProfile, payload: Dict[str, Any],
                           lab_id: str, az_dir: str) -> Dict[str, Any]:
        if row.kind != "msal_bundle":
            raise HTTPException(status_code=400, detail=(
                "Alleen een 'msal_bundle'-profiel kan naar een lab gesynct worden. "
                "Voor een service principal: draai 'az login --service-principal' in het lab."))
        from services.lab.lab_service import LabService
        files = {k: v for k, v in payload.items() if k in AZ_BUNDLE_FILES}
        res = await LabService(self.db).az_login(lab_id, az_dir=az_dir, files=files)
        return {"ok": bool(res.get("ok")), "target": "lab", "detail": res}
