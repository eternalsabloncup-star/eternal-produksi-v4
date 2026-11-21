"""AEAD recovery untuk design approval raw token (Owner option B).

- AES-256-GCM via ``cryptography``.
- Associated data mengikat accurate_po_id + design_version.
- Key dari env (bukan source). Fail-closed bila key/ciphertext tidak valid.
- Jangan log plaintext token atau key.
"""

from __future__ import annotations

import base64
import os
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD_PREFIX: Final[str] = "eternal-v4-design-approval-link-v1"
NONCE_LEN: Final[int] = 12
KEY_LEN: Final[int] = 32


class DesignLinkCryptoError(Exception):
    """Recovery gagal — treat as unavailable (fail-closed)."""


def parse_recovery_key(raw: str | None) -> bytes | None:
    """Parse urlsafe/std base64 32-byte key. Empty → None (feature off)."""
    text = (raw or "").strip()
    if not text:
        return None
    pad = "=" * (-len(text) % 4)
    try:
        key = base64.urlsafe_b64decode(text + pad)
    except Exception as exc:  # noqa: BLE001
        raise DesignLinkCryptoError("recovery key tidak valid") from exc
    if len(key) != KEY_LEN:
        raise DesignLinkCryptoError("recovery key harus 32 bytes")
    return key


def _aad(*, accurate_po_id: str, design_version: int) -> bytes:
    return f"{AAD_PREFIX}|{accurate_po_id}|{int(design_version)}".encode("utf-8")


def encrypt_approval_token(
    raw_token: str,
    *,
    accurate_po_id: str,
    design_version: int,
    key: bytes,
) -> str:
    if not raw_token or not isinstance(raw_token, str):
        raise DesignLinkCryptoError("token kosong")
    if len(key) != KEY_LEN:
        raise DesignLinkCryptoError("recovery key harus 32 bytes")
    aes = AESGCM(key)
    nonce = os.urandom(NONCE_LEN)
    ct = aes.encrypt(
        nonce,
        raw_token.encode("utf-8"),
        _aad(accurate_po_id=accurate_po_id, design_version=design_version),
    )
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_approval_token(
    ciphertext_b64: str,
    *,
    accurate_po_id: str,
    design_version: int,
    key: bytes,
) -> str:
    if not ciphertext_b64 or not isinstance(ciphertext_b64, str):
        raise DesignLinkCryptoError("ciphertext kosong")
    if len(key) != KEY_LEN:
        raise DesignLinkCryptoError("recovery key harus 32 bytes")
    pad = "=" * (-len(ciphertext_b64) % 4)
    try:
        blob = base64.urlsafe_b64decode(ciphertext_b64 + pad)
    except Exception as exc:  # noqa: BLE001
        raise DesignLinkCryptoError("ciphertext rusak") from exc
    if len(blob) <= NONCE_LEN:
        raise DesignLinkCryptoError("ciphertext rusak")
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    try:
        plain = AESGCM(key).decrypt(
            nonce,
            ct,
            _aad(accurate_po_id=accurate_po_id, design_version=design_version),
        )
    except Exception as exc:  # noqa: BLE001
        raise DesignLinkCryptoError("decrypt gagal") from exc
    token = plain.decode("utf-8")
    if not token:
        raise DesignLinkCryptoError("token kosong")
    return token
