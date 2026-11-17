"""Argon2id password hashing tests."""

from __future__ import annotations

from app.auth.passwords import hash_password, verify_password


def test_argon2id_round_trip() -> None:
    hashed = hash_password("rahasia-yang-kuat-123")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "rahasia-yang-kuat-123") is True
    assert verify_password(hashed, "salah") is False
