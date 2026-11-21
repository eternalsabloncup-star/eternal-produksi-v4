"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import get_settings_dep
from app.config import Settings
from app.db import ping

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health(settings: Settings = Depends(get_settings_dep)) -> dict[str, object]:
    try:
        ping()
        mongo_ok = True
    except Exception:
        mongo_ok = False
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database tidak tersedia",
        ) from None

    from app.services.tracking_token_seal import preflight_tracking_seal_key

    seal = preflight_tracking_seal_key()
    # Fail-closed di production jika seal key belum siap (tanpa mencetak secret).
    if settings.app_env == "production" and not seal.get("production_ready"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "TRACKING_SEAL_KEY_NOT_READY",
                "message": (
                    "V4_TRACKING_LINK_SEAL_KEY belum siap di production. "
                    "Set Fernet key persisten sebelum cutover Live Tracking copy."
                ),
                "tracking_seal": {
                    "configured": seal.get("configured"),
                    "key_format_ok": seal.get("key_format_ok"),
                    "roundtrip_ok": seal.get("roundtrip_ok"),
                    "status": seal.get("status"),
                },
            },
        )

    return {
        "status": "ok",
        "app_env": settings.app_env,
        "mongodb": mongo_ok,
        # Read-only feature flags untuk verifikasi aktivasi ops (bukan toggle).
        "features": {
            "loading_sj_task_enabled": bool(settings.loading_sj_task_enabled),
        },
        "tracking_seal": {
            "configured": seal.get("configured"),
            "key_format_ok": seal.get("key_format_ok"),
            "roundtrip_ok": seal.get("roundtrip_ok"),
            "production_ready": seal.get("production_ready"),
            "status": seal.get("status"),
        },
    }
