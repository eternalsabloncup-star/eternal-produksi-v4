"""API Live Tracking — publik bertoken + manajemen internal berizin."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.auth.permissions import PERM_OPERATIONS_FULL, has_permission
from app.auth.rate_limit import LoginRateLimiter
from app.config import get_settings
from app.db import get_db
from app.repositories.tracking_link_repository import MissingRequiredTrackingIndexError
from app.services import tracking_service as tracking
from app.services.tracking_service import (
    TrackingConflict,
    TrackingError,
    TrackingForbidden,
    TrackingLegacyUnrecoverable,
    TrackingNotFound,
)
from app.services.tracking_token_seal import (
    TrackingSealKeyInvalid,
    TrackingSealKeyMissing,
)

router = APIRouter(tags=["tracking"])


def _http_from_index_error(exc: MissingRequiredTrackingIndexError) -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": exc.code,
            "message": str(exc),
            "missing": exc.missing,
            "conflict": exc.conflict,
        },
    )

_tracking_limiter: LoginRateLimiter | None = None


def _limiter() -> LoginRateLimiter:
    global _tracking_limiter
    if _tracking_limiter is None:
        settings = get_settings()
        _tracking_limiter = LoginRateLimiter(
            max_attempts=settings.login_rate_limit_attempts,
            window_seconds=settings.login_rate_limit_window_seconds,
        )
    return _tracking_limiter


def reset_tracking_rate_limiter() -> None:
    global _tracking_limiter
    if _tracking_limiter is not None:
        _tracking_limiter.clear_all()
    _tracking_limiter = None


def _client_key(request: Request, raw_token: str) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = (forwarded.split(",")[0].strip() if forwarded else "") or (
        request.client.host if request.client else "unknown"
    )
    # Jangan log token; gunakan prefix pendek untuk bucket brute-force.
    prefix = (raw_token or "")[:4]
    return f"track:{ip}:{prefix}"


def _require_ops(auth: AuthContext) -> None:
    if not has_permission(auth.user, PERM_OPERATIONS_FULL):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses ditolak")


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"


class GenerateLinkRequest(BaseModel):
    ttl_seconds: int | None = Field(default=None, ge=60, le=60 * 60 * 24 * 365)
    # Pemulihan legacy: wajib sama dengan link aktif yang dikonfirmasi operator.
    expected_active_link_id: str | None = Field(default=None, max_length=64)


class RevokeLinkRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


# —— Public (token) ——


@router.get("/api/public/tracking/{customer_slug}/{po_slug}/{token}")
def get_public_tracking(
    customer_slug: str,
    po_slug: str,
    token: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _no_store(response)
    limiter = _limiter()
    key = _client_key(request, token)
    if limiter.is_blocked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Terlalu banyak percobaan. Coba lagi nanti.",
        )
    try:
        payload = tracking.public_tracking_view(
            get_db(),
            customer_slug=customer_slug,
            po_slug=po_slug,
            raw_token=token,
        )
    except MissingRequiredTrackingIndexError as exc:
        raise _http_from_index_error(exc) from exc
    except TrackingNotFound:
        limiter.register_failure(key)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link pelacakan tidak valid.")
    except TrackingForbidden:
        limiter.register_failure(key)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses ditolak")
    limiter.reset(key)
    return payload


# —— Internal (session + ops) ——


@router.get("/api/tracking/{accurate_po_id}")
def get_internal_tracking(
    accurate_po_id: str,
    response: Response,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    _require_ops(auth)
    _no_store(response)
    try:
        return tracking.internal_tracking_view(get_db(), accurate_po_id=accurate_po_id)
    except MissingRequiredTrackingIndexError as exc:
        raise _http_from_index_error(exc) from exc
    except TrackingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc


@router.get("/api/tracking/{accurate_po_id}/links")
def list_links(
    accurate_po_id: str,
    response: Response,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    _require_ops(auth)
    _no_store(response)
    try:
        rows = tracking.list_tracking_links(get_db(), accurate_po_id)
    except MissingRequiredTrackingIndexError as exc:
        raise _http_from_index_error(exc) from exc
    except TrackingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    return {"items": rows, "accurate_po_id": accurate_po_id}


@router.get("/api/tracking/{accurate_po_id}/links/active-url")
def get_active_tracking_url(
    accurate_po_id: str,
    response: Response,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Salin ulang path link aktif — authenticated, scoped PO, no-store.

    Tidak regenerate. Tidak mengembalikan URL untuk revoked/expired.
    Legacy hash-only → 409 legacy_unrecoverable (butuh persetujuan regenerate).
    """
    _require_ops(auth)
    _no_store(response)
    try:
        return tracking.reveal_active_tracking_url(
            get_db(),
            accurate_po_id=accurate_po_id,
            actor_email=auth.user.get("email_normalized"),
        )
    except MissingRequiredTrackingIndexError as exc:
        raise _http_from_index_error(exc) from exc
    except TrackingLegacyUnrecoverable as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": exc.message,
                "link_id": exc.link_id,
                "accurate_po_id": exc.accurate_po_id,
                "requires_explicit_recovery": True,
            },
        ) from exc
    except (TrackingSealKeyMissing, TrackingSealKeyInvalid) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except TrackingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except TrackingError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.post("/api/tracking/{accurate_po_id}/links", status_code=status.HTTP_201_CREATED)
def post_generate_link(
    accurate_po_id: str,
    response: Response,
    body: GenerateLinkRequest | None = None,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_ops(auth)
    _no_store(response)
    ttl = body.ttl_seconds if body else None
    try:
        result = tracking.generate_tracking_link(
            get_db(),
            accurate_po_id=accurate_po_id,
            actor_email=auth.user.get("email_normalized"),
            ttl_seconds=ttl,
        )
    except MissingRequiredTrackingIndexError as exc:
        raise _http_from_index_error(exc) from exc
    except (TrackingSealKeyMissing, TrackingSealKeyInvalid) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except TrackingConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.message) from exc
    except TrackingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    return result


@router.post("/api/tracking/{accurate_po_id}/links/regenerate")
def post_regenerate_link(
    accurate_po_id: str,
    response: Response,
    body: GenerateLinkRequest | None = None,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_ops(auth)
    _no_store(response)
    ttl = body.ttl_seconds if body else None
    expected = body.expected_active_link_id if body else None
    try:
        return tracking.regenerate_tracking_link(
            get_db(),
            accurate_po_id=accurate_po_id,
            actor_email=auth.user.get("email_normalized"),
            ttl_seconds=ttl,
            expected_active_link_id=expected,
        )
    except MissingRequiredTrackingIndexError as exc:
        raise _http_from_index_error(exc) from exc
    except TrackingConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except TrackingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc


@router.post("/api/tracking/links/{link_id}/revoke")
def post_revoke_link(
    link_id: str,
    response: Response,
    body: RevokeLinkRequest | None = None,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_ops(auth)
    _no_store(response)
    try:
        doc = tracking.revoke_tracking_link(
            get_db(),
            link_id=link_id,
            actor_email=auth.user.get("email_normalized"),
            reason=body.reason if body else None,
        )
    except MissingRequiredTrackingIndexError as exc:
        raise _http_from_index_error(exc) from exc
    except TrackingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    return {"link": doc}
