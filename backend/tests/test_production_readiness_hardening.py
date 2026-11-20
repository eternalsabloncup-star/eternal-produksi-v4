"""Regresi Production Readiness: no seed/createIndex di request + fail-closed Master/index."""

from __future__ import annotations

import copy
import os

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import evidence_repository as evidence
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import sample_repository as samples
from app.repositories import schedule_repository as schedules
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import production_service as prod
from app.services import scheduling_service
from app.services.index_preflight import (
    MissingRequiredIndexError,
    clear_index_verification_cache,
    mongo_target_cache_key,
)
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_A = "106318"

HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}
HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["access.all_branches"],
}
SCREENER = {
    "email_normalized": "screener@eternal.local",
    "role": "user",
    "operational_role": "screener",
    "effective_module_permissions": ["access.all_branches"],
}
OPERATOR = {
    "email_normalized": "op1@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": ["access.all_branches"],
}

PNG = b"\x89PNG sample"


def _seed_po(db: Database) -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    upsert_sales_order(db, normalize_sales_order(payload))
    mark_po_accepted(db)
    doc = get_by_accurate_id(db, PO_ID)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji hardening"
        )
    return doc


def _schedule_one(db: Database, po_doc: dict, key: str) -> str:
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": OPERATOR["email_normalized"], "qty": 50}],
        idempotency_key=key,
    )
    return res["allocations"][0]["allocation_id"]


def _prep_screen(db: Database, allocation_id: str, suffix: str) -> None:
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_A,
        qty=50,
        idempotency_key=f"prep-{suffix}",
    )
    prod.confirm_screen(
        db,
        SCREENER,
        allocation_id=allocation_id,
        qty=1,
        idempotency_key=f"screen-{suffix}",
    )


def test_production_request_never_seeds_master(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    po = _seed_po(db)
    alloc = _schedule_one(db, po, "hard-prod-alloc")
    _prep_screen(db, alloc, "hard-prod")

    def _boom(*_a, **_k):
        raise AssertionError("seed_master_defaults tidak boleh dipanggil dari production")

    monkeypatch.setattr(master, "seed_master_defaults", _boom)
    prod.submit_sample(
        db,
        OPERATOR,
        allocation_id=alloc,
        filename="s.png",
        content_type="image/png",
        content=PNG,
    )


def test_scheduling_request_never_seeds_master(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    po = _seed_po(db)

    def _boom(*_a, **_k):
        raise AssertionError("seed_master_defaults tidak boleh dipanggil dari scheduling")

    monkeypatch.setattr(master, "seed_master_defaults", _boom)
    _schedule_one(db, po, "hard-sched-alloc")


def test_operational_api_never_seeds_master(
    app_client, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.bootstrap import create_admin_user

    create_admin_user(
        db,
        email="sa-hard@eternal.local",
        display_name="SA",
        password="SuperAdminPass1!",
        allow_if_admin_exists=True,
    )

    def _boom(*_a, **_k):
        raise AssertionError(
            "seed_master_defaults tidak boleh dipanggil dari operational API"
        )

    monkeypatch.setattr(master, "seed_master_defaults", _boom)

    login = app_client.post(
        "/api/auth/login",
        json={"email": "sa-hard@eternal.local", "password": "SuperAdminPass1!"},
    )
    assert login.status_code == 200
    r = app_client.get("/api/master/task-types")
    assert r.status_code == 200
    assert len(r.json()["items"]) > 0


def test_missing_master_fail_closed_no_fallback(db: Database) -> None:
    db["master_task_types"].delete_many({"task_type": "cetak"})
    clear_index_verification_cache(db=db)
    with pytest.raises(master.MissingMasterConfigError):
        master.require_task_type_assigned_role(db, "cetak")


def test_missing_assigned_role_fail_closed(db: Database) -> None:
    db["master_task_types"].update_one(
        {"task_type": "cetak"}, {"$unset": {"assigned_role": ""}}
    )
    with pytest.raises(master.MissingMasterConfigError):
        master.require_task_type_assigned_role(db, "cetak")


def test_missing_index_fail_closed_schedule(db: Database) -> None:
    clear_index_verification_cache(db=db)
    db["production_allocations"].drop_index("uniq_allocation_id")
    clear_index_verification_cache(db=db)
    with pytest.raises(MissingRequiredIndexError) as exc:
        schedules.require_schedule_indexes(db)
    assert "uniq_allocation_id" in exc.value.missing
    schedules.ensure_schedule_indexes(db)


def test_wrong_index_spec_fail_closed_sample(db: Database) -> None:
    clear_index_verification_cache(db=db)
    db["operator_samples"].drop_index("uniq_sample_allocation_version")
    db["operator_samples"].create_index(
        [("allocation_id", 1), ("version", -1)],
        name="uniq_sample_allocation_version",
        unique=False,
    )
    clear_index_verification_cache(db=db)
    with pytest.raises(MissingRequiredIndexError) as exc:
        samples.require_sample_indexes(db)
    assert any(c["name"] == "uniq_sample_allocation_version" for c in exc.value.conflict)
    db["operator_samples"].drop_index("uniq_sample_allocation_version")
    samples.ensure_sample_indexes(db)


def test_request_path_never_create_index_on_gate(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    coll_cls = type(db["po_gates"])
    original = coll_cls.create_index

    def _spy(self, *a, **k):  # noqa: ANN001
        calls.append(str(k.get("name") or a))
        return original(self, *a, **k)

    monkeypatch.setattr(coll_cls, "create_index", _spy, raising=True)
    clear_index_verification_cache(db=db)
    gates.get_gates(db, "99199")
    assert calls == []


def test_index_cache_isolated_across_clients(mongo_client: MongoClient) -> None:
    import app.services.index_preflight as preflight
    from pymongo import MongoClient as PMC

    clear_index_verification_cache()
    name = "eternal_v4_test_cache_iso_master"
    db_a = mongo_client[name]
    master.ensure_master_indexes(db_a)
    key_a = mongo_target_cache_key(db_a)
    assert key_a in preflight._VERIFIED_SPECS

    uri = os.environ["MONGODB_URI"]
    client_b = PMC(uri, serverSelectionTimeoutMS=3000)
    try:
        db_b = client_b[name]
        key_b = mongo_target_cache_key(db_b)
        assert key_a != key_b
        assert key_b not in preflight._VERIFIED_SPECS
        master.require_master_indexes(db_b)
        assert key_b in preflight._VERIFIED_SPECS
    finally:
        client_b.close()
        clear_index_verification_cache()
        mongo_client.drop_database(name)


def test_confirm_sample_fails_when_approval_master_missing(db: Database) -> None:
    """Draft tetap boleh tersimpan; confirm gagal tanpa master approval-sampel."""
    po = _seed_po(db)
    alloc = _schedule_one(db, po, "hard-orphan-alloc")
    _prep_screen(db, alloc, "hard-orphan")

    prod.upload_sample_draft(
        db,
        OPERATOR,
        allocation_id=alloc,
        filename="orphan.png",
        content_type="image/png",
        content=PNG,
    )
    db["master_task_types"].delete_many({"task_type": "approval-sampel"})
    with pytest.raises(master.MissingMasterConfigError):
        prod.confirm_sample_submission(db, OPERATOR, allocation_id=alloc)
    sample = samples.latest_sample(db, alloc)
    assert sample is not None
    assert sample["status"] == samples.STATUS_DRAFT


def test_sample_approval_no_partial_when_print_master_missing(db: Database) -> None:
    po = _seed_po(db)
    alloc = _schedule_one(db, po, "hard-partial-alloc")
    _prep_screen(db, alloc, "hard-partial")
    from tests.sample_flow_helpers import draft_and_confirm

    submitted = draft_and_confirm(db, OPERATOR, alloc)
    db["master_task_types"].delete_many({"task_type": "cetak"})
    with pytest.raises(master.MissingMasterConfigError):
        prod.decide_sample(
            db,
            HEAD,
            allocation_id=alloc,
            version=int(submitted["version"]),
            approved=True,
        )
    sample = samples.latest_sample(db, alloc)
    assert sample is not None
    assert sample["status"] == samples.STATUS_PENDING


def test_confirm_print_idempotent(db: Database) -> None:
    po = _seed_po(db)
    alloc = _schedule_one(db, po, "hard-print-alloc")
    _prep_screen(db, alloc, "hard-print")
    from tests.sample_flow_helpers import approve_sample

    approve_sample(db, OPERATOR, HEAD, alloc)
    first = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=10, idempotency_key="print-hard-a"
    )
    second = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=10, idempotency_key="print-hard-a"
    )
    assert second.get("duplicate") is True
    assert first["task"]["qty_confirmed"] == second["task"]["qty_confirmed"]


def test_qc_packing_consistent_after_print(db: Database) -> None:
    po = _seed_po(db)
    alloc = _schedule_one(db, po, "hard-qc-alloc")
    _prep_screen(db, alloc, "hard-qc")
    from tests.sample_flow_helpers import approve_sample

    approve_sample(db, OPERATOR, HEAD, alloc)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=20, idempotency_key="print-hard-qc"
    )
    qc = prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_A,
        qty=20,
        idempotency_key="qc-hard",
    )
    assert qc["qty_ready_to_ship"] == 20


def test_scheduling_requires_po_acceptance(db: Database) -> None:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    upsert_sales_order(db, normalize_sales_order(payload))
    doc = get_by_accurate_id(db, PO_ID)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="no-accept"
        )
    with pytest.raises(scheduling_service.ScheduleGateError):
        scheduling_service.create_allocations(
            db,
            HEAD,
            po_doc=doc,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": OPERATOR["email_normalized"], "qty": 5}],
            idempotency_key="no-accept-hard",
        )


def test_sj_sync_default_still_off() -> None:
    from app.config import Settings

    s = Settings(mongodb_uri="mongodb://127.0.0.1:27017", mongodb_db="x")
    assert s.sj_sync_enabled is False
