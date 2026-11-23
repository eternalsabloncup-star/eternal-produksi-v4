#!/usr/bin/env python3
"""M8 Loading-SJ acceptance hygiene — READ-ONLY dry-run report.

Reports loading-sj tasks whose parent PO(s) are not accepted.
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

    from app.services.object_auth import (
        resolve_task_source_po_ids,
        task_source_po_accepted,
    )
    from app.services.po_acceptance import is_po_accepted
    from app.repositories.po_repository import get_by_accurate_id

    client = MongoClient(mongo_url)
    db = client[db_name]

    status_counter: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    hidden_ok = 0
    would_show_if_bug = 0
    total = 0

    cursor = db.operational_tasks.find({"task_type": "loading-sj"})
    for task in cursor:
        total += 1
        po_ids = resolve_task_source_po_ids(db, task)
        statuses: list[str] = []
        for pid in po_ids:
            po = get_by_accurate_id(db, pid)
            if po is None:
                statuses.append("MISSING_PO")
            elif is_po_accepted(po):
                statuses.append("accepted")
            else:
                st = (
                    ((po.get("operational") or {}).get("acceptance") or {}).get(
                        "status"
                    )
                    or "null"
                )
                statuses.append(str(st))
        key = ",".join(sorted(set(statuses))) if statuses else "NO_PO_REF"
        status_counter[key] += 1

        accepted_visible = task_source_po_accepted(db, task)
        if not accepted_visible:
            hidden_ok += 1
        else:
            # Should only happen when all parents accepted.
            if any(s != "accepted" for s in statuses):
                would_show_if_bug += 1

        if len(examples) < args.limit and (
            not statuses or any(s != "accepted" for s in statuses)
        ):
            ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
            examples.append(
                {
                    "task_key": task.get("task_key"),
                    "status": task.get("status"),
                    "accurate_sj_id": ref.get("accurate_delivery_note_id"),
                    "po_ids": po_ids,
                    "acceptance_statuses": statuses,
                    "visible_under_accepted_gate": accepted_visible,
                }
            )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db_name": db_name,
        "mode": "READ_ONLY_DRY_RUN",
        "mutations": "NONE",
        "total_loading_sj_tasks": total,
        "acceptance_status_buckets": dict(status_counter),
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
            "delivery_service._require_accepted_po_for_note on confirm",
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
