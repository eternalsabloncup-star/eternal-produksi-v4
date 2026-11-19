#!/usr/bin/env python3
"""Verifikasi isolasi target sinkronisasi SJ — READ/CHECK saja, tanpa sync.

Menolak klaim "aman" hanya dari hostname. Memeriksa:
- kill switch V4_SJ_SYNC_ENABLED
- nama DB eternal_v4_test_*
- bukan nama ops/legacy
- kontrak authSource (bila diset)
- host Accurate bukan placeholder *.test (jika dikonfigurasi)

Tidak mencetak token/signature/URI lengkap.
Exit 0 = siap untuk sync non-prod (setelah Owner nyalakan kill switch + live Accurate).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _host_of(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").lower()


def main() -> int:
    from app.config import clear_settings_cache, get_settings
    from app.db_guard import UnsafeMongoTargetError, assert_safe_mongo_sync_target

    clear_settings_cache()
    settings = get_settings()
    host = _host_of(settings.accurate_base_url)
    report: dict = {
        "ok": False,
        "sj_sync_enabled": bool(settings.sj_sync_enabled),
        "app_env": settings.app_env,
        "mongodb_db": settings.mongodb_db,
        "mongodb_db_is_test_prefix": settings.mongodb_db.startswith("eternal_v4_test_"),
        "accurate_host": host or None,
        "accurate_host_is_placeholder_test": host.endswith(".test") if host else None,
        "accurate_token_configured": bool(settings.accurate_api_token.strip()),
        "accurate_signature_configured": bool(
            settings.accurate_api_signature_secret.strip()
        ),
        "accurate_db_id_configured": bool(settings.accurate_db_id.strip()),
        "accurate_db_id_kind": (
            "numeric"
            if settings.accurate_db_id.strip().isdigit()
            else (
                "uuid"
                if len(settings.accurate_db_id.strip()) == 36
                else ("empty" if not settings.accurate_db_id.strip() else "other")
            )
        ),
        "checks": [],
        "blocker_codes": [],
    }

    def fail(code: str, message: str) -> None:
        report["checks"].append({"code": code, "ok": False, "message": message})
        report["blocker_codes"].append(code)

    def ok(code: str, message: str) -> None:
        report["checks"].append({"code": code, "ok": True, "message": message})

    # 1) Kill switch — info saja (boleh OFF saat audit; sync tetap butuh ON).
    if settings.sj_sync_enabled:
        ok("kill_switch", "V4_SJ_SYNC_ENABLED aktif")
    else:
        fail(
            "kill_switch_off",
            "Kill switch OFF — sync API/script akan ditolak sampai dinyalakan eksplisit.",
        )

    # 2) Mongo guard (nama + port + env) — bukti isolasi, bukan tebakan host.
    expected_auth = (
        os.environ.get("V4_MONGO_AUTH_SOURCE")
        or os.environ.get("MONGO_AUTH_SOURCE")
        or ""
    ).strip() or None
    try:
        assert_safe_mongo_sync_target(
            uri=settings.mongodb_uri,
            db_name=settings.mongodb_db,
            app_env=settings.app_env,
            expected_auth_source=expected_auth,
        )
        ok("mongo_guard", "assert_safe_mongo_sync_target PASS")
    except UnsafeMongoTargetError as exc:
        fail(exc.code, "Target Mongo ditolak guard sync")

    # 3) Accurate live host (bukan stub Cloud Agent).
    if not host:
        fail("accurate_host_missing", "Account base Accurate belum dikonfigurasi")
    elif host.endswith(".test"):
        fail(
            "accurate_host_placeholder",
            "Host Accurate masih placeholder *.test — bukan live",
        )
    else:
        ok("accurate_host", "Host Accurate bukan placeholder *.test")

    if not settings.accurate_api_token.strip() or not settings.accurate_api_signature_secret.strip():
        fail("accurate_credentials_missing", "Token/signature Accurate belum dikonfigurasi")
    else:
        ok("accurate_credentials", "Token dan signature terkonfigurasi (nilai tidak ditampilkan)")

    if settings.accurate_db_pin_required and not settings.accurate_db_id.strip().isdigit():
        fail(
            "accurate_db_pin_invalid",
            "Pin database wajib numerik saat require_db_pin aktif",
        )
    elif settings.accurate_db_id.strip():
        ok("accurate_db_pin", f"DB pin kind={report['accurate_db_id_kind']}")

    report["ok"] = len(report["blocker_codes"]) == 0
    report["live_sync_ready"] = report["ok"]
    report["writes_accurate"] = False
    report["mutates_production_mongo"] = False
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
