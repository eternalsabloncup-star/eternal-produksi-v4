#!/usr/bin/env python3
"""Manual sync Surat Jalan Accurate → Mongo V4 NON-PRODUCTION saja.

Syarat:
  - V4_SJ_SYNC_ENABLED=1
  - DB_NAME / MONGODB_DB = eternal_v4_test_*
  - assert_safe_mongo_sync_target PASS
  - Host Accurate bukan *.test
  - Hanya GET Accurate (client existing)

Tidak menulis Accurate. Tidak menyentuh eternal_v4 / eternal_produksi_v4.
Output aman: counters + kode error — tanpa token, signature, atau payload pelanggan.

Mode:
  (default) list+detail sync window
  --discover          read-only cari SJ↔SO single-SO (tanpa tulis Mongo)
  --sj-ids a,b,c      sync hanya SJ terpilih (hasil discovery)

Contoh:
  export APP_ENV=test
  export DB_NAME=eternal_v4_test_sj_manual
  export MONGO_URL='mongodb://…/?authSource=…&replicaSet=rs0'
  export V4_SJ_SYNC_ENABLED=1
  PYTHONPATH=backend python3 scripts/accurate_sj_sync_nonprod.py --discover --prefer-po-id 95106
  PYTHONPATH=backend python3 scripts/accurate_sj_sync_nonprod.py --sj-ids 123,456
"""

from __future__ import annotations

import argparse
import json
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


def _parse_sj_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in str(raw).replace(" ", "").split(","):
        if part and part not in out:
            out.append(part)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SJ Accurate → Mongo test (manual)")
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-rows", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--actor-email",
        default="owner@eternal.local",
        help="Email aktor audit (dinormalisasi di service)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Read-only discovery SJ↔SO; tidak menulis Mongo",
    )
    parser.add_argument(
        "--prefer-po-id",
        default="",
        help="PO Accurate yang diprioritaskan bila ada SJ single-SO terkait",
    )
    parser.add_argument(
        "--sj-ids",
        default="",
        help="Comma-separated Accurate SJ IDs untuk sync terarah",
    )
    args = parser.parse_args(argv)

    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, ensure_indexes, get_db
    from app.db_guard import UnsafeMongoTargetError
    from app.integrations.accurate.auth import AccurateAuthError
    from app.integrations.accurate.client import AccurateReadClient
    from app.services import delivery_sync
    from app.services.delivery_sj_discovery import discover_sj_so_links

    clear_settings_cache()
    close_client()
    settings = get_settings()

    host = _host_of(settings.accurate_base_url)
    if not host or host.endswith(".test"):
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "accurate_host_not_live",
                    "error": "Accurate host missing or placeholder *.test",
                }
            )
        )
        return 3

    if not settings.sj_sync_enabled and not args.discover:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "SJ_SYNC_DISABLED",
                    "error": "Set V4_SJ_SYNC_ENABLED=1 setelah verifikasi isolasi",
                }
            )
        )
        return 4

    user = {
        "email_normalized": str(args.actor_email).strip().lower(),
        "role": "super_admin",
    }
    prefer = (args.prefer_po_id or "").strip()
    sj_ids = _parse_sj_ids(args.sj_ids)

    try:
        client = AccurateReadClient.from_settings()

        if args.discover:
            report = discover_sj_so_links(
                client=client,
                page_size=args.page_size,
                max_pages=args.max_pages,
                max_rows=args.max_rows,
                timeout_seconds=args.timeout_seconds,
                prefer_po_id=prefer or None,
            )
            safe = {
                "ok": bool(report.get("ok")) and int(report.get("selected_sj_count") or 0) > 0,
                "stage": "discover",
                "mode": "discover",
                "mongodb_db": settings.mongodb_db,
                "writes_accurate": False,
                "listed": report.get("listed"),
                "pages": report.get("pages"),
                "single_so_sj": report.get("single_so_sj"),
                "multi_so_rejected": report.get("multi_so_rejected"),
                "identity_failed": report.get("identity_failed"),
                "detail_failed": report.get("detail_failed"),
                "distinct_po_count": report.get("distinct_po_count"),
                "prefer_po_id": report.get("prefer_po_id"),
                "prefer_po_sj_count": report.get("prefer_po_sj_count"),
                "selected_po_id": report.get("selected_po_id"),
                "selected_sj_ids": report.get("selected_sj_ids"),
                "selected_sj_count": report.get("selected_sj_count"),
                "selection_reason": report.get("selection_reason"),
                "stopped_reason": report.get("stopped_reason"),
                "elapsed_seconds": report.get("elapsed_seconds"),
            }
            print(json.dumps(safe, ensure_ascii=False))
            return 0 if safe["ok"] else 11

        if not settings.sj_sync_enabled:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "code": "SJ_SYNC_DISABLED",
                        "error": "Set V4_SJ_SYNC_ENABLED=1 setelah verifikasi isolasi",
                    }
                )
            )
            return 4

        db = get_db(settings)
        ensure_indexes(db)

        if sj_ids:
            result = delivery_sync.sync_selected_delivery_orders_from_accurate(
                db,
                user,
                sj_ids,
                client=client,
                mongo_guard="test",
                scope="cli-nonprod-selected",
            )
        else:
            result = delivery_sync.sync_delivery_orders_from_accurate(
                db,
                user,
                client=client,
                page_size=args.page_size,
                max_pages=args.max_pages,
                max_rows=args.max_rows,
                timeout_seconds=args.timeout_seconds,
                mongo_guard="test",
                scope="cli-nonprod",
            )
    except UnsafeMongoTargetError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "unsafe_mongo_target"}))
        return 5
    except delivery_sync.DeliverySyncDisabledError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "sync_disabled"}))
        return 4
    except delivery_sync.DeliverySyncInProgressError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "sync_in_progress"}))
        return 6
    except AccurateAuthError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "accurate_auth"}))
        return 7
    except TimeoutError:
        print(json.dumps({"ok": False, "code": "timeout", "error": "accurate_timeout"}))
        return 8
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "sync_failed",
                    "error": type(exc).__name__,
                }
            )
        )
        return 9
    finally:
        close_client()
        clear_settings_cache()

    safe = {
        "ok": bool(result.get("ok")),
        "stage": "sync",
        "mode": "selected" if sj_ids else "list",
        "mongodb_db": settings.mongodb_db,
        "sj_sync_enabled": True,
        "writes_accurate": False,
        "deletes_missing_sj": False,
        "auto_sync": False,
        "created": result.get("created"),
        "updated": result.get("updated"),
        "unchanged": result.get("unchanged"),
        "skipped": result.get("skipped"),
        "failed": result.get("failed"),
        "listed": result.get("listed"),
        "pages": result.get("pages"),
        "skip_reasons": result.get("skip_reasons") or {},
        "stopped_reason": result.get("stopped_reason"),
        "selected": bool(result.get("selected")),
        "failure_count": len(result.get("failures") or []),
        "failure_codes": [
            f.get("code") for f in (result.get("failures") or []) if isinstance(f, dict)
        ][:20],
    }
    print(json.dumps(safe, ensure_ascii=False))
    return 0 if safe["ok"] else 10


if __name__ == "__main__":
    raise SystemExit(main())
