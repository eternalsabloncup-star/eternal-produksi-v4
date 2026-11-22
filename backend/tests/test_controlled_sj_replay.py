"""Controlled SJ Replay — isolated Mongo tests (bukan production)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import org_master_repository as org
from app.repositories.po_repository import ensure_po_indexes, get_by_accurate_id
from app.services import controlled_sj_replay as csr
from app.services import controlled_sj_replay_audit as audit
from app.services.accurate_auto_sync.date_floor import clear_date_floor_cache
from app.services.accurate_auto_sync.sj_sync_review import (
    REASON_PO_NOT_REGISTERED,
    STATUS_RESOLVED,
    get_sj_sync_review,
    upsert_sj_sync_review,
)
from app.services.po_repair_maintenance import (
    acquire_po_repair_maintenance,
    enter_po_repair_writer_section,
    is_po_repair_maintenance_active,
    release_po_repair_maintenance,
)
from app.services.po_sync import sync_sales_order_payload

LIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    monkeypatch.setenv("V4_LOADING_SJ_TASK_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    clear_settings_cache()
    clear_date_floor_cache()
    yield
    clear_settings_cache()
    clear_date_floor_cache()


@pytest.fixture()
def sdb(db: Database) -> Database:
    ensure_po_indexes(db)
    deliveries.ensure_delivery_indexes(db)
    org.ensure_org_master_indexes(db)
    org.upsert_branch(db, code="50", name="Cabang 50", is_active=True)
    org.upsert_branch(db, code="150", name="Cabang 150", is_active=True)
    audit.ensure_controlled_sj_replay_audit_indexes(db)
    # Master task type loading-sj agar ensure_loading_sj_task bisa jalan.
    from app.repositories import master_repository as master

    try:
        master.ensure_master_indexes(db)
    except Exception:  # noqa: BLE001
        pass
    db["master_task_types"].update_one(
        {"task_type": "loading-sj"},
        {
            "$set": {
                "task_type": "loading-sj",
                "assigned_role": "helper",
                "label": "Loading Surat Jalan",
                "is_active": True,
            }
        },
        upsert=True,
    )
    return db


def _manifest() -> csr.SealedManifest:
    return csr.load_sealed_manifest(csr.default_manifest_path())


def _sj_payload(
    sj_id: str,
    *,
    so_id: str,
    branch_id: str = "50",
    multi: bool = False,
    cancel: bool = False,
) -> dict[str, Any]:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = int(sj_id)
    payload["number"] = f"TEST-SJ/{sj_id}"
    payload["transDate"] = "02/11/2026"
    payload["branchId"] = int(branch_id)
    item = copy.deepcopy(payload["detailItem"][0])
    item["id"] = int(f"{sj_id}1")
    item["salesOrderId"] = int(so_id)
    if isinstance(item.get("salesOrder"), dict):
        item["salesOrder"]["id"] = int(so_id)
        item["salesOrder"]["number"] = f"PO/{so_id}"
    payload["detailItem"] = [item]
    if multi:
        second = copy.deepcopy(item)
        second["id"] = int(f"{sj_id}2")
        second["salesOrderId"] = 99999999
        if isinstance(second.get("salesOrder"), dict):
            second["salesOrder"]["id"] = 99999999
        payload["detailItem"].append(second)
    if cancel:
        payload["statusName"] = "Cancelled"
    return payload


def _seed_po(db: Database, accurate_id: str, *, branch_id: str = "50") -> None:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id)
    payload["branchId"] = int(branch_id)
    # Tanggal >= floor agar ingest PO test tidak ditolak date floor.
    payload["transDate"] = "02/11/2026"
    out = sync_sales_order_payload(db, payload, mongo_guard="test")
    assert int(out.get("writes") or 0) >= 1 or out.get("was_insert") is True
    assert get_by_accurate_id(db, accurate_id) is not None


class FakeClient:
    def __init__(self, details: dict[str, dict[str, Any]] | None = None) -> None:
        self.details = details or {}
        self.fail_ids: set[str] = set()
        self.calls = 0

    def fetch_delivery_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.calls += 1
        key = str(accurate_id)
        if key in self.fail_ids:
            raise ConnectionError("accurate_down")
        if key not in self.details:
            raise LookupError(key)
        return copy.deepcopy(self.details[key])


def _client_for_manifest(m: csr.SealedManifest, **overrides: dict[str, Any]) -> FakeClient:
    details = {
        e.accurate_sj_id: _sj_payload(
            e.accurate_sj_id, so_id=e.accurate_so_id, branch_id=e.branch_id
        )
        for e in m.entries
    }
    details.update(overrides)
    return FakeClient(details)


def _dry_approval(m: csr.SealedManifest) -> csr.ReplayApproval:
    return csr.ReplayApproval(
        mode="dry_run",
        expect_manifest_sha256=m.manifest_sha256,
        expect_id_list_sha256=m.id_list_sha256,
        expect_sj_count=m.sj_count,
        expect_batch_id=m.batch_id,
    )


def _apply_approval(m: csr.SealedManifest) -> csr.ReplayApproval:
    return csr.ReplayApproval(
        mode="apply",
        expect_manifest_sha256=m.manifest_sha256,
        expect_id_list_sha256=m.id_list_sha256,
        expect_sj_count=m.sj_count,
        expect_batch_id=m.batch_id,
        execute=True,
        confirm_batch_id=m.batch_id,
    )


def test_manifest_integrity_and_excluded_ids() -> None:
    m = _manifest()
    assert m.sj_count == 150
    assert m.id_list_sha256 == csr.APPROVED_ID_LIST_SHA256
    assert m.ids.isdisjoint(csr.EXCLUDED_QUARANTINE_IDS)
    assert len(m.ids) == 150
    # recompute seals
    raw = json.loads(m.path.read_text(encoding="utf-8"))
    assert csr.compute_manifest_sha256(raw) == m.manifest_sha256
    assert (
        csr.compute_id_list_sha256([e.accurate_sj_id for e in m.entries])
        == m.id_list_sha256
    )


def test_manifest_tamper_detected(tmp_path: Path) -> None:
    m = _manifest()
    raw = json.loads(m.path.read_text(encoding="utf-8"))
    raw["entries"][0]["accurate_so_id"] = "99999999"
    p = tmp_path / "tampered.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(csr.ControlledSjReplayError) as ei:
        csr.load_sealed_manifest(p)
    assert ei.value.code == "manifest_checksum_mismatch"


def test_excluded_ids_rejected_from_allowlist() -> None:
    m = _manifest()
    with pytest.raises(csr.ControlledSjReplayError) as ei:
        csr.assert_id_in_allowlist(m, "120851")
    assert ei.value.code == "not_in_allowlist"


def test_dry_run_default_zero_writes(sdb: Database) -> None:
    m = _manifest()
    # seed only first SO to keep test lighter for evaluate path via run
    first = m.entries[0]
    _seed_po(sdb, first.accurate_so_id, branch_id=first.branch_id)
    client = _client_for_manifest(m)
    # seed all unique SOs referenced (80) — required for eligible
    seen: set[str] = set()
    for e in m.entries:
        if e.accurate_so_id in seen:
            continue
        seen.add(e.accurate_so_id)
        _seed_po(sdb, e.accurate_so_id, branch_id=e.branch_id)

    before_notes = sdb["accurate_delivery_notes"].count_documents({})
    before_audit = sdb[audit.RUNS].count_documents({})
    report = csr.run_controlled_sj_replay(
        sdb,
        approval=_dry_approval(m),
        client=client,
        mongo_guard="test",
        app_env="test",
    )
    assert report["ok"] is True
    assert report["mode"] == "dry_run"
    assert report["processed"] == 150
    assert report["summary"]["eligible"] == 150
    assert report["summary"]["writes"] == 0
    assert sdb["accurate_delivery_notes"].count_documents({}) == before_notes
    assert sdb[audit.RUNS].count_documents({}) == before_audit
    assert not is_po_repair_maintenance_active(sdb)


def test_production_apply_guards() -> None:
    m = _manifest()
    with pytest.raises(csr.ControlledSjReplayError) as ei:
        csr.assert_runtime_approval(
            csr.ReplayApproval(
                mode="apply",
                expect_manifest_sha256=m.manifest_sha256,
                expect_id_list_sha256=m.id_list_sha256,
                expect_sj_count=150,
                expect_batch_id=m.batch_id,
                execute=True,
                confirm_batch_id=m.batch_id,
            ),
            manifest=m,
            app_env="production",
            live_sha=None,
        )
    assert ei.value.code == "apply_production_ack_required"

    with pytest.raises(csr.ControlledSjReplayError) as ei2:
        csr.assert_runtime_approval(
            csr.ReplayApproval(
                mode="apply",
                expect_manifest_sha256=m.manifest_sha256,
                expect_id_list_sha256=m.id_list_sha256,
                expect_sj_count=150,
                expect_batch_id=m.batch_id,
                execute=True,
                confirm_batch_id=m.batch_id,
                i_understand_this_writes_production=True,
                expect_live_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
            manifest=m,
            app_env="production",
            live_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
    assert ei2.value.code == "apply_live_sha_mismatch"


def test_dual_apply_second_fails_on_maintenance(sdb: Database) -> None:
    first = acquire_po_repair_maintenance(
        sdb, ttl_seconds=120, actor="controlled_sj_replay_apply"
    )
    with pytest.raises(Exception):
        acquire_po_repair_maintenance(sdb, ttl_seconds=120, actor="other")
    release_po_repair_maintenance(
        sdb,
        owner_token=str(first["owner_token"]),
        job_tokens=dict(first["job_tokens"]),
    )


def test_apply_blocks_writer_section(sdb: Database) -> None:
    handle = acquire_po_repair_maintenance(
        sdb, ttl_seconds=120, actor="controlled_sj_replay_apply"
    )
    with pytest.raises(Exception):
        enter_po_repair_writer_section(sdb)
    release_po_repair_maintenance(
        sdb,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )


def test_apply_lock_unavailable_zero_writes(sdb: Database) -> None:
    m = _manifest()
    blocker = acquire_po_repair_maintenance(
        sdb, ttl_seconds=120, actor="other_maintenance"
    )
    client = _client_for_manifest(m)
    with pytest.raises(csr.ControlledSjReplayError) as ei:
        csr.run_controlled_sj_replay(
            sdb,
            approval=_apply_approval(m),
            client=client,
            mongo_guard="test",
            app_env="test",
        )
    assert ei.value.code == "maintenance_acquire_failed"
    assert sdb["accurate_delivery_notes"].count_documents({}) == 0
    assert sdb[audit.RUNS].count_documents({}) == 0
    release_po_repair_maintenance(
        sdb,
        owner_token=str(blocker["owner_token"]),
        job_tokens=dict(blocker["job_tokens"]),
    )


def test_multi_so_and_branch_conflict_stop(sdb: Database) -> None:
    m = _manifest()
    e = m.entries[0]
    _seed_po(sdb, e.accurate_so_id, branch_id=e.branch_id)
    multi = _sj_payload(e.accurate_sj_id, so_id=e.accurate_so_id, multi=True)
    out = csr.evaluate_sj_candidate(
        sdb, manifest=m, accurate_sj_id=e.accurate_sj_id, payload=multi
    )
    assert out["ok"] is False
    assert out["outcome"] == "conflict"
    assert out["reason"] == "multi_so"

    bad_branch = _sj_payload(
        e.accurate_sj_id, so_id=e.accurate_so_id, branch_id="150" if e.branch_id == "50" else "50"
    )
    out2 = csr.evaluate_sj_candidate(
        sdb, manifest=m, accurate_sj_id=e.accurate_sj_id, payload=bad_branch
    )
    assert out2["ok"] is False
    assert out2["reason"] == "branch_mismatch"


def test_so_mismatch_and_cancel(sdb: Database) -> None:
    m = _manifest()
    e = m.entries[0]
    _seed_po(sdb, e.accurate_so_id, branch_id=e.branch_id)
    wrong = _sj_payload(e.accurate_sj_id, so_id="11111111", branch_id=e.branch_id)
    out = csr.evaluate_sj_candidate(
        sdb, manifest=m, accurate_sj_id=e.accurate_sj_id, payload=wrong
    )
    assert out["reason"] == "so_id_mismatch"
    cancelled = _sj_payload(
        e.accurate_sj_id, so_id=e.accurate_so_id, branch_id=e.branch_id, cancel=True
    )
    out2 = csr.evaluate_sj_candidate(
        sdb, manifest=m, accurate_sj_id=e.accurate_sj_id, payload=cancelled
    )
    assert out2["reason"] == "cancelled_or_void"


def test_apply_one_then_idempotent(sdb: Database) -> None:
    m = _manifest()
    e = m.entries[0]
    _seed_po(sdb, e.accurate_so_id, branch_id=e.branch_id)
    upsert_sj_sync_review(
        sdb,
        accurate_sj_id=e.accurate_sj_id,
        reason=REASON_PO_NOT_REGISTERED,
        source_so_ids=[e.accurate_so_id],
        accurate_po_id=e.accurate_so_id,
        retryable=True,
    )
    payload = _sj_payload(e.accurate_sj_id, so_id=e.accurate_so_id, branch_id=e.branch_id)
    handle = acquire_po_repair_maintenance(
        sdb, ttl_seconds=300, actor="controlled_sj_replay_apply"
    )
    run = audit.start_run(
        sdb,
        batch_id=m.batch_id,
        manifest_sha256=m.manifest_sha256,
        id_list_sha256=m.id_list_sha256,
        live_sha=None,
        mode="apply",
        sj_count=1,
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    first = csr.replay_one_sj(
        sdb,
        manifest=m,
        accurate_sj_id=e.accurate_sj_id,
        payload=payload,
        mode="apply",
        mongo_guard="test",
        run_id=str(run["run_id"]),
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        maintenance_owner_token=str(handle["owner_token"]),
        maintenance_job_tokens=dict(handle["job_tokens"]),
    )
    assert first["ok"] is True
    assert first["outcome"] == "created"
    assert deliveries.get_delivery_note(sdb, e.accurate_sj_id) is not None
    review = get_sj_sync_review(sdb, e.accurate_sj_id)
    assert review is not None
    assert review["status"] == STATUS_RESOLVED

    second = csr.replay_one_sj(
        sdb,
        manifest=m,
        accurate_sj_id=e.accurate_sj_id,
        payload=payload,
        mode="apply",
        mongo_guard="test",
        run_id=str(run["run_id"]),
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        maintenance_owner_token=str(handle["owner_token"]),
        maintenance_job_tokens=dict(handle["job_tokens"]),
        attempt_number=2,
    )
    assert second["ok"] is True
    assert second["outcome"] == "already_present"
    assert second["writes"] == 0
    assert sdb["accurate_delivery_notes"].count_documents(
        {"accurate_sj_id": e.accurate_sj_id}
    ) == 1
    release_po_repair_maintenance(
        sdb,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )


def test_loading_dedup_no_delivery_confirm(sdb: Database) -> None:
    m = _manifest()
    e = m.entries[1]
    _seed_po(sdb, e.accurate_so_id, branch_id=e.branch_id)
    payload = _sj_payload(e.accurate_sj_id, so_id=e.accurate_so_id, branch_id=e.branch_id)
    handle = acquire_po_repair_maintenance(
        sdb, ttl_seconds=300, actor="controlled_sj_replay_apply"
    )
    run = audit.start_run(
        sdb,
        batch_id=m.batch_id,
        manifest_sha256=m.manifest_sha256,
        id_list_sha256=m.id_list_sha256,
        live_sha=None,
        mode="apply",
        sj_count=1,
        lease_fp="abc",
        fence_epoch=1,
    )
    out = csr.replay_one_sj(
        sdb,
        manifest=m,
        accurate_sj_id=e.accurate_sj_id,
        payload=payload,
        mode="apply",
        mongo_guard="test",
        run_id=str(run["run_id"]),
        lease_fp="abc",
        maintenance_owner_token=str(handle["owner_token"]),
        maintenance_job_tokens=dict(handle["job_tokens"]),
    )
    assert out["ok"] is True
    note = deliveries.get_delivery_note(sdb, e.accurate_sj_id)
    assert note is not None
    # Loading availability (may be ensured/skipped depending master readiness)
    assert out.get("loading_task_outcome") in {
        "ensured",
        "already_present",
        "skipped_or_disabled",
    }
    assert deliveries.has_successful_delivery_event(sdb, e.accurate_sj_id) is False
    assert deliveries.get_active_loading_event(sdb, e.accurate_sj_id) is None
    # Dedup second ensure
    again = csr._ensure_loading_availability(sdb, note)
    assert again["outcome"] in {"already_present", "ensured", "skipped_or_disabled"}
    release_po_repair_maintenance(
        sdb,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )


def test_accurate_fetch_failure_stops_batch(sdb: Database) -> None:
    m = _manifest()
    for e in m.entries[:3]:
        _seed_po(sdb, e.accurate_so_id, branch_id=e.branch_id)
    # Minimal client: only first ids, fail first
    details = {
        e.accurate_sj_id: _sj_payload(
            e.accurate_sj_id, so_id=e.accurate_so_id, branch_id=e.branch_id
        )
        for e in m.entries
    }
    client = FakeClient(details)
    client.fail_ids.add(m.entries[0].accurate_sj_id)
    report = csr.run_controlled_sj_replay(
        sdb,
        approval=_apply_approval(m),
        client=client,
        mongo_guard="test",
        app_env="test",
    )
    assert report["ok"] is False
    assert report["stopped"] is True
    assert report["stop_reason"] == "accurate_fetch_failed"
    assert report["processed"] == 1
    assert sdb["accurate_delivery_notes"].count_documents({}) == 0
    assert not is_po_repair_maintenance_active(sdb)


def test_apply_small_subset_via_monkeypatch(
    sdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APPLY 2 SJ untuk kecepatan — seal production tetap diuji terpisah."""
    m = _manifest()
    subset = m.entries[:2]
    for e in subset:
        _seed_po(sdb, e.accurate_so_id, branch_id=e.branch_id)
        upsert_sj_sync_review(
            sdb,
            accurate_sj_id=e.accurate_sj_id,
            reason=REASON_PO_NOT_REGISTERED,
            source_so_ids=[e.accurate_so_id],
            accurate_po_id=e.accurate_so_id,
            retryable=True,
        )

    tiny = csr.SealedManifest(
        label=m.label,
        version=m.version,
        batch_id=m.batch_id,
        audit_date=m.audit_date,
        audit_source=m.audit_source,
        sj_count=2,
        id_list_sha256=csr.compute_id_list_sha256([e.accurate_sj_id for e in subset]),
        manifest_sha256=m.manifest_sha256,
        entries=subset,
        path=m.path,
    )

    def _load(_path=None):
        return tiny

    monkeypatch.setattr(csr, "load_sealed_manifest", _load)
    approval = csr.ReplayApproval(
        mode="apply",
        expect_manifest_sha256=tiny.manifest_sha256,
        expect_id_list_sha256=tiny.id_list_sha256,
        expect_sj_count=2,
        expect_batch_id=tiny.batch_id,
        execute=True,
        confirm_batch_id=tiny.batch_id,
    )
    client = FakeClient(
        {
            e.accurate_sj_id: _sj_payload(
                e.accurate_sj_id, so_id=e.accurate_so_id, branch_id=e.branch_id
            )
            for e in subset
        }
    )
    report = csr.run_controlled_sj_replay(
        sdb,
        approval=approval,
        client=client,
        mongo_guard="test",
        app_env="test",
    )
    assert report["ok"] is True
    assert report["processed"] == 2
    assert report["summary"]["created"] == 2
    assert report["summary"]["writes"] == 2
    assert report["audit_finalize_verified"] is True
    assert sdb[audit.RUNS].count_documents({}) == 1
    assert sdb[audit.ITEMS].count_documents({}) == 2
    run = sdb[audit.RUNS].find_one({})
    assert run["status"] == "completed"
    assert not is_po_repair_maintenance_active(sdb)


def test_finish_run_failure_not_success(
    sdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = _manifest()
    subset = m.entries[:1]
    _seed_po(sdb, subset[0].accurate_so_id, branch_id=subset[0].branch_id)
    tiny = csr.SealedManifest(
        label=m.label,
        version=m.version,
        batch_id=m.batch_id,
        audit_date=m.audit_date,
        audit_source=m.audit_source,
        sj_count=1,
        id_list_sha256=csr.compute_id_list_sha256([subset[0].accurate_sj_id]),
        manifest_sha256=m.manifest_sha256,
        entries=subset,
        path=m.path,
    )
    monkeypatch.setattr(csr, "load_sealed_manifest", lambda _p=None: tiny)

    def _boom(*_a, **_k):
        raise RuntimeError("finalize_boom")

    monkeypatch.setattr(audit, "finish_run", _boom)
    approval = csr.ReplayApproval(
        mode="apply",
        expect_manifest_sha256=tiny.manifest_sha256,
        expect_id_list_sha256=tiny.id_list_sha256,
        expect_sj_count=1,
        expect_batch_id=tiny.batch_id,
        execute=True,
        confirm_batch_id=tiny.batch_id,
    )
    client = FakeClient(
        {
            subset[0].accurate_sj_id: _sj_payload(
                subset[0].accurate_sj_id,
                so_id=subset[0].accurate_so_id,
                branch_id=subset[0].branch_id,
            )
        }
    )
    report = csr.run_controlled_sj_replay(
        sdb,
        approval=approval,
        client=client,
        mongo_guard="test",
        app_env="test",
    )
    assert report["ok"] is False
    assert report["audit_finalize_verified"] is False
    assert report["status"] == "reconciliation_required"
    assert not is_po_repair_maintenance_active(sdb)
