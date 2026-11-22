"""P10 — sealed P8C parent recovery + controlled multi-PO SJ tooling (isolated Mongo)."""

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
from app.repositories import delivery_repository as deliveries
from app.repositories import org_master_repository as org
from app.repositories import task_repository as tasks
from app.repositories.po_repository import (
    ensure_po_indexes,
    get_by_accurate_id,
    po_collection,
)
from app.services import controlled_sj_replay_audit as sj_audit
from app.services import historical_po_recovery_audit as po_audit
from app.services.accurate_auto_sync.date_floor import clear_date_floor_cache
from app.services.accurate_auto_sync.sj_sync_review import (
    REASON_MULTI_SO,
    STATUS_OPEN,
    STATUS_RESOLVED,
    get_sj_sync_review,
    upsert_sj_sync_review,
)
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.controlled_multi_po_sj_replay import (
    BATCH_ID as SJ_BATCH_ID,
    ControlledMultiPoSjReplayError,
    ReplayApproval as SjApproval,
    assert_runtime_approval as assert_sj_approval,
    compute_id_list_sha256 as sj_id_list_sha,
    compute_manifest_sha256 as sj_manifest_sha,
    default_manifest_path as sj_default_manifest_path,
    evaluate_sj_candidate,
    load_sealed_manifest as load_sj_manifest,
    replay_one_sj,
    run_controlled_multi_po_sj_replay,
)
from app.services.controlled_sj_replay import (
    BATCH_ID as P7D_BATCH_ID,
    load_sealed_manifest as load_p7d_manifest,
)
from app.services.delivery_service import LOADING_SJ_TASK
from app.services.historical_po_recovery import (
    BATCH_ID as P6_BATCH_ID,
    P8C_BATCH_ID,
    HistoricalRecoveryError,
    RecoveryApproval,
    assert_runtime_approval,
    compute_id_list_sha256,
    compute_manifest_sha256,
    default_manifest_path,
    load_sealed_manifest,
    recover_one_po,
    run_historical_po_recovery,
)
from app.services.po_repair_maintenance import (
    acquire_po_repair_maintenance,
    release_po_repair_maintenance,
)
from app.services.po_sync import sync_sales_order_payload

LIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"
)
OWNER = {
    "email_normalized": "owner@eternal.local",
    "role": "super_admin",
    "effective_module_permissions": ["access.all_branches"],
}


class FakeSOClient:
    def __init__(self, details: dict[str, dict[str, Any]]):
        self.details = details

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        key = str(accurate_id)
        if key not in self.details:
            raise LookupError(key)
        return copy.deepcopy(self.details[key])


class FakeDOClient:
    def __init__(self, details: dict[str, dict[str, Any]]):
        self.details = details

    def fetch_delivery_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        key = str(accurate_id)
        if key not in self.details:
            raise LookupError(key)
        return copy.deepcopy(self.details[key])


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    monkeypatch.setenv("V4_LOADING_SJ_TASK_ENABLED", "1")
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
    sj_audit.ensure_controlled_sj_replay_audit_indexes(db)
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
    branch_id: str = "50",
    customer_id: str = "30501",
    lines: int = 2,
) -> dict[str, Any]:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id)
    payload["number"] = f"PO/TEST/{accurate_id}"
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


def _sj(
    sj_id: str,
    *,
    so_ids: list[str],
    branch_id: str = "50",
) -> dict[str, Any]:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = int(sj_id)
    payload["number"] = f"DO/TEST/{sj_id}"
    payload["transDate"] = "05/11/2026"
    payload["branchId"] = int(branch_id)
    base = payload["detailItem"][0]
    items = []
    for i, so_id in enumerate(so_ids):
        item = copy.deepcopy(base)
        item["id"] = int(f"{sj_id}{i + 1}")
        item["salesOrderId"] = int(so_id)
        item["salesOrderDetailId"] = int(f"{so_id}{i + 1}")
        item["quantity"] = 5
        if isinstance(item.get("salesOrder"), dict):
            item["salesOrder"]["id"] = int(so_id)
            item["salesOrder"]["number"] = f"PO/{so_id}"
        items.append(item)
    payload["detailItem"] = items
    return payload


def _seed_po(db: Database, accurate_id: str, *, branch_id: str = "50") -> None:
    payload = _hist_so(accurate_id, trans_date="2026-11-05", branch_id=branch_id)
    # post-floor for normal sync seed of supporting parents
    sync_sales_order_payload(db, payload, mongo_guard="test")


# --- P6 regression ---


def test_p6_manifest_still_loads_exactly_80() -> None:
    m = load_sealed_manifest()
    assert m.batch_id == P6_BATCH_ID
    assert m.po_count == 80
    assert len(m.entries) == 80


def test_p7d_manifest_untouched() -> None:
    m = load_p7d_manifest()
    assert m.batch_id == P7D_BATCH_ID
    assert m.sj_count == 150


def test_p8c_manifest_loads_exactly_10() -> None:
    m = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
    assert m.batch_id == P8C_BATCH_ID
    assert m.po_count == 10
    assert len(m.id_list_sha256) == 64
    assert all(e.customer_id and e.related_sj_ids for e in m.entries)


def test_unknown_batch_rejected(tmp_path: Path) -> None:
    body = {
        "label": "not_a_real_batch",
        "version": "1",
        "batch_id": "not_a_real_batch",
        "audit_date": "2026-11-22",
        "audit_source": "test",
        "global_date_floor": "2026-11-01",
        "po_count": 1,
        "entries": [
            {
                "accurate_po_id": "1",
                "trans_date": "2026-01-01",
                "branch_id": "50",
                "sj_count": 1,
            }
        ],
    }
    body["manifest_sha256"] = compute_manifest_sha256(body)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(body))
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_batch_mismatch"


def test_p8c_rejects_eleven_entries(tmp_path: Path) -> None:
    src = json.loads(default_manifest_path(P8C_BATCH_ID).read_text())
    src["entries"].append(
        {
            "accurate_po_id": "99999",
            "trans_date": "2026-08-01",
            "branch_id": "50",
            "sj_count": 1,
            "customer_id": "1",
            "related_sj_ids": ["119068"],
            "line_ids_sha256": "a" * 64,
        }
    )
    src["po_count"] = 11
    ids = [e["accurate_po_id"] for e in src["entries"]]
    src["id_list_sha256"] = compute_id_list_sha256(ids)
    src.pop("manifest_sha256", None)
    src["manifest_sha256"] = compute_manifest_sha256(src)
    path = tmp_path / "eleven.json"
    path.write_text(json.dumps(src))
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_count_mismatch"


def test_p8c_tamper_checksum_rejected(tmp_path: Path) -> None:
    src = json.loads(default_manifest_path(P8C_BATCH_ID).read_text())
    src["entries"][0]["customer_id"] = "00000"
    path = tmp_path / "tamper.json"
    path.write_text(json.dumps(src))
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_checksum_mismatch"


def test_p8c_wrong_id_list_rejected(tmp_path: Path) -> None:
    src = json.loads(default_manifest_path(P8C_BATCH_ID).read_text())
    src["id_list_sha256"] = "0" * 64
    src.pop("manifest_sha256", None)
    src["manifest_sha256"] = compute_manifest_sha256(src)
    path = tmp_path / "bad_ids.json"
    path.write_text(json.dumps(src))
    with pytest.raises(HistoricalRecoveryError) as ei:
        load_sealed_manifest(path)
    assert ei.value.code == "manifest_id_list_mismatch"


def test_p8c_approval_requires_id_list() -> None:
    m = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
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


def test_p8c_dry_run_zero_writes(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
    entry = m.entries[0]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id=entry.branch_id,
        customer_id=entry.customer_id,
    )
    client = FakeSOClient({entry.accurate_po_id: payload})
    # tiny one-entry path via recover_one dry
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


@contextmanager
def _po_fence(db: Database, manifest: Any) -> Iterator[dict[str, Any]]:
    handle = acquire_po_repair_maintenance(
        db, ttl_seconds=120, actor="test_p10_po"
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


def test_p8c_apply_creates_and_idempotent(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
    entry = m.entries[0]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id=entry.branch_id,
        customer_id=entry.customer_id,
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
    assert get_by_accurate_id(tdb, entry.accurate_po_id) is not None

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


def test_p8c_customer_mismatch_rejected(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
    entry = m.entries[0]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id=entry.branch_id,
        customer_id="99999",
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


def test_p8c_branch_mismatch_rejected(tdb: Database) -> None:
    m = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
    entry = m.entries[0]
    payload = _hist_so(
        entry.accurate_po_id,
        trans_date=entry.trans_date.isoformat(),
        branch_id="150",
        customer_id=entry.customer_id,
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


# --- Multi-PO SJ ---


def test_sj_manifest_exact_six_and_excludes_120851() -> None:
    m = load_sj_manifest()
    assert m.batch_id == SJ_BATCH_ID
    assert m.sj_count == 6
    assert "120851" not in m.ids
    assert m.ids == frozenset(
        {"119068", "119155", "119721", "119950", "119979", "120806"}
    )


def test_sj_rejects_120851() -> None:
    m = load_sj_manifest()
    with pytest.raises(ControlledMultiPoSjReplayError) as ei:
        evaluate_sj_candidate(
            # db unused until allowlist
            type("D", (), {})(),  # type: ignore[arg-type]
            manifest=m,
            accurate_sj_id="120851",
            payload={"id": "120851"},
        )
    assert ei.value.code == "excluded_forever"


def test_sj_rejects_non_allowlist() -> None:
    m = load_sj_manifest()
    with pytest.raises(ControlledMultiPoSjReplayError) as ei:
        evaluate_sj_candidate(
            type("D", (), {})(),  # type: ignore[arg-type]
            manifest=m,
            accurate_sj_id="111111",
            payload={"id": "111111"},
        )
    assert ei.value.code == "not_in_allowlist"


def test_sj_missing_parent_ineligible(tdb: Database) -> None:
    m = load_sj_manifest()
    entry = next(e for e in m.entries if e.accurate_sj_id == "119979")
    # seed only one parent
    _seed_po(tdb, entry.parent_po_ids[0])
    payload = _sj(entry.accurate_sj_id, so_ids=list(entry.parent_po_ids))
    # align detail fingerprint by leaving empty detail_attr in evaluate — sealed has hash
    # rebuild fingerprint match: compute from payload and temporarily skip by using evaluate
    # after patching entry via local copy — instead clear mismatch by updating payload
    # to match sealed fingerprint is hard; use evaluate with fingerprint bypass via
    # temporary manifest mutation through object replace:
    from dataclasses import replace

    soft = replace(entry, detail_attr_sha256="")
    soft_manifest = replace(
        m,
        entries=tuple(soft if e.accurate_sj_id == entry.accurate_sj_id else e for e in m.entries),
    )
    out = evaluate_sj_candidate(
        tdb,
        manifest=soft_manifest,
        accurate_sj_id=entry.accurate_sj_id,
        payload=payload,
    )
    assert out["ok"] is False
    assert out["reason"] == "INELIGIBLE_PARENT_NOT_REGISTERED"


def test_sj_apply_multi_po_full_path(tdb: Database) -> None:
    m = load_sj_manifest()
    entry = next(e for e in m.entries if e.accurate_sj_id == "119979")
    for pid in entry.parent_po_ids:
        _seed_po(tdb, pid)
    payload = _sj(entry.accurate_sj_id, so_ids=list(entry.parent_po_ids))
    from dataclasses import replace

    soft_entry = replace(entry, detail_attr_sha256="")
    soft = replace(
        m,
        entries=tuple(
            soft_entry if e.accurate_sj_id == entry.accurate_sj_id else e for e in m.entries
        ),
    )
    upsert_sj_sync_review(
        tdb,
        accurate_sj_id=entry.accurate_sj_id,
        reason=REASON_MULTI_SO,
        source_so_ids=list(entry.parent_po_ids),
        retryable=True,
    )
    before_events = tdb["loading_events"].count_documents({})
    before_del = tdb["delivery_events"].count_documents({})

    handle = acquire_po_repair_maintenance(tdb, ttl_seconds=120, actor="test_p10_sj")
    run = sj_audit.start_run(
        tdb,
        batch_id=soft.batch_id,
        manifest_sha256=soft.manifest_sha256,
        id_list_sha256=soft.id_list_sha256,
        live_sha=None,
        mode="apply",
        sj_count=1,
        lease_fp=sj_audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    try:
        out = replay_one_sj(
            tdb,
            manifest=soft,
            accurate_sj_id=entry.accurate_sj_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            run_id=str(run["run_id"]),
            lease_fp=sj_audit.lease_fingerprint(str(handle["owner_token"])),
            maintenance_owner_token=str(handle["owner_token"]),
            maintenance_job_tokens=dict(handle["job_tokens"]),
        )
    finally:
        release_po_repair_maintenance(
            tdb,
            owner_token=str(handle["owner_token"]),
            job_tokens=dict(handle.get("job_tokens") or {}),
        )

    assert out["ok"] is True
    assert out["outcome"] == "created"
    note = deliveries.get_delivery_note(tdb, entry.accurate_sj_id)
    assert note is not None
    assert deliveries.note_is_multi_po(note)
    assert tuple(sorted(deliveries.note_parent_po_ids(note), key=int)) == entry.parent_po_ids
    review = get_sj_sync_review(tdb, entry.accurate_sj_id)
    assert review is not None
    assert review.get("status") == STATUS_RESOLVED
    # loading task ensured, qty not confirmed
    from app.services.delivery_service import loading_sj_task_key

    task = tasks.get_task(tdb, loading_sj_task_key(entry.accurate_sj_id))
    assert task is not None
    assert int(task.get("qty_confirmed") or 0) == 0
    assert tdb["loading_events"].count_documents({}) == before_events
    assert tdb["delivery_events"].count_documents({}) == before_del

    # idempotent second apply
    handle = acquire_po_repair_maintenance(tdb, ttl_seconds=120, actor="test_p10_sj2")
    run = sj_audit.start_run(
        tdb,
        batch_id=soft.batch_id,
        manifest_sha256=soft.manifest_sha256,
        id_list_sha256=soft.id_list_sha256,
        live_sha=None,
        mode="apply",
        sj_count=1,
        lease_fp=sj_audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    try:
        again = replay_one_sj(
            tdb,
            manifest=soft,
            accurate_sj_id=entry.accurate_sj_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            run_id=str(run["run_id"]),
            lease_fp=sj_audit.lease_fingerprint(str(handle["owner_token"])),
            maintenance_owner_token=str(handle["owner_token"]),
            maintenance_job_tokens=dict(handle["job_tokens"]),
        )
    finally:
        release_po_repair_maintenance(
            tdb,
            owner_token=str(handle["owner_token"]),
            job_tokens=dict(handle.get("job_tokens") or {}),
        )
    assert again["outcome"] == "already_present"
    assert (
        tdb[deliveries.DELIVERY_NOTES_COLLECTION].count_documents(
            {"accurate_sj_id": entry.accurate_sj_id}
        )
        == 1
    )


def test_sj_dry_run_zero_writes(tdb: Database) -> None:
    m = load_sj_manifest()
    entry = next(e for e in m.entries if e.accurate_sj_id == "120806")
    for pid in entry.parent_po_ids:
        _seed_po(tdb, pid)
    payload = _sj(entry.accurate_sj_id, so_ids=list(entry.parent_po_ids))
    from dataclasses import replace

    soft = replace(
        m,
        entries=tuple(
            replace(e, detail_attr_sha256="") if e.accurate_sj_id == entry.accurate_sj_id else e
            for e in m.entries
        ),
    )
    before = tdb[deliveries.DELIVERY_NOTES_COLLECTION].count_documents({})
    out = replay_one_sj(
        tdb,
        manifest=soft,
        accurate_sj_id=entry.accurate_sj_id,
        payload=payload,
        mode="dry_run",
        mongo_guard="test",
    )
    assert out["ok"] is True
    assert out["writes"] == 0
    assert tdb[deliveries.DELIVERY_NOTES_COLLECTION].count_documents({}) == before
    assert tdb[sj_audit.RUNS].count_documents({"batch_id": SJ_BATCH_ID}) == 0


def test_sj_apply_guard_requires_execute() -> None:
    m = load_sj_manifest()
    bad = SjApproval(
        mode="apply",
        expect_manifest_sha256=m.manifest_sha256,
        expect_id_list_sha256=m.id_list_sha256,
        expect_sj_count=m.sj_count,
        expect_batch_id=m.batch_id,
        execute=False,
    )
    with pytest.raises(ControlledMultiPoSjReplayError) as ei:
        assert_sj_approval(bad, manifest=m, app_env="test")
    assert ei.value.code == "apply_execute_required"


def test_full_chain_parent_then_sj(tdb: Database) -> None:
    """Isolated: recover one sealed P8C parent → ingest dependent multi-PO SJ."""
    po_m = load_sealed_manifest(default_manifest_path(P8C_BATCH_ID))
    # Use parents for 119155: 92201, 93319 — both in P8C missing list
    needed = ["92201", "93319"]
    details = {}
    for aid in needed:
        entry = po_m.entry(aid)
        assert entry is not None
        details[aid] = _hist_so(
            aid,
            trans_date=entry.trans_date.isoformat(),
            branch_id=entry.branch_id,
            customer_id=entry.customer_id,
        )

    # APPLY parents one-by-one under fence
    for aid in needed:
        entry = po_m.entry(aid)
        assert entry is not None
        with _po_fence(tdb, po_m) as fence:
            out = recover_one_po(
                tdb,
                manifest=po_m,
                accurate_id=aid,
                payload=details[aid],
                mode="apply",
                mongo_guard="test",
                **fence,
            )
        assert out["outcome"] in {"created", "already_present"}
        assert get_by_accurate_id(tdb, aid) is not None

    sj_m = load_sj_manifest()
    sj_entry = next(e for e in sj_m.entries if e.accurate_sj_id == "119155")
    from dataclasses import replace

    soft = replace(
        sj_m,
        entries=tuple(
            replace(e, detail_attr_sha256="")
            if e.accurate_sj_id == sj_entry.accurate_sj_id
            else e
            for e in sj_m.entries
        ),
    )
    payload = _sj(sj_entry.accurate_sj_id, so_ids=list(sj_entry.parent_po_ids))
    upsert_sj_sync_review(
        tdb,
        accurate_sj_id=sj_entry.accurate_sj_id,
        reason=REASON_MULTI_SO,
        source_so_ids=list(sj_entry.parent_po_ids),
        retryable=True,
    )
    handle = acquire_po_repair_maintenance(tdb, ttl_seconds=120, actor="test_p10_chain")
    run = sj_audit.start_run(
        tdb,
        batch_id=soft.batch_id,
        manifest_sha256=soft.manifest_sha256,
        id_list_sha256=soft.id_list_sha256,
        live_sha=None,
        mode="apply",
        sj_count=1,
        lease_fp=sj_audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    try:
        sj_out = replay_one_sj(
            tdb,
            manifest=soft,
            accurate_sj_id=sj_entry.accurate_sj_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            run_id=str(run["run_id"]),
            lease_fp=sj_audit.lease_fingerprint(str(handle["owner_token"])),
            maintenance_owner_token=str(handle["owner_token"]),
            maintenance_job_tokens=dict(handle["job_tokens"]),
        )
    finally:
        release_po_repair_maintenance(
            tdb,
            owner_token=str(handle["owner_token"]),
            job_tokens=dict(handle.get("job_tokens") or {}),
        )
    assert sj_out["outcome"] == "created"
    note = deliveries.get_delivery_note(tdb, "119155")
    assert note is not None
    assert deliveries.note_is_multi_po(note)
    assert get_sj_sync_review(tdb, "119155")["status"] == STATUS_RESOLVED
