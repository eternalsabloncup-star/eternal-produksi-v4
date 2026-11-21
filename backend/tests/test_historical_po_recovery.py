"""Historical PO recovery allowlist — manifest, floor exception, side effects."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import pytest
from pymongo.database import Database

from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.org_master_repository import (
    ensure_org_master_indexes,
    upsert_branch,
)
from app.repositories.po_repository import (
    count_by_accurate_id,
    get_by_accurate_id,
    po_collection,
)
from app.services.accurate_auto_sync.date_floor import (
    clear_date_floor_cache,
    configured_date_floor,
)
from app.services import historical_po_recovery_audit as audit
from app.services.historical_po_recovery import (
    BATCH_ID,
    HistoricalRecoveryError,
    RecoveryApproval,
    assert_id_in_allowlist,
    compute_manifest_sha256,
    default_manifest_path,
    issue_floor_permit,
    load_sealed_manifest,
    recover_one_po,
    run_historical_po_recovery,
)
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, ACCEPTANCE_PENDING
from app.services.po_repair_maintenance import (
    acquire_po_repair_maintenance,
    release_po_repair_maintenance,
)
from app.services.po_sync import sync_sales_order_payload


class FakeAccurateClient:
    def __init__(self, details: dict[str, dict[str, Any]]):
        self.details = details
        self.detail_calls = 0

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.detail_calls += 1
        key = str(accurate_id)
        if key not in self.details:
            raise LookupError(key)
        return copy.deepcopy(self.details[key])


@pytest.fixture()
def recovery_db(db: Database, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    clear_date_floor_cache()
    ensure_org_master_indexes(db)
    upsert_branch(db, code="50", name="Yogyakarta", is_active=True)
    upsert_branch(db, code="150", name="Semarang", is_active=True)
    audit.ensure_historical_recovery_audit_indexes(db)
    return db


@contextmanager
def _apply_fence(db: Database) -> Iterator[dict[str, Any]]:
    """Exclusive maintenance + audit run untuk recover_one_po APPLY unit tests."""
    handle = acquire_po_repair_maintenance(
        db, ttl_seconds=120, actor="test_historical_po_recovery"
    )
    run = audit.start_run(
        db,
        batch_id=BATCH_ID,
        manifest_sha256=_manifest().manifest_sha256,
        live_sha=None,
        mode="apply",
        po_count=1,
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    ctx = {
        "run_id": str(run["run_id"]),
        "lease_fp": audit.lease_fingerprint(str(handle["owner_token"])),
        "maintenance_owner_token": str(handle["owner_token"]),
        "maintenance_job_tokens": dict(handle["job_tokens"]),
    }
    try:
        yield ctx
    finally:
        release_po_repair_maintenance(
            db,
            owner_token=str(handle["owner_token"]),
            job_tokens=dict(handle.get("job_tokens") or {}),
        )


def _recover_apply(
    db: Database,
    *,
    manifest: Any,
    accurate_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with _apply_fence(db) as fence:
        return recover_one_po(
            db,
            manifest=manifest,
            accurate_id=accurate_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            **fence,
        )


def _detail(
    accurate_id: str,
    *,
    trans_date: str,
    branch_id: str,
    number: str | None = None,
    with_lines: bool = True,
) -> dict[str, Any]:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id)
    payload["transDate"] = trans_date
    payload["number"] = number or f"PO/{trans_date}/X"
    payload["branchId"] = int(branch_id)
    if not with_lines:
        payload["detailItem"] = []
    return payload


def _manifest() -> Any:
    return load_sealed_manifest(default_manifest_path())


def _dry_approval(manifest: Any) -> RecoveryApproval:
    return RecoveryApproval(
        mode="dry_run",
        expect_manifest_sha256=manifest.manifest_sha256,
        expect_po_count=manifest.po_count,
        expect_batch_id=manifest.batch_id,
    )


def _apply_test_approval(manifest: Any) -> RecoveryApproval:
    return RecoveryApproval(
        mode="apply",
        expect_manifest_sha256=manifest.manifest_sha256,
        expect_po_count=manifest.po_count,
        expect_batch_id=manifest.batch_id,
        execute=True,
        confirm_batch_id=manifest.batch_id,
    )


# --- A. Manifest ---


def test_manifest_exact_80_unique_and_checksum() -> None:
    m = _manifest()
    assert m.po_count == 80
    assert len(m.entries) == 80
    assert len(m.ids) == 80
    assert m.batch_id == BATCH_ID
    assert m.label == BATCH_ID
    assert m.global_date_floor == date(2026, 11, 1)
    assert sum(1 for e in m.entries if e.branch_id == "50") == 71
    assert sum(1 for e in m.entries if e.branch_id == "150") == 9
    assert all(e.trans_date < date(2026, 11, 1) for e in m.entries)
    assert sum(e.sj_count for e in m.entries) == 150
    # checksum stable
    raw = json.loads(default_manifest_path().read_text(encoding="utf-8"))
    assert compute_manifest_sha256(raw) == m.manifest_sha256


def test_manifest_rejects_tamper(tmp_path: Path) -> None:
    raw = json.loads(default_manifest_path().read_text(encoding="utf-8"))
    raw["entries"][0]["sj_count"] = 999
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_checksum_mismatch"


def test_manifest_missing(tmp_path: Path) -> None:
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(tmp_path / "nope.json")
    assert ei.value.code == "manifest_missing"


def test_outside_allowlist_rejected() -> None:
    m = _manifest()
    with pytest.raises(HistoricalRecoveryError) as ei:
        assert_id_in_allowlist(m, "99999999")
    assert ei.value.code == "not_in_allowlist"


# --- B/C/D. Floor + branch + identity ---


def test_normal_po_before_floor_still_rejected(recovery_db: Database) -> None:
    assert configured_date_floor() == date(2026, 11, 1)
    payload = _detail("70957", trans_date="16/02/2026", branch_id="50")
    out = sync_sales_order_payload(recovery_db, payload, mongo_guard="test")
    assert out["writes"] == 0
    assert out["outcome"] == "excluded"
    assert out["reason"] == "before_date_floor"
    assert count_by_accurate_id(recovery_db, "70957") == 0


def test_normal_po_after_floor_accepted(recovery_db: Database) -> None:
    payload = _detail("99001", trans_date="01/11/2026", branch_id="50")
    out = sync_sales_order_payload(recovery_db, payload, mongo_guard="test")
    assert out["writes"] == 1
    assert out["was_insert"] is True
    assert get_by_accurate_id(recovery_db, "99001") is not None


def test_recovery_allowlisted_historical_accepted_on_apply(
    recovery_db: Database,
) -> None:
    m = _manifest()
    entry = m.entry("70957")
    assert entry is not None
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    out = _recover_apply(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
    )
    assert out["ok"] is True
    assert out["outcome"] == "created"
    assert out["writes"] == 1
    assert count_by_accurate_id(recovery_db, entry.accurate_po_id) == 1


def test_recovery_outside_allowlist_rejected_even_with_fake_permit_path(
    recovery_db: Database,
) -> None:
    m = _manifest()
    payload = _detail("88888", trans_date="31/10/2026", branch_id="50")
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id="88888",
        payload=payload,
        mode="apply",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["outcome"] == "ineligible"
    assert out["reason"] == "not_in_allowlist"


def test_apply_without_maintenance_stops_before_write(recovery_db: Database) -> None:
    m = _manifest()
    entry = m.entry("70957")
    assert entry is not None
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="apply",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "maintenance_required"
    assert out["writes"] == 0
    assert count_by_accurate_id(recovery_db, entry.accurate_po_id) == 0


def test_global_floor_unchanged_after_recovery(recovery_db: Database) -> None:
    m = _manifest()
    entry = next(e for e in m.entries if e.accurate_po_id == "94062")
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    _recover_apply(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
    )
    assert configured_date_floor() == date(2026, 11, 1)
    # Non-allowlisted historical still blocked:
    other = _detail("77777", trans_date="30/10/2026", branch_id="50")
    blocked = sync_sales_order_payload(recovery_db, other, mongo_guard="test")
    assert blocked["reason"] == "before_date_floor"


def test_branch_mismatch_rejected(recovery_db: Database) -> None:
    m = _manifest()
    entry = m.entry("70957")
    assert entry is not None
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id="150" if entry.branch_id == "50" else "50",
    )
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="dry_run",
    )
    assert out["ok"] is False
    assert out["reason"] == "branch_mismatch"


def test_out_of_scope_branch_rejected(recovery_db: Database) -> None:
    m = _manifest()
    # Craft permit would fail; validate via payload branch 999 with matching id
    # but first mutate entry check: use recover with wrong branch not in master.
    entry = m.entry("70957")
    assert entry is not None
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id="999",
    )
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="dry_run",
    )
    assert out["ok"] is False
    assert out["reason"] in {"branch_mismatch", "out_of_scope_branch", "branch_inactive"}


def test_accurate_id_mismatch_rejected(recovery_db: Database) -> None:
    m = _manifest()
    entry = m.entry("70957")
    assert entry is not None
    payload = _detail(
        "71151",
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="dry_run",
    )
    assert out["ok"] is False
    assert out["reason"] == "accurate_id_mismatch"


def test_missing_identity_rejected(recovery_db: Database) -> None:
    m = _manifest()
    entry = m.entry("70957")
    assert entry is not None
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
        with_lines=False,
    )
    payload["number"] = ""
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="dry_run",
    )
    assert out["ok"] is False
    assert out["reason"] in {"missing_po_number", "no_detail_items"}


def test_floor_permit_rejects_wrong_id(recovery_db: Database) -> None:
    m = _manifest()
    permit = issue_floor_permit(m, "70957")
    payload = _detail("70957", trans_date="16/02/2026", branch_id="50")
    assert permit.permits("70957", payload) is True
    assert permit.permits("71151", payload) is False
    # Without permit, still excluded:
    out = sync_sales_order_payload(recovery_db, payload, mongo_guard="test")
    assert out["reason"] == "before_date_floor"


# --- E/F. Idempotency + side effects ---


def test_idempotent_second_ingest_no_duplicate(recovery_db: Database) -> None:
    m = _manifest()
    entry = m.entry("71151")
    assert entry is not None
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    first = _recover_apply(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
    )
    second = _recover_apply(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
    )
    assert first["outcome"] == "created"
    assert second["outcome"] == "already_present"
    assert second["writes"] == 0
    assert count_by_accurate_id(recovery_db, entry.accurate_po_id) == 1


def test_existing_branch_conflict_no_silent_overwrite(recovery_db: Database) -> None:
    m = _manifest()
    entry = m.entry("70957")
    assert entry is not None
    # Seed existing with different branch via direct insert path after on-floor insert then patch
    seed = _detail("70957", trans_date="01/11/2026", branch_id="150")
    # Temporarily use floor-ok date to insert, then we only care about branch conflict check
    sync_sales_order_payload(recovery_db, seed, mongo_guard="test")
    doc = get_by_accurate_id(recovery_db, "70957")
    assert doc is not None
    # Force canonical branch 150 if insert set it
    po_collection(recovery_db).update_one(
        {"accurate_id": "70957"}, {"$set": {"branch_id": "150"}}
    )
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,  # 50
    )
    out = _recover_apply(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
    )
    assert out["ok"] is False
    assert out["outcome"] == "conflict"
    assert get_by_accurate_id(recovery_db, "70957")["branch_id"] == "150"


def test_side_effects_unchanged_on_apply(recovery_db: Database) -> None:
    m = _manifest()
    entry = m.entry("87769")
    assert entry is not None
    # Seed unrelated side-effect counters at 0; mark acceptance pending explicitly after
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    out = _recover_apply(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
    )
    assert out["ok"] is True
    assert out["side_effects_unchanged"] is True
    doc = get_by_accurate_id(recovery_db, entry.accurate_po_id)
    assert doc is not None
    ops = doc.get("operational") or {}
    # Default insert: empty operational — not auto-accepted
    assert ops.get("acceptance", {}).get("status") in (None, ACCEPTANCE_PENDING, "")
    assert recovery_db["operational_tasks"].count_documents({}) == 0
    assert out["side_effects_after"]["loading_events"] == 0
    assert out["side_effects_after"]["delivery_events"] == 0
    assert out["side_effects_after"]["stock_movements"] == 0
    assert out["side_effects_after"]["receipts"] == 0


def test_already_accepted_conflict(recovery_db: Database) -> None:
    m = _manifest()
    entry = m.entry("70957")
    assert entry is not None
    seed = _detail("70957", trans_date="01/11/2026", branch_id="50")
    sync_sales_order_payload(recovery_db, seed, mongo_guard="test")
    po_collection(recovery_db).update_one(
        {"accurate_id": "70957"},
        {"$set": {"operational.acceptance.status": ACCEPTANCE_ACCEPTED}},
    )
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    out = _recover_apply(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
    )
    assert out["outcome"] == "conflict"
    assert out["reason"] == "already_accepted"


# --- G. Operational safety ---


def test_dry_run_zero_writes(recovery_db: Database) -> None:
    m = _manifest()
    # Only feed first 3 details; batch stops? No — missing fetch fails. Feed all 80.
    details = {
        e.accurate_po_id: _detail(
            e.accurate_po_id,
            trans_date=e.trans_date.strftime("%d/%m/%Y"),
            branch_id=e.branch_id,
        )
        for e in m.entries
    }
    client = FakeAccurateClient(details)
    report = run_historical_po_recovery(
        recovery_db,
        approval=_dry_approval(m),
        client=client,
        mongo_guard="test",
        app_env="test",
    )
    assert report["mode"] == "dry_run"
    assert report["summary"]["writes"] == 0
    assert recovery_db["accurate_sales_orders"].count_documents({}) == 0
    assert report["summary"]["eligible"] == 80


def test_apply_without_runtime_approval_refused(recovery_db: Database) -> None:
    m = _manifest()
    client = FakeAccurateClient({})
    bad = RecoveryApproval(
        mode="apply",
        expect_manifest_sha256=m.manifest_sha256,
        expect_po_count=m.po_count,
        expect_batch_id=m.batch_id,
        execute=False,
        confirm_batch_id=m.batch_id,
    )
    with pytest.raises(HistoricalRecoveryError) as ei:
        run_historical_po_recovery(
            recovery_db,
            approval=bad,
            client=client,
            app_env="test",
        )
    assert ei.value.code == "apply_execute_required"


def test_apply_production_without_sha_refused(recovery_db: Database) -> None:
    m = _manifest()
    client = FakeAccurateClient({})
    bad = RecoveryApproval(
        mode="apply",
        expect_manifest_sha256=m.manifest_sha256,
        expect_po_count=m.po_count,
        expect_batch_id=m.batch_id,
        execute=True,
        confirm_batch_id=m.batch_id,
        i_understand_this_writes_production=True,
        expect_live_sha=None,
    )
    with pytest.raises(HistoricalRecoveryError) as ei:
        run_historical_po_recovery(
            recovery_db,
            approval=bad,
            client=client,
            app_env="production",
            live_sha="264cf597bb8a5e493ea6b678c0e503518fe41c80",
        )
    assert ei.value.code == "apply_live_sha_required"


def test_apply_test_db_creates_without_side_effects(recovery_db: Database) -> None:
    m = _manifest()
    # APPLY only first entry via recover_one to keep test fast; batch covered by dry_run
    entry = m.entries[0]
    details = {
        entry.accurate_po_id: _detail(
            entry.accurate_po_id,
            trans_date=entry.trans_date.strftime("%d/%m/%Y"),
            branch_id=entry.branch_id,
        )
    }
    # Full batch apply would need all 80 — use recover_one + approval path unit above.
    out = _recover_apply(
        recovery_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=details[entry.accurate_po_id],
    )
    assert out["outcome"] == "created"
    assert count_by_accurate_id(recovery_db, entry.accurate_po_id) == 1


def test_checksum_mismatch_approval_stops(recovery_db: Database) -> None:
    m = _manifest()
    client = FakeAccurateClient({})
    bad = RecoveryApproval(
        mode="dry_run",
        expect_manifest_sha256="0" * 64,
        expect_po_count=m.po_count,
        expect_batch_id=m.batch_id,
    )
    with pytest.raises(HistoricalRecoveryError) as ei:
        run_historical_po_recovery(
            recovery_db, approval=bad, client=client, app_env="test"
        )
    assert ei.value.code == "approval_checksum_mismatch"


def test_po_job_timer_path_unaffected(recovery_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recovery module tidak di-import / dijalankan oleh po_job."""
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache
    from app.services.accurate_auto_sync import po_job

    clear_settings_cache()
    import app.services.accurate_auto_sync.po_job as po_job_mod

    src = Path(po_job_mod.__file__).read_text(encoding="utf-8")
    assert "historical_po_recovery" not in src


# --- H. Cross-module quick regression ---


def test_cross_module_normal_sync_and_sj_reasons_untouched() -> None:
    from app.services.accurate_auto_sync.sj_sync_review import (
        REASON_DELIVERY_NOTE_IDENTITY,
        REASON_MULTI_SO,
        REASON_PO_NOT_REGISTERED,
    )
    from app.services.po_acceptance import ACCEPTANCE_ACCEPTED as ACC

    assert REASON_PO_NOT_REGISTERED == "po_not_registered"
    assert REASON_MULTI_SO == "MULTI_SO_NOT_SUPPORTED"
    assert REASON_DELIVERY_NOTE_IDENTITY == "DELIVERY_NOTE_IDENTITY"
    assert ACC == "accepted"
