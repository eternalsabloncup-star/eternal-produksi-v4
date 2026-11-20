"""Authenticated encryption for tracking-link raw tokens (server-side only).

Fernet (AES-128-CBC + HMAC-SHA256). Raw token never stored plaintext in Mongo.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

# Material lokal test/dev saja — production wajib V4_TRACKING_LINK_SEAL_KEY.
_DEV_SEAL_MATERIAL = b"eternal-v4-dev-tracking-seal-v1"


class TrackingSealKeyMissing(RuntimeError):
    code = "TRACKING_SEAL_KEY_MISSING"

    def __init__(self) -> None:
        super().__init__(
            "V4_TRACKING_LINK_SEAL_KEY wajib di production untuk menyimpan/mengambil "
            "URL Live Tracking terenkripsi."
        )


class TrackingSealError(RuntimeError):
    code = "TRACKING_SEAL_ERROR"

    def __init__(self, message: str = "Gagal membuka token Live Tracking terenkripsi.") -> None:
        super().__init__(message)


def _dev_fernet_key() -> bytes:
    digest = hashlib.sha256(_DEV_SEAL_MATERIAL).digest()
    return base64.urlsafe_b64encode(digest)


def resolve_fernet_key() -> bytes:
    settings = get_settings()
    raw = str(settings.tracking_link_seal_key or "").strip()
    if raw:
        return raw.encode("utf-8")
    if settings.app_env == "production":
        raise TrackingSealKeyMissing()
    return _dev_fernet_key()


@lru_cache(maxsize=4)
def _fernet_for_key(key_bytes: bytes) -> Fernet:
    return Fernet(key_bytes)


def clear_seal_cache() -> None:
    _fernet_for_key.cache_clear()


def seal_raw_token(raw_token: str) -> str:
    """Kembalikan ciphertext Fernet (str)."""
    token = str(raw_token or "").strip()
    if not token:
        raise TrackingSealError("raw token kosong")
    f = _fernet_for_key(resolve_fernet_key())
    return f.encrypt(token.encode("utf-8")).decode("ascii")


def unseal_raw_token(sealed: str) -> str:
    blob = str(sealed or "").strip()
    if not blob:
        raise TrackingSealError("ciphertext kosong")
    f = _fernet_for_key(resolve_fernet_key())
    try:
        return f.decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise TrackingSealError() from exc
