"""M9.2B — sealed 10-PO historical parent recovery (isolated Mongo 27019)."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
from pymongo.database import Database

from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import org_master_repository as org
from app.repositories import task_repository as tasks
from app.repositories.po_repository import (
    ensure_po_indexes,
    get_by_accurate_id,
    po_collection,
)
from app.services import historical_po_recovery_audit as po_audit
from app.services.accurate_auto_sync.date_floor import clear_date_floor_cache
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.delivery_service import LOADING_SJ_TASK
from app.services.historical_po_recovery import (
    BATCH_ID as P6_BATCH_ID,
    M92B_BATCH_ID,
    P8C_BATCH_ID,
    HistoricalRecoveryError,
    RecoveryApproval,
    assert_id_in_allowlist,
    assert_runtime_approval,
    compute_id_list_sha256,
    compute_manifest_sha256,
    default_manifest_path,
    load_sealed_manifest,
    recover_one_po,
)
from app.services.po_acceptance import is_po_accepted
from app.services.po_repair_maintenance import (
    acquire_po_repair_maintenance,
    release_po_repair_maintenance,
)

# M9.2A audit used no trailing newline; tooling appends trailing newline.
M92A_AUDIT_ID_LIST_SHA_NO_NL = (
    "fea5ffc8fcdaf231db476265a9b59c12406325d9151d1c800beb801cd967831d"
)
M92B_EXPECTED_IDS = [
    "62913",
    "93693",
    "93696",
    "93728",
    "93785",
    "93788",
    "93852",
    "93871",
    "93901",
    "93907",
]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    monkeypatch.setenv("V4_LOADING_SJ_TASK_ENABLED", "1")
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    clear_date_floor_cache()
    yield
    clear_settings_cache()
    clear_date_floor_cache()


@pytest.fixture()
def tdb(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    ensure_po_indexes(db)
    deliveries.ensure_delivery_indexes(db)
    org.ensure_org_master_indexes(db)
    org.upsert_branch(db, code="50", name="Yogyakarta", is_active=True)
    org.upsert_branch(db, code="150", name="Semarang", is_active=True)
    po_audit.ensure_historical_recovery_audit_indexes(db)
    db["master_task_types"].update_one(
        {"task_type": LOADING_SJ_TASK},
        {
            "$set": {
                "task_type": LOADING_SJ_TASK,
                "label": "Loading Surat Jalan",
                "scope": "delivery_note",
                "assigned_role": "helper",
                "is_active": True,
            }
        },
        upsert=True,
    )
    return db


def _hist_so(
    accurate_id: str,
    *,
    trans_date: str,
    branch_id: str,
    customer_id: str,
    po_no: str,
    lines: int = 2,
) -> dict[str, Any]:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id)
    payload["number"] = po_no
    payload["transDate"] = trans_date
    payload["branchId"] = int(branch_id) if branch_id.isdigit() else branch_id
    payload["customer"] = {"id": int(customer_id), "name": "Cust"}
    payload["customerId"] = int(customer_id)
    items = []
    base = (payload.get("detailItem") or [{}])[0]
    for i in range(lines):
        it = copy.deepcopy(base) if isinstance(base, dict) else {}
        it["id"] = int(f"{accurate_id}{i + 1}")
        it["quantity"] = 10 + i
        items.append(it)
    payload["detailItem"] = items
    return payload


@contextmanager
def _po_fence(db: Database, manifest: Any) -> Iterator[dict[str, Any]]:
    handle = acquire_po_repair_maintenance(
        db, ttl_seconds=120, actor="test_m92b_po"
    )
    run = po_audit.start_run(
        db,
        batch_id=manifest.batch_id,
        manifest_sha256=manifest.manifest_sha256,
        live_sha=None,
        mode="apply",
        po_count=1,
        lease_fp=po_audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    ctx = {
        "run_id": str(run["run_id"]),
        "lease_fp": po_audit.lease_fingerprint(str(handle["owner_token"])),
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


def test_m92b_manifest_loads_exact_10() -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    assert m.batch_id == M92B_BATCH_ID
    assert m.po_count == 10
    assert m.global_date_floor.isoformat() == "2026-11-01"
    assert [e.accurate_po_id for e in m.entries] == M92B_EXPECTED_IDS
    assert m.id_list_sha256 == compute_id_list_sha256(M92B_EXPECTED_IDS)
    assert m.id_list_sha256 != M92A_AUDIT_ID_LIST_SHA_NO_NL
    assert all(e.customer_id and e.related_sj_ids and e.po_no for e in m.entries)
    assert all(e.trans_date.isoformat() < "2026-11-01" for e in m.entries)
    related = {sj for e in m.entries for sj in e.related_sj_ids}
    assert "120851" not in related


def test_m92b_no_collision_with_p6_p8c() -> None:
    m92b = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    p6 = load_sealed_manifest(default_manifest_path(P6_BATCH_ID))
    p8c = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
    assert m92b.ids.isdisjoint(p6.ids)
    assert m92b.ids.isdisjoint(p8c.ids)


def test_m92b_repeated_load_deterministic() -> None:
    a = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    b = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    assert a.manifest_sha256 == b.manifest_sha256
    assert a.id_list_sha256 == b.id_list_sha256
    assert [e.accurate_po_id for e in a.entries] == [
        e.accurate_po_id for e in b.entries
    ]


def test_m92b_tamper_checksum_rejected(tmp_path: Path) -> None:
    src = json.loads(default_manifest_path(M92B_BATCH_ID).read_text())
    src["entries"][0]["customer_id"] = "00000"
    path = tmp_path / "tamper.json"
    path.write_text(json.dumps(src))
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_checksum_mismatch"


def test_m92b_wrong_id_list_rejected(tmp_path: Path) -> None:
    src = json.loads(default_manifest_path(M92B_BATCH_ID).read_text())
    src["id_list_sha256"] = "0" * 64
    src.pop("manifest_sha256", None)
    src["manifest_sha256"] = compute_manifest_sha256(src)
    path = tmp_path / "bad_ids.json"
    path.write_text(json.dumps(src))
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_id_list_mismatch"


def test_m92b_rejects_non_allowlisted_id() -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    with pytest.raises(HistoricalRecoveryError) as ei:
        assert_id_in_allowlist(m, "99999")
    assert ei.value.code == "not_in_allowlist"


def test_m92b_rejects_floor_change(tmp_path: Path) -> None:
    src = json.loads(default_manifest_path(M92B_BATCH_ID).read_text())
    src["global_date_floor"] = "2026-10-01"
    src.pop("manifest_sha256", None)
    src["manifest_sha256"] = compute_manifest_sha256(src)
    path = tmp_path / "floor.json"
    path.write_text(json.dumps(src))
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_floor_changed"


def test_m92b_approval_requires_id_list() -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    dry = RecoveryApproval(
        mode="dry_run",
        expect_manifest_sha256=m.manifest_sha256,
        expect_po_count=m.po_count,
        expect_batch_id=m.batch_id,
    )
    with pytest.raises(HistoricalRecoveryError) as ei:
        assert_runtime_approval(dry, manifest=m, app_env="test")
    assert ei.value.code == "approval_id_list_mismatch"

    ok = RecoveryApproval(
        mode="dry_run",
        expect_manifest_sha256=m.manifest_sha256,
        expect_po_count=m.po_count,
        expect_batch_id=m.batch_id,
        expect_id_list_sha256=m.id_list_sha256,
    )
    assert_runtime_approval(ok, manifest=m, app_env="test")


def test_m92b_dry_run_zero_writes(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    entry = m.entries[0]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id=entry.branch_id,
        customer_id=entry.customer_id,
        po_no=entry.po_no,
    )
    before = po_collection(tdb).count_documents({})
    out = recover_one_po(
        tdb,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is True
    assert out.get("writes", 0) == 0
    assert po_collection(tdb).count_documents({}) == before
    assert tdb[po_audit.RUNS].count_documents({}) == 0


def test_m92b_apply_creates_idempotent_no_acceptance_no_loading(
    tdb: Database,
) -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    entry = m.entries[0]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id=entry.branch_id,
        customer_id=entry.customer_id,
        po_no=entry.po_no,
    )
    with _po_fence(tdb, m) as fence:
        created = recover_one_po(
            tdb,
            manifest=m,
            accurate_id=entry.accurate_po_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            **fence,
        )
    assert created["outcome"] == "created"
    doc = get_by_accurate_id(tdb, entry.accurate_po_id)
    assert doc is not None
    assert is_po_accepted(doc) is False
    assert (
        tasks.tasks_collection(tdb).count_documents({"task_type": LOADING_SJ_TASK})
        == 0
    )
    assert tdb["loading_events"].count_documents({}) == 0
    assert tdb["delivery_events"].count_documents({}) == 0

    with _po_fence(tdb, m) as fence:
        again = recover_one_po(
            tdb,
            manifest=m,
            accurate_id=entry.accurate_po_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            **fence,
        )
    assert again["outcome"] == "already_present"
    assert po_collection(tdb).count_documents({"accurate_id": entry.accurate_po_id}) == 1


def test_m92b_existing_po_preserved(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    entry = m.entries[1]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id=entry.branch_id,
        customer_id=entry.customer_id,
        po_no=entry.po_no,
    )
    with _po_fence(tdb, m) as fence:
        recover_one_po(
            tdb,
            manifest=m,
            accurate_id=entry.accurate_po_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            **fence,
        )
    before = get_by_accurate_id(tdb, entry.accurate_po_id)
    assert before is not None
    with _po_fence(tdb, m) as fence:
        out = recover_one_po(
            tdb,
            manifest=m,
            accurate_id=entry.accurate_po_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            **fence,
        )
    assert out["outcome"] == "already_present"
    after = get_by_accurate_id(tdb, entry.accurate_po_id)
    assert after is not None
    assert after.get("accurate_id") == before.get("accurate_id")


def test_m92b_customer_mismatch_rejected(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    entry = m.entries[0]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id=entry.branch_id,
        customer_id="99999",
        po_no=entry.po_no,
    )
    out = recover_one_po(
        tdb,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "customer_mismatch"


def test_m92b_branch_mismatch_rejected(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    entry = next(e for e in m.entries if e.branch_id == "50")
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id="150",
        customer_id=entry.customer_id,
        po_no=entry.po_no,
    )
    out = recover_one_po(
        tdb,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "branch_mismatch"


def test_m92b_po_no_mismatch_rejected(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(M92B_BATCH_ID))
    entry = m.entries[0]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id=entry.branch_id,
        customer_id=entry.customer_id,
        po_no="PO/WRONG/000000",
    )
    out = recover_one_po(
        tdb,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "po_no_mismatch"


def test_m92b_date_floor_env_unchanged() -> None:
    from app.services.accurate_auto_sync.date_floor import configured_date_floor

    assert configured_date_floor().isoformat() == "2026-11-01"
