"""CAS apply loop + audit persistence — testable tanpa production flags."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SetBranchFn = Callable[[Any, str, str], dict[str, Any] | None]
PrewriteFn = Callable[..., str | None]
RefreshFn = Callable[..., bool]


class AuditPersistError(RuntimeError):
    """Audit tidak dapat dipersist — write berikutnya wajib dihentikan."""


def persist_audit(path: Path, report: dict[str, Any]) -> None:
    """Atomic-ish write: temp + replace. Gagal → AuditPersistError."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(report, indent=2, default=str))
        tmp.replace(path)
    except OSError as exc:
        raise AuditPersistError(f"audit_persist_failed:{exc}") from exc


def _ok_flag(
    *,
    stopped: bool,
    failed: int,
    blocked: int,
    filled: int,
    already: int,
    skipped: int,
    total: int,
) -> bool:
    return (
        not stopped
        and failed == 0
        and blocked == 0
        and filled + already == total
        and skipped == 0
    )


def run_cas_apply_loop(
    db: Any,
    items: list[dict[str, Any]],
    *,
    audit_path: Path,
    base_report: dict[str, Any],
    set_branch_fn: SetBranchFn,
    prewrite_fn: PrewriteFn,
    refresh_fn: RefreshFn | None,
    maint_handle: dict[str, Any] | None,
    refresh_every: int = 25,
    verify_maintenance_owner: bool = True,
) -> dict[str, Any]:
    """Jalankan CAS per kandidat; persist audit tiap progres; fail-closed partial."""
    approved_ids = {it["accurate_id"] for it in items}
    audit: list[dict[str, Any]] = []
    filled = blocked = already = skipped = failed = 0
    stopped = False
    stop_reason: str | None = None
    wrote_any = False
    audit_persist_error: str | None = None

    def snapshot_report(**extra: Any) -> dict[str, Any]:
        ok = _ok_flag(
            stopped=stopped,
            failed=failed,
            blocked=blocked,
            filled=filled,
            already=already,
            skipped=skipped,
            total=len(items),
        )
        return {
            **base_report,
            "ok": ok,
            "wrote_mongo": wrote_any,
            "stopped": stopped,
            "stop_reason": stop_reason,
            "filled": filled,
            "blocked": blocked,
            "already": already,
            "skipped": skipped,
            "failed": failed,
            "items": list(audit),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }

    def checkpoint(force: bool = False) -> None:
        nonlocal audit_persist_error, stopped, stop_reason, failed
        try:
            persist_audit(audit_path, snapshot_report())
        except AuditPersistError as exc:
            audit_persist_error = str(exc)
            if not stopped:
                stopped = True
                stop_reason = "audit_persist_failed"
                failed += 1
            # Coba persist sekali lagi dengan stop_reason (best-effort).
            try:
                persist_audit(
                    audit_path,
                    snapshot_report(audit_persist_error=audit_persist_error),
                )
            except AuditPersistError:
                pass
            if not force:
                raise

    try:
        for idx, it in enumerate(items):
            if stopped:
                break
            if (
                refresh_fn is not None
                and maint_handle is not None
                and idx
                and idx % max(1, refresh_every) == 0
            ):
                if not refresh_fn(
                    db,
                    owner_token=maint_handle["owner_token"],
                    job_tokens=maint_handle.get("job_tokens"),
                ):
                    stopped = True
                    stop_reason = "maintenance_lease_lost"
                    failed += 1
                    audit.append(
                        {
                            "accurate_id": it["accurate_id"],
                            "status": "lease_lost",
                        }
                    )
                    checkpoint()
                    break

            aid = it["accurate_id"]
            if aid not in approved_ids:
                failed += 1
                audit.append({"accurate_id": aid, "status": "foreign_id_rejected"})
                stopped = True
                stop_reason = "foreign_id"
                checkpoint()
                break

            block = prewrite_fn(
                db,
                accurate_id=aid,
                expected_branch_id=it["expected_branch_id"],
                expected_po_no=it.get("po_no"),
                expected_trans_date=it.get("accurate_trans_date"),
            )
            if block == "already_has_branch":
                blocked += 1
                audit.append(
                    {
                        "accurate_id": aid,
                        "status": "blocked",
                        "reason": "candidate_branched_after_snapshot",
                    }
                )
                stopped = True
                stop_reason = "manifest_drift_mid_apply"
                checkpoint()
                break
            if block is not None:
                blocked += 1
                audit.append(
                    {
                        "accurate_id": aid,
                        "status": "blocked",
                        "reason": block,
                        "expected": it["expected_branch_id"],
                    }
                )
                stopped = True
                stop_reason = block
                checkpoint()
                break

            if maint_handle is not None and verify_maintenance_owner:
                try:
                    from app.services.po_repair_maintenance import (
                        require_maintenance_owner,
                    )

                    require_maintenance_owner(
                        db, owner_token=str(maint_handle["owner_token"])
                    )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    audit.append(
                        {
                            "accurate_id": aid,
                            "status": "maintenance_lost",
                            "error": type(exc).__name__,
                            "msg": str(exc)[:200],
                        }
                    )
                    stopped = True
                    stop_reason = "maintenance_lease_lost"
                    checkpoint()
                    break

            try:
                filled_doc = set_branch_fn(db, aid, it["expected_branch_id"])
            except Exception as exc:  # noqa: BLE001
                failed += 1
                audit.append(
                    {
                        "accurate_id": aid,
                        "status": "write_error",
                        "error": type(exc).__name__,
                        "msg": str(exc)[:200],
                    }
                )
                stopped = True
                stop_reason = "write_error"
                checkpoint()
                break

            if filled_doc is None:
                blocked += 1
                race = prewrite_fn(
                    db,
                    accurate_id=aid,
                    expected_branch_id=it["expected_branch_id"],
                    expected_po_no=it.get("po_no"),
                    expected_trans_date=it.get("accurate_trans_date"),
                )
                audit.append(
                    {
                        "accurate_id": aid,
                        "status": "cas_miss_or_blocked",
                        "reason": race or "cas_miss",
                        "expected": it["expected_branch_id"],
                    }
                )
                stopped = True
                stop_reason = race or "cas_miss"
                checkpoint()
                break

            wrote_any = True
            filled += 1
            audit.append(
                {
                    "accurate_id": aid,
                    "status": "filled",
                    "branch_id": filled_doc.get("branch_id"),
                    "expected": it["expected_branch_id"],
                }
            )
            try:
                checkpoint()
            except AuditPersistError:
                # Stop reason sudah di-set; jangan lanjut write.
                break

        remaining_start = len(audit)
        if stopped and remaining_start < len(items):
            for it in items[remaining_start:]:
                skipped += 1
                audit.append(
                    {
                        "accurate_id": it["accurate_id"],
                        "status": "skipped_after_stop",
                        "stop_reason": stop_reason,
                    }
                )
            checkpoint(force=True)
    except Exception as exc:  # noqa: BLE001 — pastikan audit partial tersimpan
        stopped = True
        stop_reason = stop_reason or f"loop_exception:{type(exc).__name__}"
        failed += 1
        audit.append(
            {
                "status": "loop_exception",
                "error": type(exc).__name__,
                "msg": str(exc)[:200],
                "filled_before_error": filled,
            }
        )
        remaining_start = len([x for x in audit if x.get("accurate_id")])
        # Best-effort skip remainder without inventing ids if malformed
        try:
            checkpoint(force=True)
        except AuditPersistError:
            pass
        raise

    report = snapshot_report()
    if audit_persist_error:
        report["audit_persist_error"] = audit_persist_error
    report["notes"] = list(base_report.get("notes") or []) + [
        "Isolasi: writer lease + Mongo maintenance drain; bukan cek-sebelum-operasi saja.",
        "Partial apply ⇒ ok=false; audit dipersist per progres.",
    ]
    try:
        persist_audit(audit_path, report)
    except AuditPersistError as exc:
        report["ok"] = False
        report["audit_persist_error"] = str(exc)
        report["stopped"] = True
        report["stop_reason"] = report.get("stop_reason") or "audit_persist_failed"
    return report
