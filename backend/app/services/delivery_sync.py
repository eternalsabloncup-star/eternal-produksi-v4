"""Sinkronisasi manual Surat Jalan Accurate → Mongo V4 (read-only Accurate).

Alur: DO list (paginated) → DO detail → normalize → validasi PO V4 → ingest.
Tidak menulis Accurate. Tidak menghapus SJ V4 yang absen dari listing.
Tidak menyamakan Qty SJ dengan Qty Loading. Tidak ada scheduler/auto-sync.

Mutasi Mongo default lewat ``assert_safe_mongo_sync_target`` (DB test
``eternal_v4_test_*``). Jalur production eksplisit memakai
``assert_safe_mongo_production_sync_target`` + flag
``V4_SJ_SYNC_PRODUCTION_ENABLED`` (default OFF; butuh CEO).
Endpoint API manual tetap ``mongo_guard=test``.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError, ServerSelectionTimeoutError

from app.config import get_settings
from app.db_guard import (
    UnsafeMongoTargetError,
    assert_safe_mongo_production_sync_target,
    assert_safe_mongo_sync_target,
)
from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.fields import as_text
from app.repositories import delivery_repository as deliveries
from app.repositories.po_repository import get_by_accurate_id
from app.services import delivery_service

MongoGuardMode = Literal["test", "production"]

SYNC_LOCK_COLLECTION = "sync_locks"
SYNC_LOCK_ID = "delivery_order_sync"
DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_ROWS = 500
DEFAULT_TIMEOUT_SECONDS = 120.0
LOCK_TTL_SECONDS = 600


class DeliverySyncInProgressError(RuntimeError):
    """Sinkronisasi SJ dengan cakupan yang sama sedang berjalan."""

    code = "SYNC_IN_PROGRESS"


class DeliverySyncTimeoutError(TimeoutError):
    """Batas waktu sinkronisasi SJ tercapai."""

    code = "SYNC_TIMEOUT"


class DeliverySyncDisabledError(PermissionError):
    """Kill switch V4_SJ_SYNC_ENABLED=0 — sync ditolak."""

    code = "SJ_SYNC_DISABLED"


def is_sj_sync_enabled() -> bool:
    """Kill switch eksplisit; default OFF di Settings."""
    return bool(get_settings().sj_sync_enabled)


def require_sj_sync_enabled() -> None:
    if not is_sj_sync_enabled():
        raise DeliverySyncDisabledError(
            "Sinkronisasi Surat Jalan nonaktif (V4_SJ_SYNC_ENABLED). "
            "Aktifkan hanya setelah verifikasi non-prod dan persetujuan Owner/CEO.",
        )


def is_sj_sync_production_enabled() -> bool:
    """Flag jalur production SJ; default OFF dan independen dari kill switch test."""
    return bool(get_settings().sj_sync_production_enabled)


def require_sj_sync_production_enabled() -> None:
    if not is_sj_sync_production_enabled():
        raise UnsafeMongoTargetError(
            "Jalur production SJ nonaktif (V4_SJ_SYNC_PRODUCTION_ENABLED). "
            "Nyalakan hanya setelah CEO + backup + non-prod PASS.",
            code="mongo_sj_production_flag_off",
        )


def resolve_sj_mongo_guard() -> MongoGuardMode:
    """Pilih guard: production hanya jika kedua flag ON; selain itu test."""
    if is_sj_sync_enabled() and is_sj_sync_production_enabled():
        return "production"
    return "test"


def _guard_db(db: Database, *, mongo_guard: MongoGuardMode = "test") -> None:
    import os

    require_sj_sync_enabled()
    settings = get_settings()
    expected_auth = (
        os.environ.get("V4_MONGO_AUTH_SOURCE")
        or os.environ.get("MONGO_AUTH_SOURCE")
        or ""
    ).strip() or None
    if mongo_guard == "production":
        require_sj_sync_production_enabled()
        assert_safe_mongo_production_sync_target(
            uri=settings.mongodb_uri,
            db_name=db.name,
            app_env=settings.app_env,
            expected_auth_source=expected_auth or "eternal_v4",
        )
        return
    if mongo_guard != "test":
        raise UnsafeMongoTargetError(
            "mongo_guard SJ tidak dikenal — gunakan test atau production.",
            code="mongo_sj_guard_invalid",
        )
    assert_safe_mongo_sync_target(
        uri=settings.mongodb_uri,
        db_name=db.name,
        app_env=settings.app_env,
        expected_auth_source=expected_auth,
    )


def _locks(db: Database):
    return db[SYNC_LOCK_COLLECTION]


def acquire_delivery_sync_lock(
    db: Database, *, scope: str = SYNC_LOCK_ID, ttl_seconds: int = LOCK_TTL_SECONDS
) -> None:
    """Kunci Mongo sederhana — cegah dua sync SJ bersamaan untuk cakupan sama."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    lock_id = f"{SYNC_LOCK_ID}:{scope}"
    try:
        _locks(db).insert_one(
            {
                "_id": lock_id,
                "scope": scope,
                "started_at": now,
                "expires_at": expires,
            }
        )
        return
    except DuplicateKeyError:
        pass

    stolen = _locks(db).find_one_and_update(
        {"_id": lock_id, "expires_at": {"$lte": now}},
        {"$set": {"started_at": now, "expires_at": expires, "scope": scope}},
    )
    if stolen is None:
        raise DeliverySyncInProgressError(
            "Sinkronisasi Surat Jalan sudah berjalan untuk cakupan yang sama."
        )


def release_delivery_sync_lock(db: Database, *, scope: str = SYNC_LOCK_ID) -> None:
    _locks(db).delete_one({"_id": f"{SYNC_LOCK_ID}:{scope}"})


def _list_page_params(page: int, page_size: int) -> dict[str, Any]:
    # Kontrak Accurate list: sp.page (1-based) + sp.pageSize.
    return {"sp.page": page, "sp.pageSize": page_size}


def _safe_error(exc: BaseException) -> dict[str, str]:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return {"code": code, "error": type(exc).__name__}
    if isinstance(exc, deliveries.DeliveryNoteIdentityError):
        return {"code": exc.code, "error": "identity"}
    if isinstance(exc, UnsafeMongoTargetError):
        return {"code": exc.code, "error": "unsafe_mongo_target"}
    if isinstance(exc, AccurateAuthError):
        return {"code": exc.code, "error": "accurate_auth"}
    if isinstance(exc, TimeoutError):
        return {"code": "timeout", "error": "timeout"}
    if isinstance(exc, LookupError):
        return {"code": "not_found", "error": "lookup"}
    return {"code": "sync_failed", "error": type(exc).__name__}


def sync_delivery_order_payload(
    db: Database,
    user: dict[str, Any],
    payload: dict[str, Any],
    *,
    mongo_guard: MongoGuardMode = "test",
    require_registered_po: bool = True,
) -> dict[str, Any]:
    """Normalisasi + ingest satu SJ. Skip bila PO belum terdaftar di V4."""
    from app.services.po_repair_maintenance import WriterFence, po_repair_writer_section

    with po_repair_writer_section(db) as fence:
        return _sync_delivery_order_payload_under_writer_lease(
            db,
            user,
            payload,
            mongo_guard=mongo_guard,
            require_registered_po=require_registered_po,
            writer_fence=fence,
        )


def _sync_delivery_order_payload_under_writer_lease(
    db: Database,
    user: dict[str, Any],
    payload: dict[str, Any],
    *,
    mongo_guard: MongoGuardMode = "test",
    require_registered_po: bool = True,
    writer_fence: WriterFence,
) -> dict[str, Any]:
    writer_fence.assert_valid(db)
    _guard_db(db, mongo_guard=mongo_guard)
    from app.services.accurate_auto_sync.sj_sync_review import (
        REASON_DELIVERY_NOTE_IDENTITY,
        REASON_MULTI_SO,
        REASON_PO_NOT_REGISTERED,
        STATUS_OPEN,
        collect_line_sales_order_ids,
        get_sj_sync_review,
        mark_sj_sync_review_resolved,
        upsert_sj_sync_review,
    )

    try:
        note = deliveries.normalize_delivery_note(payload)
    except deliveries.DeliveryNoteIdentityError as exc:
        # Identitas data (MULTI_SO / line-SO hilang / konflik) → quarantine durable.
        # Tanpa SJ id tidak bisa dikarantina — fatal ke pemanggil.
        # Kegagalan simpan review → re-raise agar discovery page_failed (jangan majukan
        # checkpoint tanpa catatan gagal).
        sj_id = as_text(payload.get("id") or payload.get("accurate_sj_id"))
        if not sj_id:
            raise
        exc_code = as_text(getattr(exc, "code", None)) or REASON_DELIVERY_NOTE_IDENTITY
        if exc_code == REASON_MULTI_SO:
            reason = REASON_MULTI_SO
        else:
            # SO_ID_CONFLICT / SO_NUMBER_CONFLICT / DELIVERY_NOTE_IDENTITY → umbrella.
            reason = REASON_DELIVERY_NOTE_IDENTITY
        so_ids = collect_line_sales_order_ids(payload)
        # Detail aman: pesan normalizer sudah tanpa PII pelanggan.
        safe_detail = as_text(str(exc)) or None
        # Kegagalan durable write (DB/lock) → exception ke discovery = page_failed.
        # Jangan majukan checkpoint tanpa catatan gagal.
        upsert_sj_sync_review(
            db,
            accurate_sj_id=sj_id,
            reason=reason,
            source_so_ids=so_ids,
            sj_no=as_text(payload.get("number") or payload.get("sj_no")) or None,
            # Retryable: siklus berikutnya boleh re-fetch; ingest hanya jika
            # sumber sudah valid. Jangan map PO / line palsu.
            retryable=True,
            last_error_code=exc_code,
            last_error_detail=safe_detail,
        )
        persisted = get_sj_sync_review(db, sj_id)
        if (
            persisted is None
            or persisted.get("status") != STATUS_OPEN
            or as_text(persisted.get("reason")) != reason
        ):
            raise RuntimeError(
                "SJ identity quarantine tidak durable — review tidak tersimpan."
            )
        return {
            "outcome": "review_required",
            "reason": reason,
            "accurate_sj_id": sj_id,
            "source_so_ids": so_ids,
            "writes": 0,
            "quarantined": True,
            "last_error_code": exc_code,
        }

    po_id = str(note["accurate_po_id"])
    if require_registered_po and get_by_accurate_id(db, po_id) is None:
        upsert_sj_sync_review(
            db,
            accurate_sj_id=str(note["accurate_sj_id"]),
            reason=REASON_PO_NOT_REGISTERED,
            source_so_ids=[po_id],
            accurate_po_id=po_id,
            sj_no=as_text(note.get("sj_no")) or None,
            retryable=True,
            last_error_code=REASON_PO_NOT_REGISTERED,
        )
        # Tetap ``skipped`` agar kompatibel counter manual sync; discovery
        # mengenali keduanya. Deferred record menjamin replay setelah PO ada.
        return {
            "outcome": "skipped",
            "reason": REASON_PO_NOT_REGISTERED,
            "accurate_sj_id": note["accurate_sj_id"],
            "accurate_po_id": po_id,
            "deferred": True,
            "writes": 0,
        }

    before = deliveries.get_delivery_note(db, note["accurate_sj_id"])
    result = delivery_service.ingest_delivery_note(db, user, payload)
    if before is None:
        outcome = "created"
    elif result.get("source_changed"):
        outcome = "updated"
    else:
        outcome = "unchanged"
    mark_sj_sync_review_resolved(
        db,
        accurate_sj_id=str(note["accurate_sj_id"]),
        disposition=f"ingested_{outcome}",
    )
    return {
        "outcome": outcome,
        "accurate_sj_id": note["accurate_sj_id"],
        "accurate_po_id": po_id,
        "sj_version": result.get("sj_version"),
        "revision_status": result.get("revision_status"),
        "source_changed": bool(result.get("source_changed")),
        "was_insert": bool(result.get("was_insert")),
    }


def sync_delivery_orders_from_accurate(
    db: Database,
    user: dict[str, Any],
    *,
    client: AccurateReadClient | None = None,
    mongo_guard: MongoGuardMode = "test",
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    require_registered_po: bool = True,
    scope: str = "manual",
) -> dict[str, Any]:
    """Manual sync berpaginasi. Satu gagal tidak menghentikan SJ lain."""
    _guard_db(db, mongo_guard=mongo_guard)
    page_size = max(1, min(int(page_size), 100))
    max_pages = max(1, min(int(max_pages), 200))
    max_rows = max(1, min(int(max_rows), 2000))
    timeout_seconds = max(5.0, float(timeout_seconds))

    acquire_delivery_sync_lock(db, scope=scope)
    started = time.monotonic()
    reader = client or AccurateReadClient.from_settings()

    counters = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "review_required": 0,
        "failed": 0,
        "listed": 0,
        "pages": 0,
    }
    skip_reasons: dict[str, int] = {}
    failures: list[dict[str, Any]] = []
    stopped_reason: str | None = None

    try:
        for page in range(1, max_pages + 1):
            if time.monotonic() - started > timeout_seconds:
                stopped_reason = "timeout"
                break
            if counters["listed"] >= max_rows:
                stopped_reason = "max_rows"
                break

            try:
                rows = reader.fetch_delivery_order_list(
                    params=_list_page_params(page, page_size)
                )
            except (AccurateAuthError, TimeoutError, ConnectionError, RuntimeError) as exc:
                stopped_reason = "list_failed"
                failures.append({"stage": "list", "page": page, **_safe_error(exc)})
                counters["failed"] += 1
                break

            counters["pages"] += 1
            if not rows:
                break

            for row in rows:
                if time.monotonic() - started > timeout_seconds:
                    stopped_reason = "timeout"
                    break
                if counters["listed"] >= max_rows:
                    stopped_reason = "max_rows"
                    break

                counters["listed"] += 1
                sj_id = as_text(row.get("id"))
                if not sj_id:
                    counters["failed"] += 1
                    failures.append(
                        {
                            "stage": "list_row",
                            "code": "missing_sj_id",
                            "error": "identity",
                        }
                    )
                    continue

                try:
                    detail = reader.fetch_delivery_order_detail(sj_id)
                    outcome = sync_delivery_order_payload(
                        db,
                        user,
                        detail,
                        mongo_guard=mongo_guard,
                        require_registered_po=require_registered_po,
                    )
                    key = str(outcome.get("outcome") or "failed")
                    if key == "skipped":
                        counters["skipped"] += 1
                        reason = str(outcome.get("reason") or "unknown")
                        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    elif key == "review_required":
                        counters["review_required"] += 1
                    elif key in counters:
                        counters[key] += 1
                    else:
                        counters["failed"] += 1
                        failures.append(
                            {
                                "stage": "ingest",
                                "accurate_sj_id": sj_id,
                                "code": "unknown_outcome",
                                "error": key,
                            }
                        )
                except Exception as exc:  # noqa: BLE001 — partial failure OK
                    counters["failed"] += 1
                    failures.append(
                        {
                            "stage": "detail_or_ingest",
                            "accurate_sj_id": sj_id,
                            **_safe_error(exc),
                        }
                    )

            if stopped_reason:
                break
            if len(rows) < page_size:
                break
        else:
            stopped_reason = "max_pages"
    finally:
        release_delivery_sync_lock(db, scope=scope)

    return {
        "ok": stopped_reason not in {"list_failed"},
        "stopped_reason": stopped_reason,
        "page_size": page_size,
        "max_pages": max_pages,
        "max_rows": max_rows,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        **counters,
        "skip_reasons": skip_reasons,
        "failures": failures[:50],  # batasi diagnostik; tanpa payload pelanggan
        "auto_sync": False,
        "writes_accurate": False,
        "deletes_missing_sj": False,
    }


def sync_selected_delivery_orders_from_accurate(
    db: Database,
    user: dict[str, Any],
    sj_ids: list[str | int],
    *,
    client: AccurateReadClient | None = None,
    mongo_guard: MongoGuardMode = "test",
    require_registered_po: bool = True,
    scope: str = "manual-selected",
) -> dict[str, Any]:
    """Sync hanya SJ ID yang sudah diverifikasi discovery (read-only Accurate)."""
    _guard_db(db, mongo_guard=mongo_guard)
    ids = [as_text(x) for x in sj_ids if as_text(x)]
    if not ids:
        return {
            "ok": False,
            "code": "no_sj_ids",
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "review_required": 0,
            "failed": 0,
            "listed": 0,
            "pages": 0,
            "skip_reasons": {},
            "failures": [],
            "auto_sync": False,
            "writes_accurate": False,
            "deletes_missing_sj": False,
        }

    acquire_delivery_sync_lock(db, scope=scope)
    reader = client or AccurateReadClient.from_settings()
    counters = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "review_required": 0,
        "failed": 0,
        "listed": 0,
        "pages": 0,
    }
    skip_reasons: dict[str, int] = {}
    failures: list[dict[str, Any]] = []
    try:
        for sj_id in ids:
            counters["listed"] += 1
            try:
                detail = reader.fetch_delivery_order_detail(sj_id)
                outcome = sync_delivery_order_payload(
                    db,
                    user,
                    detail,
                    mongo_guard=mongo_guard,
                    require_registered_po=require_registered_po,
                )
                key = str(outcome.get("outcome") or "failed")
                if key == "skipped":
                    counters["skipped"] += 1
                    reason = str(outcome.get("reason") or "unknown")
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                elif key == "review_required":
                    counters["review_required"] += 1
                elif key in counters:
                    counters[key] += 1
                else:
                    counters["failed"] += 1
                    failures.append(
                        {
                            "stage": "ingest",
                            "accurate_sj_id": sj_id,
                            "code": "unknown_outcome",
                            "error": key,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                counters["failed"] += 1
                failures.append(
                    {
                        "stage": "detail_or_ingest",
                        "accurate_sj_id": sj_id,
                        **_safe_error(exc),
                    }
                )
    finally:
        release_delivery_sync_lock(db, scope=scope)

    processed = counters["created"] + counters["updated"] + counters["unchanged"]
    return {
        "ok": counters["failed"] == 0 and processed > 0,
        "stopped_reason": None,
        "selected": True,
        "elapsed_seconds": None,
        **counters,
        "skip_reasons": skip_reasons,
        "failures": failures[:50],
        "auto_sync": False,
        "writes_accurate": False,
        "deletes_missing_sj": False,
    }


def sync_one_delivery_order_from_accurate(
    db: Database,
    user: dict[str, Any],
    accurate_sj_id: str | int,
    *,
    client: AccurateReadClient | None = None,
    mongo_guard: MongoGuardMode = "test",
    require_registered_po: bool = True,
) -> dict[str, Any]:
    """Manual sync satu SJ by Accurate ID."""
    _guard_db(db, mongo_guard=mongo_guard)
    reader = client or AccurateReadClient.from_settings()
    try:
        payload = reader.fetch_delivery_order_detail(accurate_sj_id)
    except (AccurateAuthError, TimeoutError, ConnectionError):
        raise
    except PyMongoError as exc:
        raise ConnectionError(f"MongoDB error: {exc.__class__.__name__}") from exc
    except ServerSelectionTimeoutError as exc:
        raise ConnectionError(
            "MongoDB tidak terjangkau (ServerSelectionTimeout)."
        ) from exc

    result = sync_delivery_order_payload(
        db,
        user,
        payload,
        mongo_guard=mongo_guard,
        require_registered_po=require_registered_po,
    )
    result["fetch_ok"] = True
    return result
