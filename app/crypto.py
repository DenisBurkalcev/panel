"""Symmetric encryption helpers for at-rest secrets (proxy URLs, golden_keys)."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CryptoError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    # Derive a stable 32-byte key from FPK_SECRET_KEY via SHA-256, then base64-url for Fernet.
    raw = settings.secret_key.encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_str(plaintext: str) -> str:
    """Encrypt a UTF-8 string. Returns a URL-safe base64 token (str)."""
    if plaintext is None:
        raise CryptoError("Cannot encrypt None")
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_str(token: str) -> str:
    """Decrypt a token previously produced by `encrypt_str`."""
    if not token:
        raise CryptoError("Empty token")
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover - defensive
        raise CryptoError("Failed to decrypt — wrong master key or corrupted ciphertext") from exc


def maybe_decrypt(token: str | None) -> str | None:
    if token is None:
        return None
    return decrypt_str(token)
