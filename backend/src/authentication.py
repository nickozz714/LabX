"""authentication.py — single-admin JWT auth. LabX has exactly one account
(the server operator); this is deliberately not a user/org system like ND3X.
"""
from __future__ import annotations

import time

import jwt
from fastapi import Header, HTTPException, WebSocket

from config import settings

_ALG = "HS256"

# ── Password hashing (stdlib pbkdf2, no extra deps) ─────────────────────────
_PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    import hashlib
    import os as _os
    salt = _os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    import hashlib
    import hmac as _hmac
    try:
        scheme, iters, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iters))
        return _hmac.compare_digest(digest.hex(), hash_hex)
    except Exception:  # noqa: BLE001 — malformed hash = no match
        return False


def _secret() -> str:
    s = (settings.JWT_SECRET or "").strip()
    if not s:
        raise RuntimeError("LABX_JWT_SECRET is niet gezet")
    return s


def create_access_token(username: str) -> str:
    now = int(time.time())
    payload = {"sub": username, "iat": now, "exp": now + settings.JWT_TTL_MINUTES * 60}
    return jwt.encode(payload, _secret(), algorithm=_ALG)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALG])
    except jwt.PyJWTError as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"Ongeldig token: {exc}") from exc


def require_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Ontbrekende Authorization-header")
    token = authorization.split(" ", 1)[1].strip()
    return decode_access_token(token)


async def require_user_ws(websocket: WebSocket, token: str) -> dict:
    """WebSockets can't set an Authorization header from the browser, so the
    terminal endpoint validates a `?token=` query param instead (same decoder)."""
    try:
        return decode_access_token(token)
    except HTTPException:
        await websocket.close(code=4401)
        raise
