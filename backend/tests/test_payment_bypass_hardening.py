"""Bypass Payment — atomicity, idempotency, branch, derive tasks (isolated Mongo)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import payment_service, po_tasks
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
BRANCH = "HQ"
OWNER = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
OPS_FULL = {
    "email_normalized": "opsfull@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["operations.full", "access.all_branches"],
}
OPERATOR = {
    "email_normalized": "operator@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": ["access.all_branches"],
}
BRANCH_A = {
    "email_normalized": "br-a@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "branch_ids": [BRANCH],
    # Izin bypass tanpa ops.full / all_branches — tetap diikat cabang.
    "effective_module_permissions": ["payment.bypass"],
}
BRANCH_OTHER = {
    "email_normalized": "br-other@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "branch_ids": ["OTHER"],
    "effective_module_permissions": ["payment.bypass"],
}


def _sync_po(db: Database, payload=None) -> dict:
    """Seed PO langsung (bypass ingest branch/date floor) + cabang HQ + accepted."""
    upsert_sales_order(db, normalize_sales_order(payload or SAMPLE_SALES_ORDER_STRUCTURE))
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"branch_id": BRANCH, "branch_code": BRANCH}},
    )
    mark_po_accepted(db)
    master.seed_master_defaults(db)
    doc = get_by_accurate_id(db, PO_ID)
    assert doc is not None
    return doc


def _open_design(db: Database) -> None:
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="design",
        state=gates.GATE_OPEN,
        reason="uji design open",
    )


def test_bypass_success_sets_detail_bypass_not_receipt(db: Database) -> None:
    _sync_po(db)
    doc = payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="Disetujui Owner tertulis"
    )
    payment = doc["payment"]
    assert payment["state"] == gates.GATE_OPEN
    assert (payment.get("detail") or {}).get("bypass") is True
    assert "Bypass Payment:" in str(payment.get("reason") or "")
    assert db["payment_receipts"].count_documents({"accurate_po_id": PO_ID}) == 0
    assert db["audit_logs"].count_documents({"action": "payment.gate.bypass"}) == 1
    assert (
        db["po_gate_history"].count_documents(
            {"accurate_po_id": PO_ID, "gate": "payment", "detail.bypass": True}
        )
        == 1
    )
    assert doc.get("duplicate") is False


def test_bypass_reason_validation(db: Database) -> None:
    _sync_po(db)
    with pytest.raises(ValueError):
        payment_service.bypass_payment_gate(
            db, OWNER, accurate_po_id=PO_ID, reason="   "
        )
    with pytest.raises(ValueError):
        payment_service.bypass_payment_gate(
            db, OWNER, accurate_po_id=PO_ID, reason="ab"
        )
    with pytest.raises(ValueError):
        payment_service.bypass_payment_gate(
            db, OWNER, accurate_po_id=PO_ID, reason="x" * 501
        )


def test_bypass_permission_deny(db: Database) -> None:
    _sync_po(db)
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, OPERATOR, accurate_po_id=PO_ID, reason="tidak boleh"
        )


def test_bypass_unaccepted_and_missing_po(db: Database) -> None:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"branch_id": BRANCH, "branch_code": BRANCH}},
    )
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, OWNER, accurate_po_id=PO_ID, reason="belum accept"
        )
    with pytest.raises(LookupError):
        payment_service.bypass_payment_gate(
            db, OWNER, accurate_po_id="missing-po", reason="tidak ada"
        )


def test_bypass_idempotent_no_duplicate_audit(db: Database) -> None:
    _sync_po(db)
    first = payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="pertama"
    )
    assert first.get("duplicate") is False
    second = payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="kedua beda"
    )
    assert second.get("duplicate") is True
    assert (second["payment"].get("detail") or {}).get("bypass") is True
    assert "pertama" in str(second["payment"].get("reason") or "")
    assert db["audit_logs"].count_documents({"action": "payment.gate.bypass"}) == 1
    assert (
        db["po_gate_history"].count_documents(
            {"accurate_po_id": PO_ID, "detail.bypass": True}
        )
        == 1
    )


def test_bypass_conflict_when_open_normal(db: Database) -> None:
    _sync_po(db)
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="payment",
        state=gates.GATE_OPEN,
        reason="Penerimaan terverifikasi",
        detail={"auto": True, "receipt_ids": ["RC-1"]},
    )
    with pytest.raises(payment_service.PaymentGateConflictError):
        payment_service.bypass_payment_gate(
            db, OWNER, accurate_po_id=PO_ID, reason="jangan overwrite"
        )
    payment = gates.get_gates(db, PO_ID)["payment"]
    assert payment["state"] == gates.GATE_OPEN
    assert (payment.get("detail") or {}).get("bypass") is not True
    assert db["audit_logs"].count_documents({"action": "payment.gate.bypass"}) == 0


def test_bypass_concurrent_only_one_wins(db: Database) -> None:
    _sync_po(db)
    results: list[dict] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def _worker() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                payment_service.bypass_payment_gate(
                    db,
                    OWNER,
                    accurate_po_id=PO_ID,
                    reason="concurrent bypass ok",
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_worker), pool.submit(_worker)]
        for f in futs:
            f.result(timeout=30)

    assert not errors
    assert len(results) == 2
    winners = [r for r in results if not r.get("duplicate")]
    dupes = [r for r in results if r.get("duplicate")]
    assert len(winners) == 1
    assert len(dupes) == 1
    assert db["audit_logs"].count_documents({"action": "payment.gate.bypass"}) == 1
    assert (
        db["po_gate_history"].count_documents(
            {"accurate_po_id": PO_ID, "detail.bypass": True}
        )
        == 1
    )


def test_bypass_transaction_rolls_back_when_audit_fails(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sync_po(db)

    def _boom(*_a, **_k):
        raise RuntimeError("audit gagal uji")

    monkeypatch.setattr(
        "app.services.payment_service.append_audit_event", _boom
    )
    with pytest.raises(RuntimeError, match="audit gagal"):
        payment_service.bypass_payment_gate(
            db, OWNER, accurate_po_id=PO_ID, reason="harus rollback"
        )
    payment = gates.get_gates(db, PO_ID)["payment"]
    assert payment["state"] != gates.GATE_OPEN or not (
        payment.get("detail") or {}
    ).get("bypass")
    assert db["audit_logs"].count_documents({"action": "payment.gate.bypass"}) == 0
    assert (
        db["po_gate_history"].count_documents(
            {"accurate_po_id": PO_ID, "detail.bypass": True}
        )
        == 0
    )


def test_bypass_with_design_open_derives_scheduling_tasks(db: Database) -> None:
    po = _sync_po(db)
    po_tasks.derive_tasks_for_po(db, po)
    _open_design(db)
    before = len(tasks.list_tasks(db, task_type="penjadwalan", accurate_po_id=PO_ID))
    assert before == 0
    doc = payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="buka penjadwalan"
    )
    assert doc.get("workflow") is not None
    assert doc["workflow"].get("scheduling_unlocked") is True
    after = tasks.list_tasks(db, task_type="penjadwalan", accurate_po_id=PO_ID)
    assert len(after) >= 1
    # Repeated bypass tidak menambah task.
    payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="ulang"
    )
    assert (
        len(tasks.list_tasks(db, task_type="penjadwalan", accurate_po_id=PO_ID))
        == len(after)
    )


def test_bypass_with_design_closed_skips_scheduling_tasks(db: Database) -> None:
    po = _sync_po(db)
    po_tasks.derive_tasks_for_po(db, po)
    doc = payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="design masih closed"
    )
    assert gates.get_gates(db, PO_ID)["payment"]["state"] == gates.GATE_OPEN
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_CLOSED
    assert doc.get("workflow") is None
    assert tasks.list_tasks(db, task_type="penjadwalan", accurate_po_id=PO_ID) == []


def test_receipt_reconciliation_does_not_clear_bypass(db: Database) -> None:
    po = _sync_po(db)
    payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="tetap bypass"
    )
    payment_service.ingest_receipts(
        db,
        OWNER,
        accurate_po_id=PO_ID,
        po_no=str(po["po_no"]),
        receipts=[{"id": "RC-AFTER", "invoice_no": "", "amount": 10}],
    )
    payment = gates.get_gates(db, PO_ID)["payment"]
    assert payment["state"] == gates.GATE_OPEN
    assert (payment.get("detail") or {}).get("bypass") is True


def test_api_bypass_branch_deny_and_success(
    app_client: TestClient, db: Database
) -> None:
    from app.auth.passwords import hash_password
    from app.models.user import normalize_email

    _sync_po(db)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"branch_id": "HQ", "header.branch_id": "HQ"}},
    )

    def _seed(email: str, user_doc: dict) -> None:
        db["users"].insert_one(
            {
                "email_normalized": normalize_email(email),
                "display_name": email,
                "password_hash": hash_password("password-kuat-123"),
                "role": user_doc.get("role") or "user",
                "operational_role": user_doc.get("operational_role"),
                "branch_ids": user_doc.get("branch_ids") or [],
                "effective_module_permissions": user_doc.get(
                    "effective_module_permissions"
                )
                or [],
                "is_active": True,
            }
        )

    _seed("br-other@eternal.local", BRANCH_OTHER)
    _seed("br-a@eternal.local", BRANCH_A)

    def _login(email: str) -> str:
        res = app_client.post(
            "/api/auth/login",
            json={"email": email, "password": "password-kuat-123"},
        )
        assert res.status_code == 200, res.text
        return res.json()["csrf_token"]

    csrf_deny = _login("br-other@eternal.local")
    denied = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "cabang salah"},
        headers={"X-CSRF-Token": csrf_deny},
    )
    assert denied.status_code == 403

    csrf_ok = _login("br-a@eternal.local")
    ok = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "cabang benar bypass"},
        headers={"X-CSRF-Token": csrf_ok},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["payment"]["state"] == "open"
    assert (body["payment"].get("detail") or {}).get("bypass") is True

    again = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "ulang dari panel"},
        headers={"X-CSRF-Token": csrf_ok},
    )
    assert again.status_code == 200
    assert again.json().get("duplicate") is True

    db["po_gates"].update_one(
        {"accurate_po_id": PO_ID},
        {
            "$set": {
                "payment": {
                    "state": "open",
                    "reason": "normal",
                    "detail": {"auto": True},
                }
            }
        },
    )
    conflict = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "coba overwrite normal"},
        headers={"X-CSRF-Token": csrf_ok},
    )
    assert conflict.status_code == 409


def test_api_reason_too_short(app_client: TestClient, db: Database) -> None:
    from app.bootstrap import create_admin_user

    _sync_po(db)
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password="password-kuat-123",
        allow_if_admin_exists=True,
    )
    login = app_client.post(
        "/api/auth/login",
        json={"email": "owner@eternal.local", "password": "password-kuat-123"},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    res = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "ab"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 422
