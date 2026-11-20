#!/usr/bin/env python3
"""Owner-gated PO branch_id repair apply — DEFAULT REFUSE.

Tidak dijalankan oleh agent M3. Hanya setelah:
  1) Owner approval tertulis
  2) Production SHA == exact approved deploy SHA (main berisi PR #21+)
  3) Manifest kandidat dari verify Accurate detail
  4) Kill-switch PO tetap OFF
  5) Flag eksplisit --i-understand-this-writes-production

Setiap write memakai CAS ``set_branch_id_if_empty_and_unaccepted`` (atomik).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def main() -> int:
    p = argparse.ArgumentParser(description="Owner-gated branch_id repair (CAS)")
    p.add_argument("--candidates", required=True, help="JSON valid_repair_candidates")
    p.add_argument("--expect-live-sha", required=True)
    p.add_argument("--audit-out", required=True)
    p.add_argument(
        "--i-understand-this-writes-production",
        action="store_true",
        help="Wajib untuk mengizinkan write",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Tanpa flag ini: dry simulation only (masih zero write)",
    )
    args = p.parse_args()

    if not args.i_understand_this_writes_production or not args.execute:
        print(
            json.dumps(
                {
                    "ok": False,
                    "refused": True,
                    "reason": "missing_owner_write_flags",
                    "hint": "Butuh --execute dan --i-understand-this-writes-production",
                    "wrote_mongo": False,
                }
            )
        )
        return 2

    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, get_db
    from app.repositories.po_repository import set_branch_id_if_empty_and_unaccepted

    clear_settings_cache()
    close_client()
    settings = get_settings()
    if getattr(settings, "v4_po_auto_sync_enabled", False):
        print(json.dumps({"ok": False, "refused": True, "reason": "po_auto_sync_must_be_off"}))
        return 3

    live = Path("/home/eternaladmin/eternal-production-v4")
    if live.is_dir():
        import subprocess

        sha = subprocess.check_output(
            ["git", "-C", str(live), "rev-parse", "HEAD"], text=True
        ).strip()
        if sha != args.expect_live_sha:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "refused": True,
                        "reason": "live_sha_mismatch",
                        "live": sha,
                        "expect": args.expect_live_sha,
                    }
                )
            )
            return 4

    raw = json.loads(Path(args.candidates).read_text())
    items = raw.get("valid_repair_candidates") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise SystemExit("candidates invalid")

    db = get_db(settings)
    audit: list[dict[str, Any]] = []
    for it in items:
        aid = str(it.get("accurate_id") or "")
        expect = str(it.get("expected_branch_id") or "")
        if not aid or expect not in {"50", "150"}:
            audit.append({"accurate_id": aid, "status": "skipped_invalid"})
            continue
        before = db["accurate_sales_orders"].find_one(
            {"accurate_id": aid}, {"branch_id": 1, "operational.acceptance.status": 1}
        )
        if before and before.get("branch_id"):
            audit.append(
                {
                    "accurate_id": aid,
                    "status": "already_has_branch",
                    "branch_id": before.get("branch_id"),
                }
            )
            continue
        filled = set_branch_id_if_empty_and_unaccepted(db, aid, expect)
        if filled is None:
            audit.append({"accurate_id": aid, "status": "cas_miss_or_blocked", "expected": expect})
        else:
            audit.append(
                {
                    "accurate_id": aid,
                    "status": "filled",
                    "branch_id": filled.get("branch_id"),
                    "expected": expect,
                }
            )

    report = {
        "ok": True,
        "mode": "OWNER_APPROVED_WRITE",
        "expect_live_sha": args.expect_live_sha,
        "filled": sum(1 for a in audit if a.get("status") == "filled"),
        "blocked": sum(1 for a in audit if a.get("status") == "cas_miss_or_blocked"),
        "already": sum(1 for a in audit if a.get("status") == "already_has_branch"),
        "items": audit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    Path(args.audit_out).write_text(json.dumps(report, indent=2))
    print(json.dumps({"ok": True, "audit_out": args.audit_out, "filled": report["filled"]}))
    close_client()
    clear_settings_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
