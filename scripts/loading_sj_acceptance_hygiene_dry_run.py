#!/usr/bin/env python3
"""M8.1 Loading-SJ integrity — READ-ONLY dry-run classification report.

Reports loading-sj tasks by parent PO acceptance and structural integrity.
Does NOT delete, update, or repair any records.

Usage (VPS or local with MONGO_URL):
  python scripts/loading_sj_acceptance_hygiene_dry_run.py
  python scripts/loading_sj_acceptance_hygiene_dry_run.py --limit 20

Owner policy required before any APPLY / bulk cleanup.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50, help="Max example rows")
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional path to write full JSON report",
    )
    args = parser.parse_args()

    mongo_url = os.environ.get("MONGO_URL") or os.environ.get("MONGODB_URI")
    db_name = os.environ.get("DB_NAME") or os.environ.get("MONGODB_DB") or "eternal_v4"
    if not mongo_url:
        print("MONGO_URL required", file=sys.stderr)
        return 2

    from pymongo import MongoClient

    from app.repositories.po_repository import get_by_accurate_id
    from app.services.object_auth import (
        resolve_task_source_po_ids,
        task_source_po_accepted,
    )
    from app.services.po_acceptance import is_po_accepted

    client = MongoClient(mongo_url)
    db = client[db_name]

    status_counter: Counter[str] = Counter()
    class_counter: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    hidden_ok = 0
    would_show_if_bug = 0
    total = 0
    task_key_counts: Counter[str] = Counter()
    sj_ref_counts: Counter[str] = Counter()

    cursor = db.operational_tasks.find({"task_type": "loading-sj"})
    for task in cursor:
        total += 1
        task_key = str(task.get("task_key") or "")
        if task_key:
            task_key_counts[task_key] += 1
        ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
        sj_id = str(
            ref.get("accurate_delivery_note_id") or ref.get("accurate_sj_id") or ""
        ).strip()
        if sj_id:
            sj_ref_counts[sj_id] += 1

        po_ids = resolve_task_source_po_ids(db, task)
        statuses: list[str] = []
        branches_po: set[str] = set()
        for pid in po_ids:
            po = get_by_accurate_id(db, pid)
            if po is None:
                statuses.append("MISSING_PO")
            elif is_po_accepted(po):
                statuses.append("accepted")
                header = po.get("header") if isinstance(po.get("header"), dict) else {}
                branch = header.get("branch") if isinstance(header.get("branch"), dict) else {}
                bid = branch.get("id") or header.get("branch_id") or po.get("branch_id")
                if bid is not None and str(bid).strip():
                    branches_po.add(str(bid))
            else:
                st = (
                    ((po.get("operational") or {}).get("acceptance") or {}).get(
                        "status"
                    )
                    or "null"
                )
                statuses.append(str(st))

        note = (
            db.accurate_delivery_notes.find_one({"accurate_sj_id": sj_id})
            if sj_id
            else None
        )
        branches_sj: set[str] = set()
        if note:
            header = note.get("header") if isinstance(note.get("header"), dict) else {}
            branch = header.get("branch") if isinstance(header.get("branch"), dict) else {}
            bid = (
                branch.get("id")
                or header.get("branch_id")
                or note.get("branch_id")
            )
            if bid is not None and str(bid).strip():
                branches_sj.add(str(bid))

        key = ",".join(sorted(set(statuses))) if statuses else "NO_PO_REF"
        status_counter[key] += 1

        accepted_visible = task_source_po_accepted(db, task)
        if not accepted_visible:
            hidden_ok += 1
        else:
            if any(s != "accepted" for s in statuses):
                would_show_if_bug += 1

        # Deterministic classification (first matching bucket wins).
        if not sj_id:
            bucket = "missing_sj"
        elif note is None:
            bucket = "orphan_sj"
        elif not po_ids or "MISSING_PO" in statuses:
            bucket = "missing_po"
        elif len(set(statuses)) > 1 and "accepted" in statuses:
            bucket = "ambiguous_parent"
        elif accepted_visible and all(s == "accepted" for s in statuses):
            bucket = "accepted_valid"
        elif str(task.get("status") or "") == "selesai" and not accepted_visible:
            bucket = "completed_non_accepted"
        else:
            bucket = "non_accepted"
        class_counter[bucket] += 1

        branch_inconsistent = bool(
            branches_po and branches_sj and branches_po.isdisjoint(branches_sj)
        )
        if branch_inconsistent:
            class_counter["branch_inconsistent"] += 1

        if len(examples) < args.limit and (
            bucket != "accepted_valid" or branch_inconsistent
        ):
            examples.append(
                {
                    "task_key": task.get("task_key"),
                    "status": task.get("status"),
                    "accurate_sj_id": sj_id or None,
                    "po_ids": po_ids,
                    "acceptance_statuses": statuses,
                    "classification": bucket,
                    "branch_inconsistent": branch_inconsistent,
                    "visible_under_accepted_gate": accepted_visible,
                }
            )

    duplicate_task_keys = sum(1 for _, n in task_key_counts.items() if n > 1)
    duplicate_sj_refs = sum(1 for _, n in sj_ref_counts.items() if n > 1)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db_name": db_name,
        "mode": "READ_ONLY_DRY_RUN",
        "mutations": "NONE",
        "total_loading_sj_tasks": total,
        "acceptance_status_buckets": dict(status_counter),
        "classification_buckets": dict(class_counter),
        "duplicate_task_keys": duplicate_task_keys,
        "duplicate_sj_refs": duplicate_sj_refs,
        "hidden_by_accepted_gate": hidden_ok,
        "visibility_anomaly_non_accepted_shown": would_show_if_bug,
        "examples": examples,
        "owner_policy_required_for": [
            "bulk archive/delete of non-accepted loading-sj tasks",
            "APPLY remediation",
            "enabling V4_SJ_SYNC_PRODUCTION_ENABLED",
        ],
        "prospective_code_guards": [
            "object_auth.task_source_po_accepted",
            "task_open_counts filters via task_source_po_accepted",
            "delivery_service._require_accepted_po_for_note on confirm/assign/mismatch",
            "delivery_service.assert_branch_access on confirm/cancel/assign",
        ],
    }
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    # Allow running from repo root with backend on path.
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    backend = os.path.join(root, "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    raise SystemExit(_main())
