#!/usr/bin/env python3
"""Dry-run remediasi cabang PO — DEFAULT READ ONLY.

Mengklasifikasikan delta PO (vs manifest baseline) tanpa write Mongo / Accurate
mutate. Tidak ada mode production write di skrip ini.

Contoh:
  PYTHONPATH=backend python3 scripts/po_branch_repair_dry_run.py \\
    --manifest /path/backfill_manifest_200.json \\
    --out /tmp/po_branch_repair_dry_run.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

FLOOR = date(2026, 11, 1)
KNOWN = {"50", "150"}

# Prioritas eksklusif (pertama menang):
PRIORITY = (
    "OPERATIONAL_DEPENDENCY_PRESENT",
    "BRANCH_CONFLICT",
    "UNKNOWN_OR_INVALID_BRANCH",
    "INVALID_OR_MISSING_TRANSACTION_DATE",
    "OUT_OF_SCOPE_BEFORE_FLOOR",
    "SOURCE_DETAIL_NOT_VERIFIED",
    "CANDIDATE_FROM_MONGO_SNAPSHOT",
    "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR",
)


def _load_manifest_ids(path: Path) -> set[str]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "items" in raw:
        return {
            str(i.get("accurate_id") or i.get("id"))
            for i in raw["items"]
            if i.get("accurate_id") or i.get("id")
        }
    if isinstance(raw, dict):
        for key in ("accurate_ids", "ids", "po_ids"):
            if key in raw:
                return {str(x) for x in raw[key]}
    if isinstance(raw, list):
        out: set[str] = set()
        for i in raw:
            if isinstance(i, dict):
                out.add(str(i.get("accurate_id") or i.get("id")))
            else:
                out.add(str(i))
        return out
    raise SystemExit(f"Manifest tidak dikenali: {path}")


def _parse_trans(value: Any) -> date | None:
    from app.services.accurate_auto_sync.date_floor import parse_accurate_trans_date

    return parse_accurate_trans_date(value)


def _classify(row: dict[str, Any]) -> str:
    """Kategori eksklusif — prioritas berurutan, satu label per PO."""
    if row.get("tasks") or row.get("alloc") or row.get("sj"):
        return "OPERATIONAL_DEPENDENCY_PRESENT"
    branch = str(row.get("branch_id") or "").strip() or None
    meta = str(row.get("meta_branch") or "").strip() or None
    if branch and meta and branch != meta:
        return "BRANCH_CONFLICT"
    if branch:
        # Sudah scoped — bukan kandidat repair kosong→isi.
        return "ALREADY_HAS_BRANCH_ID"
    candidate = meta
    if not candidate or candidate not in KNOWN:
        return "UNKNOWN_OR_INVALID_BRANCH"
    parsed = _parse_trans(row.get("stored_po_date") or row.get("trans_date"))
    if parsed is None:
        return (
            "INVALID_OR_MISSING_TRANSACTION_DATE"
            if row.get("detail_verified")
            else "SOURCE_DETAIL_NOT_VERIFIED"
        )
    if parsed < FLOOR:
        return "OUT_OF_SCOPE_BEFORE_FLOOR"
    # Snapshot Mongo saja ≠ siap production write.
    if row.get("detail_verified"):
        return "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"
    return "CANDIDATE_FROM_MONGO_SNAPSHOT"


def main() -> int:
    p = argparse.ArgumentParser(description="Dry-run klasifikasi remediasi branch_id PO")
    p.add_argument("--manifest", required=True, help="Manifest baseline 200 PO")
    p.add_argument("--out", required=True, help="Output JSON lokal")
    p.add_argument(
        "--allow-production-readonly",
        action="store_true",
        help="Izinkan baca Mongo production (tetap zero write)",
    )
    args = p.parse_args()

    # Env app — jangan override kill-switch.
    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, get_db

    clear_settings_cache()
    close_client()
    settings = get_settings()
    if settings.app_env == "production" and not args.allow_production_readonly:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "production_readonly_flag_required",
                    "hint": "Pass --allow-production-readonly untuk audit baca saja",
                }
            )
        )
        return 2

    db = get_db(settings)
    baseline = _load_manifest_ids(Path(args.manifest))
    col = db["accurate_sales_orders"]
    all_ids = {str(d["accurate_id"]) for d in col.find({}, {"accurate_id": 1})}
    new_ids = sorted(all_ids - baseline, key=lambda x: int(x) if x.isdigit() else x)

    items: list[dict[str, Any]] = []
    for aid in new_ids:
        d = col.find_one(
            {"accurate_id": aid},
            {
                "accurate_id": 1,
                "po_no": 1,
                "branch_id": 1,
                "created_at": 1,
                "source_synced_at": 1,
                "header.po_date": 1,
                "header.raw_sync_meta": 1,
                "operational.acceptance.status": 1,
            },
        )
        if not d:
            continue
        h = d.get("header") or {}
        meta = h.get("raw_sync_meta") or {}
        tasks = db["operational_tasks"].count_documents(
            {"$or": [{"accurate_po_id": aid}, {"accurate_id": aid}]}
        )
        alloc = db["production_allocations"].count_documents(
            {"$or": [{"accurate_po_id": aid}, {"accurate_id": aid}]}
        )
        sj = db["accurate_delivery_notes"].count_documents(
            {"$or": [{"accurate_po_id": aid}, {"accurate_po_ids": aid}]}
        )
        row = {
            "accurate_id": aid,
            "po_no": d.get("po_no"),
            "stored_po_date": h.get("po_date"),
            "trans_date": h.get("po_date"),
            "branch_id": d.get("branch_id"),
            "meta_branch": meta.get("accurate_branch_id"),
            "acceptance": ((d.get("operational") or {}).get("acceptance") or {}).get(
                "status"
            ),
            "created_at": str(d.get("created_at")),
            "source_synced_at": str(d.get("source_synced_at")),
            "tasks": tasks,
            "alloc": alloc,
            "sj": sj,
            "detail_verified": False,
        }
        # Snapshot Mongo: header.po_date = transDate Accurate (bukan tanggal nomor PO).
        row["category"] = _classify(row)
        if row.get("detail_verified"):
            row["verification_level"] = "VERIFIED_FROM_ACCURATE_DETAIL"
        elif row["category"] in {
            "SOURCE_DETAIL_NOT_VERIFIED",
            "INVALID_OR_MISSING_TRANSACTION_DATE",
        } and not row.get("stored_po_date"):
            row["verification_level"] = "NOT_VERIFIED"
        else:
            row["verification_level"] = "VERIFIED_FROM_MONGO_SNAPSHOT"
        items.append(row)

    counts: dict[str, int] = {k: 0 for k in PRIORITY}
    counts["ALREADY_HAS_BRANCH_ID"] = 0
    verification_counts = {
        "VERIFIED_FROM_MONGO_SNAPSHOT": 0,
        "VERIFIED_FROM_ACCURATE_DETAIL": 0,
        "NOT_VERIFIED": 0,
    }
    for row in items:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
        verification_counts[row["verification_level"]] = (
            verification_counts.get(row["verification_level"], 0) + 1
        )

    report = {
        "ok": True,
        "mode": "DRY_RUN_READ_ONLY",
        "wrote_mongo": False,
        "date_floor": FLOOR.isoformat(),
        "baseline_count": len(baseline),
        "production_total_po": len(all_ids),
        "new_po_count": len(new_ids),
        "counts": counts,
        "verification_counts": verification_counts,
        "repair_candidates_mongo_snapshot_only": [
            i["accurate_id"]
            for i in items
            if i["category"] == "CANDIDATE_FROM_MONGO_SNAPSHOT"
        ],
        "repair_candidates_production_ready": [
            i["accurate_id"]
            for i in items
            if i["category"] == "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"
            and i.get("verification_level") == "VERIFIED_FROM_ACCURATE_DETAIL"
        ],
        "items": items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Tidak ada production write.",
            "CANDIDATE_FROM_MONGO_SNAPSHOT ≠ siap production write.",
            "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR hanya jika Accurate detail diverifikasi.",
            "Accurate detail live tidak dipanggil di dry-run default.",
        ],
    }
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "ok": True,
                "out": args.out,
                "new_po_count": len(new_ids),
                "counts": counts,
                "verification_counts": verification_counts,
                "repair_candidates_mongo_snapshot_only": len(
                    report["repair_candidates_mongo_snapshot_only"]
                ),
                "repair_candidates_production_ready": len(
                    report["repair_candidates_production_ready"]
                ),
                "wrote_mongo": False,
            }
        )
    )
    close_client()
    clear_settings_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
