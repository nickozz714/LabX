"""utils/crypto.py — Fernet helpers for secrets-at-rest (Azure profiles).

The key comes from LABX_FERNET_KEY (a urlsafe-base64 32-byte key, e.g. output
of `Fernet.generate_key()`). Never logged, never returned by any API.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet

from config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = (settings.FERNET_KEY or "").strip()
    if not key:
        raise RuntimeError(
            "LABX_FERNET_KEY is niet gezet — genereer er een met "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
