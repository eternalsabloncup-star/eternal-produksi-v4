#!/usr/bin/env python3
"""Verifikasi Accurate GET read-only untuk delta PO tanpa branch_id.

- Serial terbatas + sleep/backoff/retry transient.
- Checkpoint terikat delta_sha256 + freshness; stale/foreign diabaikan.
- identity_match wajib True untuk kandidat valid.
- Zero Mongo write / zero Accurate POST.
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

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
_SCRIPTS = _ROOT / "scripts"
for p in (_BACKEND, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from lib.po_branch_repair_safety import (  # noqa: E402
    file_sha256,
    sealed_manifest_from_verify_report,
)

FLOOR = date(2026, 11, 1)
KNOWN = {"50", "150"}
OFFICIAL_NAMES = {"50": "Yogyakarta", "150": "Semarang"}
CHECKPOINT_MAX_AGE_HOURS = 12.0


def _nid(v: Any) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    if not t or t.lower() in {"none", "null"}:
        return None
    return t


def _load_delta(path: Path) -> tuple[list[dict[str, Any]], str]:
    raw_text = path.read_text()
    digest = file_sha256(path)
    raw = json.loads(raw_text)
    if isinstance(raw, dict) and "items" in raw:
        return list(raw["items"]), digest
    if isinstance(raw, list):
        return list(raw), digest
    raise SystemExit(f"Delta tidak dikenali: {path}")


def _checkpoint_meta_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".meta.json")


def _load_checkpoint(
    path: Path, *, delta_sha256: str
) -> dict[str, dict[str, Any]]:
    done: dict[str, dict[str, Any]] = {}
    meta_path = _checkpoint_meta_path(path)
    if not path.is_file() or not meta_path.is_file():
        return done
    meta = json.loads(meta_path.read_text())
    if meta.get("delta_sha256") != delta_sha256:
        return done
    created = meta.get("created_at")
    if not created:
        return done
    ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 3600.0
    if age_h < 0 or age_h > CHECKPOINT_MAX_AGE_HOURS:
        return done
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        aid = str(row.get("accurate_id") or "")
        if not aid or row.get("ok") is not True:
            continue
        if row.get("delta_sha256") != delta_sha256:
            continue
        if row.get("identity_match") is not True:
            continue
        if not row.get("detail_verified"):
            continue
        # Freshness per-row
        verified_at = row.get("verified_at")
        if not verified_at:
            continue
        vt = datetime.fromisoformat(str(verified_at).replace("Z", "+00:00"))
        if vt.tzinfo is None:
            vt = vt.replace(tzinfo=timezone.utc)
        row_age = (
            datetime.now(timezone.utc) - vt.astimezone(timezone.utc)
        ).total_seconds() / 3600.0
        if row_age < 0 or row_age > CHECKPOINT_MAX_AGE_HOURS:
            continue
        done[aid] = row
    return done


def _ensure_checkpoint_meta(path: Path, *, delta_sha256: str) -> None:
    meta_path = _checkpoint_meta_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        if meta.get("delta_sha256") == delta_sha256:
            return
        # Manifest berubah — buang checkpoint lama.
        path.write_text("")
    meta_path.write_text(
        json.dumps(
            {
                "delta_sha256": delta_sha256,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )


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


def _identity_match(*, mongo_po_no: Any, accurate_number: Any) -> bool:
    """Fail-closed: kedua sisi harus ada dan sama."""
    left = _nid(mongo_po_no)
    right = _nid(accurate_number)
    if left is None or right is None:
        return False
    return left == right


def _classify(row: dict[str, Any]) -> str:
    if row.get("tasks") or row.get("alloc") or row.get("sj") or row.get("schedule"):
        return "OPERATIONAL_DEPENDENCY_PRESENT"
    mongo_branch = _nid(row.get("branch_id"))
    src = _nid(row.get("accurate_branch_id"))
    if mongo_branch and src and mongo_branch != src:
        return "BRANCH_CONFLICT"
    if mongo_branch:
        return "ALREADY_HAS_BRANCH_ID"
    if not row.get("detail_verified"):
        return "SOURCE_DETAIL_NOT_VERIFIED"
    if row.get("identity_match") is not True:
        return "REVIEW_REQUIRED_IDENTITY"
    if not src or src not in KNOWN:
        return "UNKNOWN_OR_INVALID_BRANCH"
    if not row.get("master_branch_active"):
        return "UNKNOWN_OR_INVALID_BRANCH"
    from app.services.accurate_auto_sync.date_floor import parse_accurate_trans_date
    from app.services.po_acceptance import ACCEPTANCE_ACCEPTED

    parsed = parse_accurate_trans_date(row.get("accurate_trans_date"))
    if parsed is None:
        return "INVALID_OR_MISSING_TRANSACTION_DATE"
    if parsed < FLOOR:
        return "OUT_OF_SCOPE_BEFORE_FLOOR"
    if row.get("acceptance") == ACCEPTANCE_ACCEPTED or row.get("acceptance") == "accepted":
        return "REVIEW_REQUIRED_ACCEPTED"
    return "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"


def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    needles = ("timeout", "temporar", "429", "502", "503", "504", "connection", "reset")
    return any(n in name or n in msg for n in needles)


def _dep_counts(db: Any, aid: str) -> dict[str, int]:
    tasks = 1 if db["operational_tasks"].find_one(
        {"$or": [{"accurate_po_id": aid}, {"accurate_id": aid}, {"ref.accurate_po_id": aid}]},
        {"_id": 1},
    ) else 0
    alloc = 1 if db["production_allocations"].find_one(
        {"$or": [{"accurate_po_id": aid}, {"accurate_id": aid}]}, {"_id": 1}
    ) else 0
    sj = 1 if db["accurate_delivery_notes"].find_one(
        {"$or": [{"accurate_po_id": aid}, {"accurate_po_ids": aid}]}, {"_id": 1}
    ) else 0
    schedule = 1 if db["schedule_commands"].find_one(
        {"accurate_po_id": aid}, {"_id": 1}
    ) else 0
    return {"tasks": tasks, "alloc": alloc, "sj": sj, "schedule": schedule}


def main() -> int:
    p = argparse.ArgumentParser(description="Accurate detail verify RO — PO branch repair")
    p.add_argument("--delta", required=True, help="JSON delta missing branch_id")
    p.add_argument("--checkpoint", required=True, help="JSONL checkpoint path")
    p.add_argument("--out", required=True, help="Report JSON output")
    p.add_argument("--sealed-out", default="", help="Optional sealed candidates JSON")
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

    client = AccurateReadClient.from_settings()
    if getattr(client, "status", None) is not None and str(client.status.value) != "ready":
        print(json.dumps({"ok": False, "error": f"accurate_not_ready:{client.status.value}"}))
        return 3

    db = get_db(settings)
    master_active = {
        str(d.get("code")): bool(d.get("is_active"))
        for d in db["master_branches"].find({}, {"code": 1, "is_active": 1, "name": 1})
    }

    delta, delta_sha256 = _load_delta(Path(args.delta))
    if args.limit and args.limit > 0:
        delta = delta[: args.limit]
    _ensure_checkpoint_meta(Path(args.checkpoint), delta_sha256=delta_sha256)
    done = _load_checkpoint(Path(args.checkpoint), delta_sha256=delta_sha256)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    fetched = 0
    resumed = 0

    for idx, base in enumerate(delta, start=1):
        aid = str(base.get("accurate_id") or "")
        if not aid:
            continue
        if aid in done:
            results.append(done[aid])
            resumed += 1
            continue

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
        deps = _dep_counts(db, aid)

        detail = None
        last_err = None
        for attempt in range(1, args.max_retries + 1):
            try:
                detail = client.fetch_sales_order_detail(aid)
                break
            except Exception as exc:  # noqa: BLE001
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
                "delta_sha256": delta_sha256,
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
        mongo_po_no = (doc or {}).get("po_no") or base.get("po_no")
        acceptance = (
            ((doc or {}).get("operational") or {}).get("acceptance") or {}
        ).get("status") or base.get("acceptance")
        identity = _identity_match(mongo_po_no=mongo_po_no, accurate_number=src_number)
        row = {
            "accurate_id": aid,
            "ok": True,
            "detail_verified": True,
            "delta_sha256": delta_sha256,
            "verification_level": "VERIFIED_FROM_ACCURATE_DETAIL",
            "po_no": mongo_po_no,
            "accurate_number": src_number,
            "identity_match": identity,
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
            **deps,
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
        and r.get("identity_match") is True
    ]
    report = {
        "ok": len(errors) == 0 and len(results) == len(delta),
        "mode": "ACCURATE_DETAIL_VERIFY_READ_ONLY",
        "wrote_mongo": False,
        "wrote_accurate": False,
        "date_floor": FLOOR.isoformat(),
        "delta_input": len(delta),
        "delta_sha256": delta_sha256,
        "verified_ok": sum(1 for r in results if r.get("ok")),
        "fetched_this_run": fetched,
        "resumed_from_checkpoint": resumed,
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
            "identity_match=False → REVIEW_REQUIRED_IDENTITY (bukan kandidat valid).",
            "Checkpoint hanya resume jika delta_sha256 + freshness cocok.",
            "Gunakan --sealed-out untuk manifest immutable apply.",
        ],
    }
    if len(results) + len([e for e in errors if e.get("accurate_id")]) < len(
        {str(x.get("accurate_id")) for x in delta if x.get("accurate_id")}
    ):
        report["ok"] = False
        report["notes"].append("partial_coverage")

    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    sealed = sealed_manifest_from_verify_report(report)
    sealed_path = Path(args.sealed_out) if args.sealed_out else Path(args.out).with_name(
        "valid_repair_candidates.sealed.json"
    )
    sealed_path.write_text(json.dumps(sealed, indent=2))
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "out": args.out,
                "sealed_out": str(sealed_path),
                "manifest_sha256": sealed["manifest_sha256"],
                "verified_ok": report["verified_ok"],
                "fetched_this_run": fetched,
                "resumed_from_checkpoint": resumed,
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
