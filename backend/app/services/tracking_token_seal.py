"""Authenticated encryption for tracking-link raw tokens (server-side only).

Fernet (AES-128-CBC + HMAC-SHA256). Raw token never stored plaintext in Mongo.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Any

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


class TrackingSealKeyInvalid(RuntimeError):
    code = "TRACKING_SEAL_KEY_INVALID"

    def __init__(self, message: str = "V4_TRACKING_LINK_SEAL_KEY format tidak valid.") -> None:
        super().__init__(message)


class TrackingSealError(RuntimeError):
    code = "TRACKING_SEAL_ERROR"

    def __init__(self, message: str = "Gagal membuka token Live Tracking terenkripsi.") -> None:
        super().__init__(message)


def _dev_fernet_key() -> bytes:
    digest = hashlib.sha256(_DEV_SEAL_MATERIAL).digest()
    return base64.urlsafe_b64encode(digest)


def _validate_fernet_key_bytes(key_bytes: bytes) -> Fernet:
    try:
        return Fernet(key_bytes)
    except (ValueError, TypeError, Exception) as exc:
        raise TrackingSealKeyInvalid(
            "V4_TRACKING_LINK_SEAL_KEY harus Fernet key (url-safe base64 32-byte)."
        ) from exc


def resolve_fernet_key() -> bytes:
    settings = get_settings()
    raw = str(settings.tracking_link_seal_key or "").strip()
    if raw:
        key_bytes = raw.encode("utf-8")
        _validate_fernet_key_bytes(key_bytes)
        return key_bytes
    if settings.app_env == "production":
        raise TrackingSealKeyMissing()
    return _dev_fernet_key()


@lru_cache(maxsize=4)
def _fernet_for_key(key_bytes: bytes) -> Fernet:
    return _validate_fernet_key_bytes(key_bytes)


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
        raise TrackingSealError(
            "Ciphertext tidak dapat dibuka — kunci seal salah/hilang atau data rusak."
        ) from exc


def preflight_tracking_seal_key() -> dict[str, Any]:
    """Status kunci seal untuk deployment — tanpa mencetak secret.

    production_ready:
      - APP_ENV=production → key configured + format OK + roundtrip OK
      - non-production → derived/dev key OK (configured boleh false)
    """
    settings = get_settings()
    configured = bool(str(settings.tracking_link_seal_key or "").strip())
    out: dict[str, Any] = {
        "configured": configured,
        "key_format_ok": False,
        "roundtrip_ok": False,
        "production_ready": False,
        "app_env": settings.app_env,
        "status": "unknown",
    }
    try:
        key = resolve_fernet_key()
        f = _fernet_for_key(key)
        out["key_format_ok"] = True
        probe = f.encrypt(b"seal-preflight")
        assert f.decrypt(probe) == b"seal-preflight"
        out["roundtrip_ok"] = True
    except TrackingSealKeyMissing:
        out["status"] = "missing"
        return out
    except TrackingSealKeyInvalid:
        out["status"] = "invalid"
        return out
    except Exception:  # noqa: BLE001 — jangan bocorkan detail crypto
        out["status"] = "error"
        return out

    if settings.app_env == "production":
        ready = configured and out["key_format_ok"] and out["roundtrip_ok"]
        out["production_ready"] = ready
        out["status"] = "ok" if ready else "not_ready"
    else:
        out["production_ready"] = out["key_format_ok"] and out["roundtrip_ok"]
        out["status"] = "ok_non_production"
    return out
