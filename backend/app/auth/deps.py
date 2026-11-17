"""Auth dependencies for FastAPI routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status

from app.auth.session import find_valid_session, load_active_user, verify_csrf
from app.config import Settings, get_settings
from app.db import get_db


@dataclass
class AuthContext:
    user: dict[str, Any]
    session: dict[str, Any]
    raw_token: str


def get_settings_dep() -> Settings:
    return get_settings()


def require_session(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> AuthContext:
    raw_token = request.cookies.get(settings.cookie_name)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tidak terautentikasi",
        )

    db = get_db(settings)
    session = find_valid_session(db, raw_token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tidak terautentikasi",
        )

    user = load_active_user(db, session["user_id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tidak terautentikasi",
        )

    return AuthContext(user=user, session=session, raw_token=raw_token)


def require_csrf(
    auth: AuthContext = Depends(require_session),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> AuthContext:
    if not verify_csrf(auth.session, x_csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token CSRF tidak valid",
        )
    return auth
