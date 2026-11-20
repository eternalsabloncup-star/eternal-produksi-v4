#!/usr/bin/env python3
"""Dry-run klasifikasi cabang PO — DEFAULT READ-ONLY, tidak menulis.

Kategori (kontrak PR #20):
  ALREADY_VALID
  READY_TO_MAP
  MISSING_ACCURATE_BRANCH_ID
  UNMAPPED_ACCURATE_BRANCH_ID
  INVALID_MASTER_BRANCH
  CONFLICT
  SOURCE_LOOKUP_FAILED

Tidak dijalankan dari startup/deploy/scheduler/import.
Write / --execute TIDAK diimplementasikan — fail-closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.branch_mapping import (  # noqa: E402
    BranchResolveStatus,
    extract_accurate_branch_id_from_po,
    normalize_accurate_branch_id,
    resolve_eternal_branch_code,
)
from app.services.object_auth import object_branch_code  # noqa: E402


CATEGORIES = (
    "ALREADY_VALID",
    "READY_TO_MAP",
    "MISSING_ACCURATE_BRANCH_ID",
    "UNMAPPED_ACCURATE_BRANCH_ID",
    "INVALID_MASTER_BRANCH",
    "CONFLICT",
    "SOURCE_LOOKUP_FAILED",
)


def classify_po(
    doc: dict[str, Any],
    *,
    master_by_code: dict[str, dict[str, Any]],
    db: Any | None = None,
    live_aid: str | None = None,
    source_lookup_failed: bool = False,
) -> dict[str, Any]:
    """Klasifikasi satu PO. Tidak menulis Mongo/Accurate."""
    accurate_id = str(doc.get("accurate_id") or "")
    existing = object_branch_code(doc)
    accurate_branch_id = live_aid or extract_accurate_branch_id_from_po(doc)

    if source_lookup_failed and not accurate_branch_id and not existing:
        return {
            "accurate_id": accurate_id,
            "category": "SOURCE_LOOKUP_FAILED",
            "accurate_branch_id": None,
            "proposed_code": None,
            "note": "accurate_detail_lookup_failed",
        }

    if existing:
        master = master_by_code.get(existing)
        if master is None:
            return {
                "accurate_id": accurate_id,
                "category": "INVALID_MASTER_BRANCH",
                "accurate_branch_id": accurate_branch_id,
                "proposed_code": existing,
                "note": "existing_code_not_in_master",
            }
        if not bool(master.get("is_active", False)):
            return {
                "accurate_id": accurate_id,
                "category": "INVALID_MASTER_BRANCH",
                "accurate_branch_id": accurate_branch_id,
                "proposed_code": existing,
                "note": "existing_code_inactive",
            }
        # Mapping berubah setelah simpan ≠ auto-move; historis tetap ALREADY_VALID.
        return {
            "accurate_id": accurate_id,
            "category": "ALREADY_VALID",
            "accurate_branch_id": accurate_branch_id,
            "proposed_code": existing,
            "note": None,
        }

    if not accurate_branch_id:
        return {
            "accurate_id": accurate_id,
            "category": "MISSING_ACCURATE_BRANCH_ID",
            "accurate_branch_id": None,
            "proposed_code": None,
            "note": None,
        }

    if db is None:
        # Tanpa DB: hanya deteksi unmapped numerik — jangan mengarang kode.
        return {
            "accurate_id": accurate_id,
            "category": "UNMAPPED_ACCURATE_BRANCH_ID",
            "accurate_branch_id": accurate_branch_id,
            "proposed_code": None,
            "note": "resolver_requires_db",
        }

    resolved = resolve_eternal_branch_code(db, accurate_branch_id)
    if resolved.status == BranchResolveStatus.RESOLVED and resolved.branch_code:
        return {
            "accurate_id": accurate_id,
            "category": "READY_TO_MAP",
            "accurate_branch_id": accurate_branch_id,
            "proposed_code": resolved.branch_code,
            "note": "safe_for_backfill_after_owner_approval",
        }
    if resolved.status == BranchResolveStatus.CONFLICT:
        return {
            "accurate_id": accurate_id,
            "category": "CONFLICT",
            "accurate_branch_id": accurate_branch_id,
            "proposed_code": None,
            "note": resolved.detail,
        }
    if resolved.status == BranchResolveStatus.INACTIVE_MASTER:
        return {
            "accurate_id": accurate_id,
            "category": "INVALID_MASTER_BRANCH",
            "accurate_branch_id": accurate_branch_id,
            "proposed_code": resolved.branch_code,
            "note": resolved.detail,
        }
    if resolved.status == BranchResolveStatus.INVALID_ID:
        return {
            "accurate_id": accurate_id,
            "category": "CONFLICT",
            "accurate_branch_id": accurate_branch_id,
            "proposed_code": None,
            "note": resolved.detail,
        }
    return {
        "accurate_id": accurate_id,
        "category": "UNMAPPED_ACCURATE_BRANCH_ID",
        "accurate_branch_id": accurate_branch_id,
        "proposed_code": None,
        "note": resolved.detail,
    }


def _impact_note(counts: Counter[str]) -> dict[str, Any]:
    blocked = (
        counts.get("MISSING_ACCURATE_BRANCH_ID", 0)
        + counts.get("UNMAPPED_ACCURATE_BRANCH_ID", 0)
        + counts.get("INVALID_MASTER_BRANCH", 0)
        + counts.get("CONFLICT", 0)
        + counts.get("SOURCE_LOOKUP_FAILED", 0)
    )
    ready = counts.get("READY_TO_MAP", 0)
    return {
        "task_allocation_permission": (
            "PO tanpa branch_code terverifikasi = DENIED untuk user branch-scoped "
            "(object_auth PR #18). operations.full / access.all_branches / SA tetap elevated."
        ),
        "safe_for_backfill_after_approval": ready,
        "manual_handling_required": blocked,
        "already_valid": counts.get("ALREADY_VALID", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PO branch backfill dry-run (read-only)")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URL") or os.environ.get("MONGODB_URI"))
    parser.add_argument("--db-name", default=os.environ.get("DB_NAME", "eternal_v4"))
    parser.add_argument("--allow-production-readonly", action="store_true")
    parser.add_argument("--json-out", default="")
    parser.add_argument(
        "--fetch-accurate",
        action="store_true",
        help="Opsional: GET Accurate detail (batch + rate limit). Tidak menulis Mongo.",
    )
    parser.add_argument("--fetch-limit", type=int, default=0, help="Batas GET Accurate (0=semua missing)")
    parser.add_argument("--fetch-sleep-ms", type=int, default=200)
    parser.add_argument("--execute", action="store_true", help="DILARANG — path write absen")
    args = parser.parse_args()

    if args.execute or os.environ.get("ETERNAL_V4_PO_BRANCH_BACKFILL_WRITE") == "1":
        print(
            "ERROR: write/execute path tidak diaktifkan pada pekerjaan PR #20. "
            "Backfill write tetap DISABLED.",
            file=sys.stderr,
        )
        return 3

    uri = args.mongo_uri
    if not uri:
        print("ERROR: set MONGO_URL / --mongo-uri", file=sys.stderr)
        return 2
    if ":27017" in uri:
        print("ERROR: menolak Mongo 27017 (legacy)", file=sys.stderr)
        return 2
    if ":27018" in uri and not args.allow_production_readonly:
        print("ERROR: production 27018 memerlukan --allow-production-readonly", file=sys.stderr)
        return 2

    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    db = client[args.db_name]

    master_rows = list(
        db["master_branches"].find(
            {},
            {
                "_id": 0,
                "code": 1,
                "name": 1,
                "is_active": 1,
                "accurate_branch_id": 1,
            },
        )
    )
    master_by_code = {
        str(b.get("code") or "").upper(): b for b in master_rows if b.get("code")
    }

    # Snapshot mapping aktif Accurate→Eternal (diagnostik saja).
    mapping_snapshot: dict[str, list[str]] = {}
    for b in master_rows:
        aid = normalize_accurate_branch_id(b.get("accurate_branch_id"))
        if not aid:
            continue
        mapping_snapshot.setdefault(aid, []).append(str(b.get("code") or "").upper())

    reader = None
    if args.fetch_accurate:
        try:
            from app.integrations.accurate.client import AccurateReadClient

            reader = AccurateReadClient.from_settings()
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: Accurate client tidak tersedia ({exc})", file=sys.stderr)
            reader = None

    rows: list[dict[str, Any]] = []
    incomplete_source: list[str] = []
    fetch_budget = args.fetch_limit if args.fetch_limit > 0 else None
    fetched = 0

    for doc in db["accurate_sales_orders"].find({}):
        live_aid = None
        lookup_failed = False
        local_aid = extract_accurate_branch_id_from_po(doc)
        existing = object_branch_code(doc)
        if (
            reader is not None
            and not local_aid
            and not existing
            and (fetch_budget is None or fetched < fetch_budget)
        ):
            try:
                payload = reader.fetch_sales_order_detail(doc.get("accurate_id"))
                raw = payload.get("branchId")
                if raw is None and isinstance(payload.get("branch"), dict):
                    raw = payload["branch"].get("id") or payload["branch"].get("no")
                live_aid = normalize_accurate_branch_id(raw)
                fetched += 1
                if args.fetch_sleep_ms > 0:
                    time.sleep(args.fetch_sleep_ms / 1000.0)
            except Exception as exc:  # noqa: BLE001
                lookup_failed = True
                fetched += 1
                incomplete_source.append(str(doc.get("accurate_id") or ""))
                del exc

        row = classify_po(
            doc,
            master_by_code=master_by_code,
            db=db,
            live_aid=live_aid,
            source_lookup_failed=lookup_failed,
        )
        rows.append(row)

    counts: Counter[str] = Counter(r["category"] for r in rows)
    by_accurate_id: Counter[str] = Counter(
        r["accurate_branch_id"]
        for r in rows
        if r.get("accurate_branch_id")
    )
    by_eternal_code: Counter[str] = Counter(
        r["proposed_code"] for r in rows if r.get("proposed_code")
    )
    impact = _impact_note(counts)

    report = {
        "dry_run": True,
        "wrote_mongo": False,
        "wrote_accurate": False,
        "auto_sync": False,
        "db_name": args.db_name,
        "total_po": len(rows),
        "master_branch_count": len(master_by_code),
        "mapping_snapshot": {
            aid: codes for aid, codes in sorted(mapping_snapshot.items())
        },
        "counts": {k: counts.get(k, 0) for k in CATEGORIES},
        "by_accurate_branch_id": dict(by_accurate_id.most_common()),
        "by_eternal_branch_code": dict(by_eternal_code.most_common()),
        "samples": {
            cat: [r["accurate_id"] for r in rows if r["category"] == cat][:10]
            for cat in CATEGORIES
            if counts.get(cat)
        },
        "incomplete_source_objects": incomplete_source[:50],
        "impact": impact,
        "safe_backfill_count": impact["safe_for_backfill_after_approval"],
        "manual_required_count": impact["manual_handling_required"],
        "fetch_accurate": bool(args.fetch_accurate),
        "fetch_count": fetched,
        "planned_change": (
            "NONE — Owner approval + locked execute path required before any write backfill."
        ),
        "note_ids_50_150": (
            "ID 50/150 tidak diberi arti cabang tanpa mapping Master resmi dari Owner."
        ),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
