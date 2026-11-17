"""Opaque session tokens (CSPRNG) stored as hashes."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from pymongo.database import Database

from app.config import Settings
from app.db import sessions_collection, users_collection


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    db: Database,
    settings: Settings,
    user_id: ObjectId,
) -> tuple[str, str, datetime]:
    raw_token = generate_session_token()
    csrf_token = generate_csrf_token()
    expires_at = utcnow() + timedelta(seconds=settings.session_ttl_seconds)
    doc = {
        "user_id": user_id,
        "token_hash": hash_token(raw_token),
        "csrf_token_hash": hash_token(csrf_token),
        "created_at": utcnow(),
        "expires_at": expires_at,
        "revoked_at": None,
    }
    sessions_collection(db).insert_one(doc)
    return raw_token, csrf_token, expires_at


def find_valid_session(
    db: Database,
    raw_token: str,
) -> dict[str, Any] | None:
    token_hash = hash_token(raw_token)
    session = sessions_collection(db).find_one({"token_hash": token_hash})
    if not session:
        return None
    if session.get("revoked_at") is not None:
        return None
    expires_at = session.get("expires_at")
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utcnow():
        return None
    return session


def revoke_session(db: Database, session_id: ObjectId) -> None:
    sessions_collection(db).update_one(
        {"_id": session_id, "revoked_at": None},
        {"$set": {"revoked_at": utcnow()}},
    )


def revoke_session_by_token(db: Database, raw_token: str) -> bool:
    session = find_valid_session(db, raw_token)
    if not session:
        return False
    revoke_session(db, session["_id"])
    return True


def load_active_user(db: Database, user_id: ObjectId) -> dict[str, Any] | None:
    user = users_collection(db).find_one({"_id": user_id})
    if not user:
        return None
    if not user.get("is_active", False):
        return None
    return user


def verify_csrf(session: dict[str, Any], csrf_token: str | None) -> bool:
    if not csrf_token:
        return False
    expected = session.get("csrf_token_hash")
    if not expected:
        return False
    return secrets.compare_digest(expected, hash_token(csrf_token))


def public_user(user: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(user["_id"]),
        "email": user.get("email_normalized", ""),
        "display_name": user.get("display_name", ""),
        "role": user.get("role", "user"),
    }
