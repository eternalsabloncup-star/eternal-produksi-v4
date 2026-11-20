#!/usr/bin/env python3
"""Verifikasi Accurate GET read-only untuk delta PO tanpa branch_id.

- Serial terbatas + sleep/backoff/retry transient.
- Checkpoint lokal JSONL (resume aman).
- Zero Mongo write / zero Accurate POST.
- Output: kandidat repair + review buckets.

Contoh:
  PYTHONPATH=backend python3 scripts/po_branch_verify_accurate_ro.py \\
    --delta /path/delta_missing_branch.json \\
    --checkpoint /tmp/m3_verify_checkpoint.jsonl \\
    --out /tmp/m3_verify_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

FLOOR = date(2026, 11, 1)
KNOWN = {"50", "150"}
OFFICIAL_NAMES = {"50": "Yogyakarta", "150": "Semarang"}


def _nid(v: Any) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    if not t or t.lower() in {"none", "null"}:
        return None
    return t


def _load_delta(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "items" in raw:
        return list(raw["items"])
    if isinstance(raw, list):
        return list(raw)
    raise SystemExit(f"Delta tidak dikenali: {path}")


def _load_checkpoint(path: Path) -> dict[str, dict[str, Any]]:
    done: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return done
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        aid = str(row.get("accurate_id") or "")
        if aid and row.get("ok") is True:
            done[aid] = row
    return done


def _append_checkpoint(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _extract_branch(detail: dict[str, Any]) -> str | None:
    d = detail.get("d") if isinstance(detail.get("d"), dict) else detail
    for key in ("branchId", "branch_id", "branchID"):
        v = _nid(d.get(key) if isinstance(d, dict) else None)
        if v:
            return v
    branch = d.get("branch") if isinstance(d, dict) else None
    if isinstance(branch, dict):
        return _nid(branch.get("id") or branch.get("branchId"))
    return None


def _extract_trans_date(detail: dict[str, Any]) -> Any:
    d = detail.get("d") if isinstance(detail.get("d"), dict) else detail
    if not isinstance(d, dict):
        return None
    return d.get("transDate") or d.get("trans_date") or d.get("poDate")


def _extract_number(detail: dict[str, Any]) -> str | None:
    d = detail.get("d") if isinstance(detail.get("d"), dict) else detail
    if not isinstance(d, dict):
        return None
    return _nid(d.get("number") or d.get("po_no") or d.get("poNo"))


def _classify(row: dict[str, Any]) -> str:
    if row.get("tasks") or row.get("alloc") or row.get("sj"):
        return "OPERATIONAL_DEPENDENCY_PRESENT"
    mongo_branch = _nid(row.get("branch_id"))
    src = _nid(row.get("accurate_branch_id"))
    if mongo_branch and src and mongo_branch != src:
        return "BRANCH_CONFLICT"
    if mongo_branch:
        return "ALREADY_HAS_BRANCH_ID"
    if not row.get("detail_verified"):
        return "SOURCE_DETAIL_NOT_VERIFIED"
    if not src or src not in KNOWN:
        return "UNKNOWN_OR_INVALID_BRANCH"
    if not row.get("master_branch_active"):
        return "UNKNOWN_OR_INVALID_BRANCH"
    from app.services.accurate_auto_sync.date_floor import parse_accurate_trans_date

    parsed = parse_accurate_trans_date(row.get("accurate_trans_date"))
    if parsed is None:
        return "INVALID_OR_MISSING_TRANSACTION_DATE"
    if parsed < FLOOR:
        return "OUT_OF_SCOPE_BEFORE_FLOOR"
    if row.get("acceptance") == "accepted":
        return "REVIEW_REQUIRED_ACCEPTED"
    return "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"


def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    needles = ("timeout", "temporar", "429", "502", "503", "504", "connection", "reset")
    return any(n in name or n in msg for n in needles)


def main() -> int:
    p = argparse.ArgumentParser(description="Accurate detail verify RO — PO branch repair")
    p.add_argument("--delta", required=True, help="JSON delta missing branch_id")
    p.add_argument("--checkpoint", required=True, help="JSONL checkpoint path")
    p.add_argument("--out", required=True, help="Report JSON output")
    p.add_argument("--sleep", type=float, default=0.35, help="Delay antar request")
    p.add_argument("--batch-every", type=int, default=25)
    p.add_argument("--batch-pause", type=float, default=2.0)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--limit", type=int, default=0, help="0 = semua")
    p.add_argument(
        "--allow-production-readonly",
        action="store_true",
        help="Wajib bila APP_ENV=production (tetap zero write)",
    )
    args = p.parse_args()

    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, get_db
    from app.integrations.accurate.client import AccurateReadClient

    clear_settings_cache()
    close_client()
    settings = get_settings()
    if settings.app_env == "production" and not args.allow_production_readonly:
        print(json.dumps({"ok": False, "error": "production_readonly_flag_required"}))
        return 2

    # Kill-switch tidak diubah; hanya baca.
    client = AccurateReadClient.from_settings()
    if getattr(client, "status", None) is not None and str(client.status.value) != "ready":
        print(json.dumps({"ok": False, "error": f"accurate_not_ready:{client.status.value}"}))
        return 3

    db = get_db(settings)
    master_active = {
        str(d.get("code")): bool(d.get("is_active"))
        for d in db["master_branches"].find({}, {"code": 1, "is_active": 1, "name": 1})
    }

    delta = _load_delta(Path(args.delta))
    if args.limit and args.limit > 0:
        delta = delta[: args.limit]
    done = _load_checkpoint(Path(args.checkpoint))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    fetched = 0

    for idx, base in enumerate(delta, start=1):
        aid = str(base.get("accurate_id") or "")
        if not aid:
            continue
        if aid in done:
            results.append(done[aid])
            continue

        # Refresh dependency/acceptance snapshot dari Mongo (read-only).
        doc = db["accurate_sales_orders"].find_one(
            {"accurate_id": aid},
            {
                "branch_id": 1,
                "po_no": 1,
                "operational.acceptance.status": 1,
                "header.po_date": 1,
                "header.raw_sync_meta": 1,
            },
        )
        tasks = db["operational_tasks"].count_documents(
            {"$or": [{"accurate_po_id": aid}, {"accurate_id": aid}]}, limit=1
        )
        alloc = db["production_allocations"].count_documents(
            {"$or": [{"accurate_po_id": aid}, {"accurate_id": aid}]}, limit=1
        )
        sj = db["accurate_delivery_notes"].count_documents(
            {"$or": [{"accurate_po_id": aid}, {"accurate_po_ids": aid}]}, limit=1
        )

        detail = None
        last_err = None
        for attempt in range(1, args.max_retries + 1):
            try:
                detail = client.fetch_sales_order_detail(aid)
                break
            except Exception as exc:  # noqa: BLE001 — bounded retry transient only
                last_err = exc
                if attempt < args.max_retries and _is_transient(exc):
                    time.sleep(min(8.0, args.sleep * (2**attempt)))
                    continue
                break

        if detail is None:
            err = {
                "accurate_id": aid,
                "ok": False,
                "detail_verified": False,
                "error": type(last_err).__name__ if last_err else "fetch_failed",
                "error_msg": str(last_err)[:240] if last_err else None,
            }
            errors.append(err)
            _append_checkpoint(Path(args.checkpoint), err)
            time.sleep(args.sleep)
            continue

        src_branch = _extract_branch(detail)
        src_date = _extract_trans_date(detail)
        src_number = _extract_number(detail)
        mongo_branch = _nid((doc or {}).get("branch_id") or base.get("branch_id"))
        acceptance = (
            ((doc or {}).get("operational") or {}).get("acceptance") or {}
        ).get("status") or base.get("acceptance")
        row = {
            "accurate_id": aid,
            "ok": True,
            "detail_verified": True,
            "verification_level": "VERIFIED_FROM_ACCURATE_DETAIL",
            "po_no": (doc or {}).get("po_no") or base.get("po_no"),
            "accurate_number": src_number,
            "identity_match": (
                src_number is None
                or _nid(base.get("po_no")) is None
                or src_number == _nid(base.get("po_no"))
            ),
            "branch_id": mongo_branch,
            "accurate_branch_id": src_branch,
            "meta_branch": (
                ((doc or {}).get("header") or {}).get("raw_sync_meta") or {}
            ).get("accurate_branch_id")
            or base.get("meta_branch"),
            "accurate_trans_date": src_date,
            "stored_po_date": ((doc or {}).get("header") or {}).get("po_date")
            or base.get("stored_po_date"),
            "master_branch_active": bool(master_active.get(str(src_branch), False)),
            "official_name": OFFICIAL_NAMES.get(str(src_branch or "")),
            "acceptance": acceptance,
            "tasks": int(tasks),
            "alloc": int(alloc),
            "sj": int(sj),
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        row["category"] = _classify(row)
        row["expected_branch_id"] = (
            src_branch
            if row["category"] == "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"
            else None
        )
        results.append(row)
        _append_checkpoint(Path(args.checkpoint), row)
        fetched += 1
        time.sleep(args.sleep)
        if args.batch_every and idx % args.batch_every == 0:
            time.sleep(args.batch_pause)

    counts = Counter(r.get("category") for r in results if r.get("ok"))
    valid = [
        {
            "accurate_id": r["accurate_id"],
            "expected_branch_id": r["expected_branch_id"],
            "accurate_trans_date": r.get("accurate_trans_date"),
            "po_no": r.get("po_no"),
        }
        for r in results
        if r.get("category") == "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"
    ]
    report = {
        "ok": len(errors) == 0,
        "mode": "ACCURATE_DETAIL_VERIFY_READ_ONLY",
        "wrote_mongo": False,
        "wrote_accurate": False,
        "date_floor": FLOOR.isoformat(),
        "delta_input": len(delta),
        "verified_ok": len(results),
        "fetched_this_run": fetched,
        "resumed_from_checkpoint": len(done),
        "errors": len(errors),
        "counts": dict(counts),
        "valid_repair_candidates": valid,
        "valid_repair_count": len(valid),
        "review_buckets": {
            k: [r["accurate_id"] for r in results if r.get("category") == k]
            for k in sorted(counts)
            if k != "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"
        },
        "error_items": errors,
        "items": results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Tidak ada production write.",
            "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR hanya setelah Accurate detail verified.",
            "Write repair memerlukan approval Owner + deploy main + runbook terpisah.",
        ],
    }
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "out": args.out,
                "verified_ok": report["verified_ok"],
                "fetched_this_run": fetched,
                "errors": len(errors),
                "counts": report["counts"],
                "valid_repair_count": len(valid),
                "wrote_mongo": False,
            }
        )
    )
    close_client()
    clear_settings_cache()
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
