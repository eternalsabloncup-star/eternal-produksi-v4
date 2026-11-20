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

    return {
        "status": "ok",
        "app_env": settings.app_env,
        "mongodb": mongo_ok,
        # Read-only feature flags untuk verifikasi aktivasi ops (bukan toggle).
        "features": {
            "loading_sj_task_enabled": bool(settings.loading_sj_task_enabled),
        },
    }
