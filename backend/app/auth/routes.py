"""Authentication HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.auth.deps import AuthContext, get_settings_dep, require_csrf, require_session
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.rate_limit import LoginRateLimiter
from app.auth.session import (
    create_session,
    generate_csrf_token,
    hash_token,
    public_user,
    revoke_session,
    verify_csrf,
)
from app.config import Settings
from app.db import get_db, sessions_collection, users_collection
from app.models.user import LoginRequest, MeResponse, MessageResponse, UserPublic, normalize_email

router = APIRouter(prefix="/api/auth", tags=["auth"])

_rate_limiter: LoginRateLimiter | None = None


def get_rate_limiter(settings: Settings) -> LoginRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = LoginRateLimiter(
            max_attempts=settings.login_rate_limit_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
        )
    return _rate_limiter


def reset_rate_limiter() -> None:
    global _rate_limiter
    if _rate_limiter is not None:
        _rate_limiter.clear_all()
    _rate_limiter = None


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _set_auth_cookies(
    response: Response,
    settings: Settings,
    session_token: str,
    csrf_token: str,
    max_age: int,
) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=session_token,
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite="lax",
        max_age=max_age,
        path="/",
    )
    # Readable by JS for X-CSRF-Token; not a bearer credential.
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        secure=bool(settings.cookie_secure),
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


@router.post("/login", response_model=MeResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
) -> MeResponse:
    limiter = get_rate_limiter(settings)
    key = _client_key(request)
    if limiter.is_blocked(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Terlalu banyak percobaan login. Coba lagi nanti.",
        )

    db = get_db(settings)
    email = normalize_email(body.email)
    user = users_collection(db).find_one({"email_normalized": email})

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Email atau kata sandi salah",
    )

    if not user or not user.get("is_active", False):
        limiter.register_failure(key)
        raise invalid

    if not verify_password(user["password_hash"], body.password):
        limiter.register_failure(key)
        raise invalid

    if needs_rehash(user["password_hash"]):
        users_collection(db).update_one(
            {"_id": user["_id"]},
            {"$set": {"password_hash": hash_password(body.password)}},
        )

    session_token, csrf_token, _expires = create_session(db, settings, user["_id"])
    limiter.reset(key)
    _set_auth_cookies(
        response,
        settings,
        session_token,
        csrf_token,
        max_age=settings.session_ttl_seconds,
    )
    from app.services.task_engine import can_act

    caps = {
        "payment_bypass": can_act(
            db, user, task_type="payment-bypass", action="approve"
        ),
    }
    return MeResponse(
        user=UserPublic(**public_user(user)),
        csrf_token=csrf_token,
        capabilities=caps,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    settings: Settings = Depends(get_settings_dep),
) -> MessageResponse:
    db = get_db(settings)
    revoke_session(db, auth.session["_id"])
    _clear_auth_cookies(response, settings)
    return MessageResponse(message="Berhasil keluar")


@router.get("/me", response_model=MeResponse)
def read_me(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings_dep),
) -> MeResponse:
    csrf_token = request.cookies.get(settings.csrf_cookie_name)
    if not csrf_token or not verify_csrf(auth.session, csrf_token):
        csrf_token = generate_csrf_token()
        sessions_collection(get_db(settings)).update_one(
            {"_id": auth.session["_id"]},
            {"$set": {"csrf_token_hash": hash_token(csrf_token)}},
        )
        response.set_cookie(
            key=settings.csrf_cookie_name,
            value=csrf_token,
            httponly=False,
            secure=bool(settings.cookie_secure),
            samesite="lax",
            max_age=settings.session_ttl_seconds,
            path="/",
        )

    from app.services.task_engine import can_act

    db = get_db(settings)
    caps = {
        "payment_bypass": can_act(
            db, auth.user, task_type="payment-bypass", action="approve"
        ),
    }
    return MeResponse(
        user=UserPublic(**public_user(auth.user)),
        csrf_token=csrf_token,
        capabilities=caps,
    )
