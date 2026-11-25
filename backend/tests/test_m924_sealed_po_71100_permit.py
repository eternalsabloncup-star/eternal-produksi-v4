"""M9.24 — sealed single-PO historical floor permit for Accurate PO 71100."""

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
from app.services import historical_po_recovery_audit as audit
from app.services.accurate_auto_sync.date_floor import (
    clear_date_floor_cache,
    configured_date_floor,
)
from app.services.historical_po_recovery import (
    BATCH_ID,
    M924_BATCH_ID,
    P8C_BATCH_ID,
    HistoricalRecoveryError,
    RecoveryApproval,
    assert_id_in_allowlist,
    assert_runtime_approval,
    compute_manifest_sha256,
    default_manifest_path,
    issue_floor_permit,
    load_sealed_manifest,
    recover_one_po,
)
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, acceptance_block
from app.services.po_repair_maintenance import (
    acquire_po_repair_maintenance,
    release_po_repair_maintenance,
)
from app.services.po_sync import sync_sales_order_payload

TARGET_ID = "71100"
TARGET_NO = "PO/2026/02/16/000036"
TARGET_DATE = date(2026, 2, 16)


@pytest.fixture()
def recovery_db(db: Database, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    clear_date_floor_cache()
    ensure_org_master_indexes(db)
    upsert_branch(db, code="50", name="Yogyakarta", is_active=True)
    upsert_branch(db, code="150", name="Semarang", is_active=True)
    audit.ensure_historical_recovery_audit_indexes(db)
    return db


def _m924() -> Any:
    return load_sealed_manifest(default_manifest_path(M924_BATCH_ID))


def _payload(
    *,
    accurate_id: str = TARGET_ID,
    number: str = TARGET_NO,
    trans_date: str = "16/02/2026",
    branch_id: str = "50",
    customer_id: str = "36200",
    line_ids: list[str] | None = None,
) -> dict[str, Any]:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id)
    payload["number"] = number
    payload["transDate"] = trans_date
    payload["branchId"] = int(branch_id)
    payload["customerId"] = int(customer_id)
    payload["customer"] = {"id": int(customer_id), "name": "KYOU MATCHA"}
    lids = line_ids or ["76500", "84452"]
    base = (payload.get("detailItem") or [{}])[0]
    items = []
    for i, lid in enumerate(lids):
        it = copy.deepcopy(base) if isinstance(base, dict) else {}
        it["id"] = int(lid)
        it["quantity"] = 1000 * (i + 1)
        items.append(it)
    payload["detailItem"] = items
    return payload


@contextmanager
def _apply_fence(db: Database, manifest: Any) -> Iterator[dict[str, Any]]:
    handle = acquire_po_repair_maintenance(
        db, ttl_seconds=120, actor="test_m924_po_71100"
    )
    run = audit.start_run(
        db,
        batch_id=manifest.batch_id,
        manifest_sha256=manifest.manifest_sha256,
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


def _recover_apply(db: Database, *, manifest: Any, payload: dict[str, Any]) -> dict[str, Any]:
    with _apply_fence(db, manifest) as fence:
        return recover_one_po(
            db,
            manifest=manifest,
            accurate_id=str(payload["id"]),
            payload=payload,
            mode="apply",
            mongo_guard="test",
            **fence,
        )


# --- Seal / registry ---


def test_m924_manifest_seal_and_exact_one() -> None:
    m = _m924()
    assert m.batch_id == M924_BATCH_ID
    assert m.po_count == 1
    assert m.ids == frozenset({TARGET_ID})
    assert m.global_date_floor == date(2026, 11, 1)
    entry = m.entry(TARGET_ID)
    assert entry is not None
    assert entry.trans_date == TARGET_DATE
    assert entry.branch_id == "50"
    assert entry.customer_id == "36200"
    assert entry.related_sj_ids == ("121064",)
    assert entry.po_no == TARGET_NO
    assert entry.enforce_po_no is True
    raw = json.loads(default_manifest_path(M924_BATCH_ID).read_text(encoding="utf-8"))
    assert compute_manifest_sha256(raw) == m.manifest_sha256
    assert m.id_list_sha256


def test_m924_existing_batches_unchanged() -> None:
    p6 = load_sealed_manifest(default_manifest_path(BATCH_ID))
    p8c = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
    assert p6.po_count == 80
    assert TARGET_ID not in p6.ids
    assert p8c.po_count == 10
    assert TARGET_ID not in p8c.ids
    raw6 = json.loads(default_manifest_path(BATCH_ID).read_text(encoding="utf-8"))
    raw8 = json.loads(default_manifest_path(P8C_BATCH_ID).read_text(encoding="utf-8"))
    assert compute_manifest_sha256(raw6) == p6.manifest_sha256
    assert compute_manifest_sha256(raw8) == p8c.manifest_sha256


def test_m924_tampered_manifest_rejected(tmp_path: Path) -> None:
    raw = json.loads(default_manifest_path(M924_BATCH_ID).read_text(encoding="utf-8"))
    raw["entries"][0]["po_no"] = "PO/TAMPERED"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_checksum_mismatch"


def test_m924_wrong_batch_id_rejected(tmp_path: Path) -> None:
    raw = json.loads(default_manifest_path(M924_BATCH_ID).read_text(encoding="utf-8"))
    raw["batch_id"] = "not_a_real_batch"
    raw["label"] = "not_a_real_batch"
    raw["manifest_sha256"] = compute_manifest_sha256(raw)
    path = tmp_path / "bad_batch.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_batch_mismatch"


def test_m924_approval_requires_id_list() -> None:
    m = _m924()
    with pytest.raises(HistoricalRecoveryError) as ei:
        assert_runtime_approval(
            RecoveryApproval(
                mode="dry_run",
                expect_manifest_sha256=m.manifest_sha256,
                expect_po_count=1,
                expect_batch_id=m.batch_id,
            ),
            manifest=m,
            app_env="test",
        )
    assert ei.value.code == "approval_id_list_mismatch"
    assert_runtime_approval(
        RecoveryApproval(
            mode="dry_run",
            expect_manifest_sha256=m.manifest_sha256,
            expect_po_count=1,
            expect_batch_id=m.batch_id,
            expect_id_list_sha256=m.id_list_sha256,
        ),
        manifest=m,
        app_env="test",
    )


# --- Positive ---


def test_t01_t03_t05_t09_t10_controlled_recovery_creates_unaccepted(
    recovery_db: Database,
) -> None:
    m = _m924()
    payload = _payload()
    out = _recover_apply(recovery_db, manifest=m, payload=payload)
    assert out["ok"] is True
    assert out["outcome"] == "created"
    assert out["writes"] == 1
    doc = get_by_accurate_id(recovery_db, TARGET_ID)
    assert doc is not None
    assert doc.get("number") == TARGET_NO or doc.get("po_no") == TARGET_NO
    assert acceptance_block(doc).get("status") != ACCEPTANCE_ACCEPTED
    # No Loading SJ solely from recovery
    assert (
        recovery_db["operational_tasks"].count_documents(
            {"task_type": "loading-sj", "ref.accurate_po_id": TARGET_ID}
        )
        == 0
    )
    # Audit provenance
    assert recovery_db[audit.ITEMS].count_documents(
        {"batch_id": M924_BATCH_ID, "accurate_po_id": TARGET_ID}
    ) >= 1


def test_t02_ordinary_sync_still_excluded(recovery_db: Database) -> None:
    payload = _payload()
    out = sync_sales_order_payload(recovery_db, payload, mongo_guard="test")
    assert out["writes"] == 0
    assert out["outcome"] == "excluded"
    assert out["reason"] == "before_date_floor"
    assert count_by_accurate_id(recovery_db, TARGET_ID) == 0
    assert configured_date_floor() == date(2026, 11, 1)


def test_t04_idempotent_second_apply(recovery_db: Database) -> None:
    m = _m924()
    payload = _payload()
    first = _recover_apply(recovery_db, manifest=m, payload=payload)
    second = _recover_apply(recovery_db, manifest=m, payload=payload)
    assert first["outcome"] == "created"
    assert second["outcome"] == "already_present"
    assert second["writes"] == 0
    assert count_by_accurate_id(recovery_db, TARGET_ID) == 1


def test_t06_permit_passes_with_exact_identity(recovery_db: Database) -> None:
    m = _m924()
    permit = issue_floor_permit(m, TARGET_ID)
    payload = _payload()
    assert permit.permits(TARGET_ID, payload) is True
    assert permit.expected_po_no == TARGET_NO
    # Ordinary sync still blocked without attaching permit through recovery path:
    blocked = sync_sales_order_payload(recovery_db, payload, mongo_guard="test")
    assert blocked["reason"] == "before_date_floor"


def test_t07_existing_p6_permit_still_works(recovery_db: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(BATCH_ID))
    entry = m.entry("70957")
    assert entry is not None
    payload = _payload(
        accurate_id=entry.accurate_po_id,
        number=f"PO/{entry.trans_date.isoformat()}/X",
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
        customer_id="1",
        line_ids=["1", "2"],
    )
    # customer not required for P6
    payload["customerId"] = 1
    payload["customer"] = {"id": 1, "name": "X"}
    out = _recover_apply(recovery_db, manifest=m, payload=payload)
    assert out["outcome"] == "created"


# --- Negative / security ---


def test_n01_n02_other_ids_rejected() -> None:
    m = _m924()
    with pytest.raises(HistoricalRecoveryError) as ei:
        assert_id_in_allowlist(m, "71101")
    assert ei.value.code == "not_in_allowlist"
    with pytest.raises(HistoricalRecoveryError):
        assert_id_in_allowlist(m, "89867")


def test_n03_wrong_po_number_rejected(recovery_db: Database) -> None:
    m = _m924()
    payload = _payload(number="PO/WRONG/NUMBER")
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=TARGET_ID,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "po_no_mismatch"


def test_n04_wrong_date_rejected(recovery_db: Database) -> None:
    m = _m924()
    payload = _payload(trans_date="17/02/2026")
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=TARGET_ID,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "trans_date_mismatch"


def test_n08_ordinary_sync_cannot_consume_permit(recovery_db: Database) -> None:
    """Floor permit object alone is not used by ordinary sync callers."""
    m = _m924()
    permit = issue_floor_permit(m, TARGET_ID)
    payload = _payload()
    assert permit.permits(TARGET_ID, payload)
    out = sync_sales_order_payload(recovery_db, payload, mongo_guard="test")
    assert out["reason"] == "before_date_floor"
    assert count_by_accurate_id(recovery_db, TARGET_ID) == 0


def test_n10_retry_no_duplicate(recovery_db: Database) -> None:
    m = _m924()
    payload = _payload()
    _recover_apply(recovery_db, manifest=m, payload=payload)
    _recover_apply(recovery_db, manifest=m, payload=payload)
    assert po_collection(recovery_db).count_documents({"accurate_id": TARGET_ID}) == 1


def test_n12_does_not_auto_accept(recovery_db: Database) -> None:
    m = _m924()
    _recover_apply(recovery_db, manifest=m, payload=_payload())
    doc = get_by_accurate_id(recovery_db, TARGET_ID)
    assert acceptance_block(doc).get("status") != ACCEPTANCE_ACCEPTED


def test_n13_does_not_modify_other_po(recovery_db: Database) -> None:
    # seed unrelated on-floor PO
    other = _payload(accurate_id="99002", number="PO/2026/11/01/X", trans_date="01/11/2026")
    sync_sales_order_payload(recovery_db, other, mongo_guard="test")
    before = get_by_accurate_id(recovery_db, "99002")
    m = _m924()
    _recover_apply(recovery_db, manifest=m, payload=_payload())
    after = get_by_accurate_id(recovery_db, "99002")
    assert before is not None and after is not None
    assert before.get("updated_at") == after.get("updated_at") or before == after


def test_n15_n16_no_sj_status_or_flag_mutation(recovery_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "1")
    recovery_db["accurate_auto_sync_status"].insert_one(
        {
            "job": "sj",
            "checkpoint": {"incremental_next_page": 23, "last_completed_id": 120902},
        }
    )
    before = recovery_db["accurate_auto_sync_status"].find_one({"job": "sj"})
    m = _m924()
    _recover_apply(recovery_db, manifest=m, payload=_payload())
    after = recovery_db["accurate_auto_sync_status"].find_one({"job": "sj"})
    assert before == after


def test_n19_branch_mismatch_rejected(recovery_db: Database) -> None:
    m = _m924()
    payload = _payload(branch_id="150")
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=TARGET_ID,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "branch_mismatch"


def test_n20_missing_lines_rejected(recovery_db: Database) -> None:
    m = _m924()
    payload = _payload()
    payload["detailItem"] = []
    out = recover_one_po(
        recovery_db,
        manifest=m,
        accurate_id=TARGET_ID,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "no_detail_items"
