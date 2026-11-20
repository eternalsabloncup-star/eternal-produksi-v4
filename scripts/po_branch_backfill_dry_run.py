#!/usr/bin/env python3
"""Dry-run identitas cabang PO (Accurate branchId kanonis) — READ-ONLY.

Kategori:
  HAS_VALID_BRANCH_ID — branch_id / meta Accurate ada dan berbentuk valid
  MISSING_BRANCH_ID — tidak ada branchId
  UNKNOWN_BRANCH_ID — ada ID tetapi tidak ada di Master (opsional katalog)
  CONFLICT — nilai bertentangan antar field
  READY_TO_BACKFILL — meta Accurate ada, top-level branch_id kosong

Write / --execute TIDAK diaktifkan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.branch_identity import (  # noqa: E402
    extract_accurate_branch_id,
    normalize_branch_identity,
)
from app.services.object_auth import object_branch_code  # noqa: E402

CATEGORIES = (
    "HAS_VALID_BRANCH_ID",
    "MISSING_BRANCH_ID",
    "UNKNOWN_BRANCH_ID",
    "CONFLICT",
    "READY_TO_BACKFILL",
)


def classify_po(
    doc: dict[str, Any],
    *,
    master_ids: set[str],
) -> dict[str, Any]:
    accurate_id = str(doc.get("accurate_id") or "")
    top = normalize_branch_identity(doc.get("branch_id"))
    meta = extract_accurate_branch_id(
        {"header": doc.get("header"), "accurate_branch_id": doc.get("accurate_branch_id")}
    )
    # Konflik bila top-level dan meta keduanya ada tetapi berbeda
    if top and meta and top != meta:
        return {
            "accurate_id": accurate_id,
            "category": "CONFLICT",
            "branch_id": top,
            "accurate_branch_id": meta,
            "note": f"top={top} meta={meta}",
        }
    effective = top or meta
    if not effective:
        # legacy branch_code tanpa Accurate id — bukan identitas kanonis baru
        legacy = normalize_branch_identity(doc.get("branch_code"))
        if legacy and not str(legacy).isdigit():
            return {
                "accurate_id": accurate_id,
                "category": "CONFLICT",
                "branch_id": legacy,
                "accurate_branch_id": None,
                "note": "legacy_branch_code_without_accurate_id",
            }
        return {
            "accurate_id": accurate_id,
            "category": "MISSING_BRANCH_ID",
            "branch_id": None,
            "accurate_branch_id": None,
            "note": None,
        }
    if meta and not top:
        unknown = bool(master_ids and meta not in master_ids)
        return {
            "accurate_id": accurate_id,
            "category": "READY_TO_BACKFILL",
            "branch_id": None,
            "accurate_branch_id": meta,
            "note": (
                "unknown_in_master_catalog"
                if unknown
                else "safe_after_owner_approval"
            ),
            "unknown_in_master": unknown,
        }
    if master_ids and effective not in master_ids:
        return {
            "accurate_id": accurate_id,
            "category": "UNKNOWN_BRANCH_ID",
            "branch_id": effective,
            "accurate_branch_id": meta,
            "note": "not_in_master_catalog",
        }
    return {
        "accurate_id": accurate_id,
        "category": "HAS_VALID_BRANCH_ID",
        "branch_id": effective,
        "accurate_branch_id": meta or effective,
        "note": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PO Accurate branchId dry-run")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URL") or os.environ.get("MONGODB_URI"))
    parser.add_argument("--db-name", default=os.environ.get("DB_NAME", "eternal_v4"))
    parser.add_argument("--allow-production-readonly", action="store_true")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if args.execute or os.environ.get("ETERNAL_V4_PO_BRANCH_BACKFILL_WRITE") == "1":
        print("ERROR: write/execute DISABLED pada pekerjaan ini.", file=sys.stderr)
        return 3
    if not args.mongo_uri:
        print("ERROR: set MONGO_URL / --mongo-uri", file=sys.stderr)
        return 2
    if ":27017" in args.mongo_uri:
        print("ERROR: menolak Mongo 27017", file=sys.stderr)
        return 2
    if ":27018" in args.mongo_uri and not args.allow_production_readonly:
        print("ERROR: production 27018 memerlukan --allow-production-readonly", file=sys.stderr)
        return 2

    from pymongo import MongoClient

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    db = client[args.db_name]
    master_ids = {
        normalize_branch_identity(b.get("code"))
        for b in db["master_branches"].find({}, {"code": 1})
        if normalize_branch_identity(b.get("code"))
    }
    rows = [
        classify_po(doc, master_ids=master_ids)
        for doc in db["accurate_sales_orders"].find({})
    ]
    counts = Counter(r["category"] for r in rows)
    by_id = Counter(r.get("accurate_branch_id") or r.get("branch_id") for r in rows if r.get("accurate_branch_id") or r.get("branch_id"))
    ready = sum(1 for r in rows if r["category"] == "READY_TO_BACKFILL")
    report = {
        "dry_run": True,
        "wrote_mongo": False,
        "contract": "accurate_branchId_canonical",
        "total_po": len(rows),
        "counts": {k: counts.get(k, 0) for k in CATEGORIES},
        "by_accurate_branch_id": dict(by_id.most_common()),
        "samples": {
            cat: [r["accurate_id"] for r in rows if r["category"] == cat][:10]
            for cat in CATEGORIES
            if counts.get(cat)
        },
        "ready_to_backfill": ready,
        "note_ids_50_150": "ID 50/150 adalah branchId Accurate bila muncul di sumber — nama tidak dikarang.",
        "planned_change": "NONE — Owner approval required before write backfill.",
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
