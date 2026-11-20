"""Gate Terima PO — permission, idempotency, workflow, audit, sync preserve."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import po_acceptance, po_tasks, scheduling_service
from app.services.po_sync import sync_sales_order_payload

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_PRINT = "106318"


def _seed_users(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    for email, role in (
        ("kp@eternal.local", "production-head"),
        ("helper@eternal.local", "helper"),
        ("designer@eternal.local", "designer"),
    ):
        db["users"].insert_one(
            {
                "email_normalized": normalize_email(email),
                "display_name": email,
                "password_hash": hash_password(PASSWORD),
                "role": "user",
                "operational_role": role,
                "is_active": True,
                "effective_module_permissions": ["access.all_branches"]
            }
        )


def _seed_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    return get_by_accurate_id(db, PO_ID)


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def test_inbox_po_without_acceptance_has_no_workflow_tasks(db: Database) -> None:
    po = _seed_po(db)
    assert po_acceptance.is_po_accepted(po) is False
    assert tasks.list_tasks(db, accurate_po_id=PO_ID) == []


def test_derive_tasks_blocked_until_accepted(db: Database) -> None:
    po = _seed_po(db)
    try:
        po_tasks.derive_tasks_for_po(db, po, require_accepted=True)
        raise AssertionError("expected PoAcceptanceError")
    except po_acceptance.PoAcceptanceError:
        pass
    assert tasks.list_tasks(db, accurate_po_id=PO_ID) == []


def test_production_head_accept_activates_workflow(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_po(db)
    csrf = _login(app_client, "kp@eternal.local")
    res = app_client.post(
        f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["duplicate"] is False
    assert body["acceptance"]["status"] == "accepted"
    assert body["acceptance"]["accepted_by"] == "kp@eternal.local"
    assert body["workflow"]["created_task_keys"]
    po = get_by_accurate_id(db, PO_ID)
    assert po_acceptance.is_po_accepted(po)
    assert tasks.list_tasks(db, accurate_po_id=PO_ID)


def test_accept_forbidden_without_permission(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_po(db)
    csrf = _login(app_client, "helper@eternal.local")
    res = app_client.post(
        f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 403
    assert po_acceptance.is_po_accepted(get_by_accurate_id(db, PO_ID)) is False
    assert tasks.list_tasks(db, accurate_po_id=PO_ID) == []


def test_accept_idempotent_second_click(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_po(db)
    csrf = _login(app_client, "kp@eternal.local")
    first = app_client.post(
        f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf}
    )
    assert first.status_code == 200
    keys1 = list(first.json()["workflow"]["created_task_keys"])
    second = app_client.post(
        f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf}
    )
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    keys2 = list(second.json()["workflow"]["created_task_keys"])
    # Tidak menggandakan task_key unik.
    assert set(keys1) == set(keys2) or set(keys2).issubset(
        {t["task_key"] for t in tasks.list_tasks(db, accurate_po_id=PO_ID)}
    )
    assert (
        len({t["task_key"] for t in tasks.list_tasks(db, accurate_po_id=PO_ID)})
        == len(tasks.list_tasks(db, accurate_po_id=PO_ID))
    )


def test_concurrent_accept_no_duplicate_workflow(db: Database) -> None:
    _seed_users(db)
    _seed_po(db)
    user = db["users"].find_one({"email_normalized": "kp@eternal.local"})
    assert user is not None

    def _once() -> dict:
        return po_acceptance.accept_po(db, user, accurate_po_id=PO_ID)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _once(), range(8)))
    winners = [r for r in results if r["duplicate"] is False]
    assert len(winners) == 1
    task_keys = [t["task_key"] for t in tasks.list_tasks(db, accurate_po_id=PO_ID)]
    assert len(task_keys) == len(set(task_keys))


def test_accept_a_not_b(db: Database) -> None:
    import copy

    _seed_users(db)
    _seed_po(db)
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = 99901
    payload["number"] = "PO/E2E/B"
    upsert_sales_order(db, normalize_sales_order(payload))
    kp = db["users"].find_one({"email_normalized": "kp@eternal.local"})
    po_acceptance.accept_po(db, kp, accurate_po_id=PO_ID)
    assert po_acceptance.is_po_accepted(get_by_accurate_id(db, PO_ID))
    assert po_acceptance.is_po_accepted(get_by_accurate_id(db, "99901")) is False
    assert tasks.list_tasks(db, accurate_po_id="99901") == []


def test_accept_audit_recorded(app_client: TestClient, db: Database) -> None:
    _seed_users(db)
    _seed_po(db)
    csrf = _login(app_client, "kp@eternal.local")
    app_client.post(f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf})
    audits = list(db["audit_logs"].find({"action": "po.accept"}, {"_id": 0}))
    assert len(audits) == 1
    assert audits[0]["actor_email"] == "kp@eternal.local"
    assert audits[0]["detail"]["accurate_po_id"] == PO_ID


def test_resync_preserves_acceptance(db: Database) -> None:
    _seed_users(db)
    _seed_po(db)
    kp = db["users"].find_one({"email_normalized": "kp@eternal.local"})
    po_acceptance.accept_po(db, kp, accurate_po_id=PO_ID)
    sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)
    po = get_by_accurate_id(db, PO_ID)
    assert po_acceptance.is_po_accepted(po)
    assert po["operational"]["acceptance"]["accepted_by"] == "kp@eternal.local"


def test_scheduling_blocked_before_accept(db: Database) -> None:
    from app.services.scheduling_service import ScheduleGateError

    _seed_users(db)
    po = _seed_po(db)
    kp = {
        "email_normalized": "kp@eternal.local",
        "role": "user",
        "operational_role": "production-head",
        "effective_module_permissions": ["access.all_branches"],
}
    try:
        scheduling_service.create_allocations(
            db,
            kp,
            po_doc=po,
            accurate_line_id=LINE_PRINT,
            allocations=[{"operator_email": "op1@eternal.local", "qty": 10}],
            idempotency_key="no-accept",
        )
        raise AssertionError("expected ScheduleGateError")
    except ScheduleGateError as exc:
        assert "belum diterima" in str(exc).lower() or "Kepala Produksi" in str(exc)


def test_design_and_derive_http_blocked_before_accept(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_po(db)
    csrf = _login(app_client, "owner@eternal.local")
    derive = app_client.post(
        f"/api/workflow/{PO_ID}/derive-tasks", headers={"X-CSRF-Token": csrf}
    )
    assert derive.status_code == 409
    design = app_client.post(
        f"/api/design/{PO_ID}/submission",
        files={"file": ("d.png", b"\x89PNG", "image/png")},
        headers={"X-CSRF-Token": csrf},
    )
    assert design.status_code == 409
    pay = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "uji bypass tanpa accept"},
        headers={"X-CSRF-Token": csrf},
    )
    assert pay.status_code == 409
    assert tasks.list_tasks(db, accurate_po_id=PO_ID) == []


def test_operations_full_and_super_admin_can_accept(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    # Admin = operations.full
    create_admin_user(
        db,
        email="admin@eternal.local",
        display_name="Admin",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    # Paksa role admin (create_admin_user biasanya super_admin pada owner)
    db["users"].update_one(
        {"email_normalized": "admin@eternal.local"},
        {"$set": {"role": "admin"}},
    )
    _seed_po(db)
    csrf = _login(app_client, "admin@eternal.local")
    res = app_client.post(f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200, res.text

    # PO kedua untuk super_admin
    import copy

    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = 99902
    payload["number"] = "PO/E2E/SA"
    upsert_sales_order(db, normalize_sales_order(payload))
    owner_csrf = _login(app_client, "owner@eternal.local")
    res2 = app_client.post(
        "/api/po/99902/accept", headers={"X-CSRF-Token": owner_csrf}
    )
    assert res2.status_code == 200, res2.text


def test_accept_retry_after_success_is_duplicate(
    app_client: TestClient, db: Database
) -> None:
    """Simulasi timeout client: transaksi sukses → retry tidak partial/ganda."""
    _seed_users(db)
    _seed_po(db)
    csrf = _login(app_client, "kp@eternal.local")
    first = app_client.post(f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200 and first.json()["duplicate"] is False
    audits_before = db["audit_logs"].count_documents({"action": "po.accept"})
    keys_before = {t["task_key"] for t in tasks.list_tasks(db, accurate_po_id=PO_ID)}
    retry = app_client.post(f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf})
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert db["audit_logs"].count_documents({"action": "po.accept"}) == audits_before
    keys_after = {t["task_key"] for t in tasks.list_tasks(db, accurate_po_id=PO_ID)}
    assert keys_after == keys_before


def test_accept_path_does_not_seed_master(
    app_client: TestClient, db: Database, monkeypatch
) -> None:
    _seed_users(db)
    _seed_po(db)
    from app.repositories import master_repository as master

    def _boom(*_a, **_k):
        raise AssertionError("seed_master_defaults tidak boleh dipanggil di accept")

    monkeypatch.setattr(master, "seed_master_defaults", _boom)
    csrf = _login(app_client, "kp@eternal.local")
    res = app_client.post(f"/api/po/{PO_ID}/accept", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200, res.text


def test_unaccepted_po_tracking_no_fake_production(db: Database) -> None:
    from app.services.tracking_progress import compute_progress, evaluate_milestones
    from app.services.tracking_workflow_adapter import load_workflow_snapshot

    _seed_po(db)
    po = get_by_accurate_id(db, PO_ID)
    wf = load_workflow_snapshot(db, PO_ID)
    milestones = evaluate_milestones(po, wf)
    progress = compute_progress(milestones, po, wf)
    by_id = {m["id"]: m for m in milestones}
    assert by_id["order_verified"]["state"] == "pending"
    assert "Kepala Produksi" in (by_id["order_verified"].get("detail") or "")
    assert progress["production"]["qty_produced"] in (0, 0.0, None)
    assert progress["active_milestone"]["id"] == "order_verified"
    assert progress["order_complete"] is False


def test_production_mutations_blocked_before_accept(
    app_client: TestClient, db: Database
) -> None:
    """production_service menolak mutasi sebelum DB berubah jika PO pending."""
    from app.repositories import task_repository as task_repo
    from app.services import production_service as prod

    _seed_users(db)
    _seed_po(db)
    helper = {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "operational_role": "helper",
        "effective_module_permissions": ["access.all_branches"],
}
    conf_before = db["task_confirmations"].count_documents({})
    tasks_before = len(task_repo.list_tasks(db, accurate_po_id=PO_ID))
    try:
        prod.confirm_preparation(
            db,
            helper,
            accurate_po_id=PO_ID,
            accurate_line_id=LINE_PRINT,
            qty=10,
            idempotency_key="prod-pending-prep",
        )
        raise AssertionError("expected ProductionGateError")
    except prod.ProductionGateError as exc:
        assert "belum diterima" in str(exc).lower()
    assert db["task_confirmations"].count_documents({}) == conf_before
    assert len(task_repo.list_tasks(db, accurate_po_id=PO_ID)) == tasks_before

    csrf = _login(app_client, "helper@eternal.local")
    http = app_client.post(
        f"/api/production/{PO_ID}/lines/{LINE_PRINT}/preparation",
        json={"qty": 10, "idempotency_key": "prod-pending-http"},
        headers={"X-CSRF-Token": csrf},
    )
    assert http.status_code == 409
    assert "belum diterima" in http.text.lower()
    assert db["task_confirmations"].count_documents({}) == conf_before


def test_production_preparation_ok_after_accept_and_schedule(db: Database) -> None:
    from app.repositories import gate_repository as gates
    from app.services import production_service as prod

    _seed_users(db)
    db["users"].insert_one(
        {
            "email_normalized": "op1@eternal.local",
            "display_name": "op1",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": "operator",
            "is_active": True,
            "effective_module_permissions": ["access.all_branches"]
        }
    )
    po = _seed_po(db)
    kp = db["users"].find_one({"email_normalized": "kp@eternal.local"})
    po_acceptance.accept_po(db, kp, accurate_po_id=PO_ID)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji prod"
        )
    po = get_by_accurate_id(db, PO_ID)
    scheduling_service.create_allocations(
        db,
        {
            "email_normalized": "kp@eternal.local",
            "role": "user",
            "operational_role": "production-head",
            "effective_module_permissions": ["access.all_branches"],
},
        po_doc=po,
        accurate_line_id=LINE_PRINT,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 50}],
        idempotency_key="prod-after-accept-sched",
    )
    helper = {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "operational_role": "helper",
        "effective_module_permissions": ["access.all_branches"],
}
    res = prod.confirm_preparation(
        db,
        helper,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=10,
        idempotency_key="prod-after-accept-prep",
    )
    assert res["task"]["qty_confirmed"] == 10
    assert res["quota"]["qty_prepared"] == 10
