"""Jalur manual sync Accurate → Mongo operasional eternal_v4.

Terpisah dari sync test. Default dry-run (0 writes). Apply butuh flag + konfirmasi.
Tidak ada scheduler / bulk. Tidak auto-migration index.
"""

from __future__ import annotations

import os
import re
from typing import Any

from pymongo.database import Database

from app.config import Settings, get_settings
from app.db_guard import assert_safe_mongo_production_sync_target
from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.normalize import normalize_sales_order
from app.repositories.po_repository import count_by_accurate_id, get_by_accurate_id
from app.services.po_sync import diff_synced_lines, sync_sales_order_payload

_NUMERIC_PIN_RE = re.compile(r"^[0-9]+$")


class ProductionSyncError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def assert_production_accurate_pin(settings: Settings | None = None) -> str:
    """Wajib pin numerik V4_ACCURATE_DB_ID + require pin aktif di production."""
    cfg = settings or get_settings()
    pin = (cfg.accurate_db_id or "").strip()
    if not pin:
        raise ProductionSyncError(
            "V4_ACCURATE_DB_ID (pin numerik) wajib untuk production sync.",
            code="accurate_database_pin_required",
        )
    if not _NUMERIC_PIN_RE.fullmatch(pin):
        raise ProductionSyncError(
            "V4_ACCURATE_DB_ID harus numerik (d.database.id).",
            code="accurate_database_pin_invalid",
        )
    # Production path selalu mewajibkan pin — jangan andalkan default settings saja.
    if cfg.app_env == "production" and cfg.accurate_require_db_pin is False:
        raise ProductionSyncError(
            "Production sync wajib Accurate DB pin (jangan matikan REQUIRE_DB_PIN).",
            code="accurate_database_pin_required",
        )
    return pin


def assert_production_sync_preflight(
    *,
    settings: Settings | None = None,
    db_name: str | None = None,
) -> Settings:
    """Validasi env/target sebelum fetch Accurate atau tulis Mongo."""
    cfg = settings or get_settings()
    name = (db_name or cfg.mongodb_db or "").strip()
    expected_auth = (
        os.environ.get("V4_MONGO_AUTH_SOURCE")
        or os.environ.get("MONGO_AUTH_SOURCE")
        or "eternal_v4"
    ).strip()
    assert_safe_mongo_production_sync_target(
        uri=cfg.mongodb_uri,
        db_name=name,
        app_env=cfg.app_env,
        expected_auth_source=expected_auth,
    )
    assert_production_accurate_pin(cfg)
    if not (
        cfg.accurate_base_url
        and cfg.accurate_api_token
        and cfg.accurate_api_signature_secret
    ):
        raise ProductionSyncError(
            "Kredensial Accurate belum lengkap.",
            code="accurate_credentials_missing",
        )
    return cfg


def _build_reader(
    cfg: Settings,
    client: AccurateReadClient | None,
) -> AccurateReadClient:
    if client is not None:
        if not getattr(client, "require_db_pin", False):
            client.require_db_pin = True  # type: ignore[attr-defined]
        pin = str(getattr(client, "db_id", "") or "").strip()
        if not pin:
            raise ProductionSyncError(
                "V4_ACCURATE_DB_ID (pin numerik) wajib untuk production sync.",
                code="accurate_database_pin_required",
            )
        if not _NUMERIC_PIN_RE.fullmatch(pin):
            raise ProductionSyncError(
                "V4_ACCURATE_DB_ID harus numerik (d.database.id).",
                code="accurate_database_pin_invalid",
            )
        return client
    return AccurateReadClient(
        account_base_url=cfg.accurate_base_url,
        api_token=cfg.accurate_api_token,
        signature_secret=cfg.accurate_api_signature_secret,
        db_id=cfg.accurate_db_id,
        timeout_seconds=float(cfg.accurate_http_timeout_seconds),
        require_db_pin=True,
    )


def run_production_sync_one_po(
    db: Database,
    accurate_id: str | int,
    *,
    client: AccurateReadClient | None = None,
    dry_run: bool = True,
    confirm_po_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Sync satu PO ke eternal_v4.

    dry_run=True (default): validasi + fetch/normalize — 0 writes Mongo
    (tidak upsert, tidak ensure_indexes).
    dry_run=False: wajib confirm_po_id == accurate_id, lalu upsert idempotent.
    """
    cfg = assert_production_sync_preflight(settings=settings, db_name=db.name)
    aid = str(accurate_id).strip()
    if not aid:
        raise ProductionSyncError("accurate_id wajib.", code="po_id_required")

    reader = _build_reader(cfg, client)
    try:
        payload = reader.fetch_sales_order_detail(aid)
    except AccurateAuthError:
        raise

    po = normalize_sales_order(payload)
    resolved_id = str(po.accurate_id or aid)

    # Baca existing hanya untuk prediksi dry-run / diff — bukan write.
    before = get_by_accurate_id(db, resolved_id)
    line_diff = diff_synced_lines(before, po)
    base = {
        "ok": True,
        "mode": "production",
        "mongo_guard": "production_passed",
        "accurate_id": resolved_id,
        "po_no": po.po_no,
        "line_count": len(po.items),
        "process_kinds": [i.process_kind for i in po.items],
        "line_diff": line_diff,
    }

    if dry_run:
        return {
            **base,
            "dry_run": True,
            "executed": False,
            "writes": 0,
            "would_insert": before is None,
            "message": "Dry-run — tidak menulis Mongo / tidak membuat index.",
        }

    if confirm_po_id is None or str(confirm_po_id).strip() != aid:
        raise ProductionSyncError(
            "Apply ditolak: wajib --confirm-po-id yang sama dengan PO yang disinkronkan.",
            code="po_confirm_required",
        )

    # Guard ulang tepat sebelum write.
    expected_auth = (
        os.environ.get("V4_MONGO_AUTH_SOURCE")
        or os.environ.get("MONGO_AUTH_SOURCE")
        or "eternal_v4"
    ).strip()
    assert_safe_mongo_production_sync_target(
        uri=cfg.mongodb_uri,
        db_name=db.name,
        app_env=cfg.app_env,
        expected_auth_source=expected_auth,
    )

    result = sync_sales_order_payload(db, payload, mongo_guard="production")
    return {
        **base,
        "dry_run": False,
        "executed": True,
        "writes": 1,
        "po_count": result["count_after"],
        "inserted": result["was_insert"],
        "updated": not result["was_insert"],
        "idempotent": bool(
            result["duplicate_prevented"] and result["count_after"] == 1
        ),
        "duplicate_prevented": result["duplicate_prevented"],
        "operational_preserved": result["operational_preserved"],
        "count_after": count_by_accurate_id(db, resolved_id),
        "line_diff": result["line_diff"],
    }
