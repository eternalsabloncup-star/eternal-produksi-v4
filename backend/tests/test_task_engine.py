"""Task engine: idempotency, invarian Qty, concurrency, RBAC Master, audit."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from pymongo.database import Database

from app.domain.operational import TaskRef
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.services import task_engine

LINE_REF = TaskRef(
    accurate_po_id="95106",
    po_no="PO/18/11/2026/000010",
    accurate_line_id="106318",
    item_code="100359",
    process_kind="Cetak",
)


def _helper_user(email: str = "helper@eternal.local") -> dict:
    return {"email_normalized": email, "role": "user", "operational_role": "helper"}


def _seed_prep_task(db: Database, qty_target: float = 1000) -> dict:
    master.seed_master_defaults(db)
    return tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role="helper",
        ref=LINE_REF,
        qty_target=qty_target,
        label="Persiapan Cup",
    )


def test_master_seed_is_idempotent(db: Database) -> None:
    # Fixture sudah bootstrap Master — kosongkan dulu agar insert pertama terukur.
    db["master_task_types"].delete_many({})
    db["master_role_actions"].delete_many({})
    first = master.seed_master_defaults(db)
    second = master.seed_master_defaults(db)
    assert first["task_types"] == 12
    assert second["task_types"] == 0
    assert len(master.list_task_types(db)) == 12


def test_task_creation_is_idempotent_on_resync(db: Database) -> None:
    _seed_prep_task(db)
    _seed_prep_task(db)
    _seed_prep_task(db)
    rows = tasks.list_tasks(db, task_type="persiapan-cup")
    assert len(rows) == 1
    assert rows[0]["qty_confirmed"] == 0
    assert rows[0]["status"] == tasks.TASK_STATUS_OPEN


def test_partial_confirmations_keep_history(db: Database) -> None:
    task = _seed_prep_task(db)
    user = _helper_user()
    task_engine.confirm_task(
        db, user, task_key=task["task_key"], qty=300, idempotency_key="prep-conf-001"
    )
    task_engine.confirm_task(
        db, user, task_key=task["task_key"], qty=200, idempotency_key="prep-conf-002"
    )
    rows = tasks.list_confirmations(db, task["task_key"])
    assert [r["qty"] for r in rows] == [300, 200]
    stored = tasks.get_task(db, task["task_key"])
    assert stored["qty_confirmed"] == 500
    assert stored["status"] == tasks.TASK_STATUS_PARTIAL


def test_same_idempotency_key_does_not_double_qty(db: Database) -> None:
    task = _seed_prep_task(db)
    user = _helper_user()
    first = task_engine.confirm_task(
        db, user, task_key=task["task_key"], qty=400, idempotency_key="retry-key-1"
    )
    second = task_engine.confirm_task(
        db, user, task_key=task["task_key"], qty=400, idempotency_key="retry-key-1"
    )
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert tasks.get_task(db, task["task_key"])["qty_confirmed"] == 400
    assert len(tasks.list_confirmations(db, task["task_key"])) == 1


def test_confirmation_above_target_is_rejected_without_partial_write(
    db: Database,
) -> None:
    task = _seed_prep_task(db, qty_target=500)
    user = _helper_user()
    task_engine.confirm_task(
        db, user, task_key=task["task_key"], qty=400, idempotency_key="ok-1"
    )
    with pytest.raises(tasks.TaskQuantityError):
        task_engine.confirm_task(
            db, user, task_key=task["task_key"], qty=200, idempotency_key="over-1"
        )
    stored = tasks.get_task(db, task["task_key"])
    assert stored["qty_confirmed"] == 400
    # Ledger tidak menyimpan entri yang ditolak.
    assert [r["idempotency_key"] for r in tasks.list_confirmations(db, task["task_key"])] == [
        "ok-1"
    ]


def test_concurrent_confirmations_never_exceed_target(db: Database) -> None:
    task = _seed_prep_task(db, qty_target=1000)
    user = _helper_user()

    def worker(i: int) -> str:
        try:
            task_engine.confirm_task(
                db,
                user,
                task_key=task["task_key"],
                qty=300,
                idempotency_key=f"concurrent-{i}",
            )
            return "ok"
        except tasks.TaskQuantityError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(worker, range(6)))

    stored = tasks.get_task(db, task["task_key"])
    assert stored["qty_confirmed"] <= 1000
    assert stored["qty_confirmed"] == 300 * results.count("ok")
    assert tasks.total_confirmed_qty(db, task["task_key"]) == stored["qty_confirmed"]
    assert results.count("ok") == 3


def test_rbac_denies_role_without_master_grant(db: Database) -> None:
    task = _seed_prep_task(db)
    driver = {
        "email_normalized": "driver@eternal.local",
        "role": "user",
        "operational_role": "driver",
    }
    with pytest.raises(task_engine.TaskPermissionError):
        task_engine.confirm_task(
            db, driver, task_key=task["task_key"], qty=10, idempotency_key="driver-x1"
        )
    assert tasks.get_task(db, task["task_key"])["qty_confirmed"] == 0


def test_master_can_grant_action_to_another_role(db: Database) -> None:
    """Helper boleh diberi izin Konfirmasi Loading lewat Master (bukan hardcode)."""
    master.seed_master_defaults(db)
    owner = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
    helper = _helper_user()

    sj_task = tasks.upsert_task(
        db,
        task_type="qty-loading-sj-berbeda",
        scope="delivery_note",
        assigned_role="production-head",
        ref=TaskRef(accurate_delivery_note_id="SJ-900", sj_no="SJ/0001"),
        qty_target=None,
        coordination=True,
    )
    with pytest.raises(task_engine.TaskPermissionError):
        task_engine.confirm_task(
            db, helper, task_key=sj_task["task_key"], qty=1, idempotency_key="sj-helper-1"
        )

    task_engine.set_role_actions(
        db,
        owner,
        role="helper",
        task_type="qty-loading-sj-berbeda",
        actions=["view", "confirm"],
    )
    result = task_engine.confirm_task(
        db, helper, task_key=sj_task["task_key"], qty=1, idempotency_key="sj-helper-2"
    )
    assert result["duplicate"] is False
    assert result["task"]["status"] == tasks.TASK_STATUS_DONE


def test_master_role_reassignment_is_audited(db: Database) -> None:
    master.seed_master_defaults(db)
    owner = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
    doc = task_engine.set_task_type_role(
        db, owner, task_type="qc-packing", role="screener"
    )
    assert doc["assigned_role"] == "screener"
    assert doc["is_default_mapping"] is False
    events = list(db["audit_logs"].find({"action": "master.task_type.assign_role"}))
    assert len(events) == 1
    assert events[0]["detail"]["task_type"] == "qc-packing"


def test_task_confirmation_writes_audit_trail(db: Database) -> None:
    task = _seed_prep_task(db)
    task_engine.confirm_task(
        db, _helper_user(), task_key=task["task_key"], qty=100, idempotency_key="audit-1"
    )
    events = list(db["audit_logs"].find({"action": "task.confirm.persiapan-cup"}))
    assert len(events) == 1
    assert events[0]["detail"]["task_key"] == task["task_key"]
    assert events[0]["detail"]["ref"]["accurate_line_id"] == "106318"


def test_super_admin_bypasses_operational_grant_but_is_audited(db: Database) -> None:
    task = _seed_prep_task(db)
    owner = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
    task_engine.confirm_task(
        db, owner, task_key=task["task_key"], qty=50, idempotency_key="sa-1"
    )
    assert tasks.get_task(db, task["task_key"])["qty_confirmed"] == 50
    assert db["audit_logs"].count_documents({"action": "task.confirm.persiapan-cup"}) == 1


def test_unknown_role_or_action_rejected(db: Database) -> None:
    master.seed_master_defaults(db)
    owner = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
    with pytest.raises(ValueError):
        task_engine.set_role_actions(
            db, owner, role="akuntan", task_type="cetak", actions=["confirm"]
        )
    with pytest.raises(ValueError):
        task_engine.set_role_actions(
            db, owner, role="operator", task_type="cetak", actions=["hapus-total"]
        )
