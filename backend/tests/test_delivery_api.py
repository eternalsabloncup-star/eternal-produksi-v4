"""API Checkpoint E: auth, CSRF, validasi file, Loading, Driver, soft delete."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import delivery_repository as deliveries
from app.repositories import gate_repository as gates
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import production_service as prod
from app.services import scheduling_service

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_PRINT = "106318"
SJ_ID = "SJ-API-7001"
PNG = b"\x89PNG api"

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


def _sj_payload(**overrides) -> dict:
    payload = {
        "id": SJ_ID,
        "number": "SJ/API/0001",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-21",
        "toAddress": "ALAMAT-API-TEST",
        "detailItem": [
            {
                "id": "SJL-API-1",
                "salesOrderDetailId": LINE_PRINT,
                "itemNo": "BRG-SIM-001",
                "quantity": 200,
                "itemUnitName": "PCS",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _seed_users(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    # HTTP session membaca effective_module_permissions dari dokumen user.
    # Tanpa access.all_branches / branch_ids, assert_branch_access menolak
    # (baseline FAIL setelah gate cabang) — selaraskan dengan fixture service-layer.
    for email, role in (
        ("kp@eternal.local", "production-head"),
        ("helper@eternal.local", "helper"),
        ("screener@eternal.local", "screener"),
        ("op1@eternal.local", "operator"),
        ("driver@eternal.local", "driver"),
    ):
        db["users"].insert_one(
            {
                "email_normalized": normalize_email(email),
                "display_name": email,
                "password_hash": hash_password(PASSWORD),
                "role": "user",
                "operational_role": role,
                "is_active": True,
                "effective_module_permissions": ["access.all_branches"],
            }
        )


def _seed_packed(db: Database, qty: float = 200) -> None:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted
    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji api E"
        )
    po_doc = get_by_accurate_id(db, PO_ID)
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_PRINT,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="api-e-sched",
    )
    alloc = res["allocations"][0]["allocation_id"]
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=qty,
        idempotency_key="api-e-prep",
    )
    prod.confirm_screen(db, SCREENER, allocation_id=alloc, qty=20, idempotency_key="api-e-screen")
    from tests.sample_flow_helpers import approve_sample

    approve_sample(db, OPERATOR, HEAD, alloc)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=qty, idempotency_key="api-e-print"
    )
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=qty,
        idempotency_key="api-e-qc",
    )


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def test_delivery_endpoints_require_session(app_client: TestClient, db: Database) -> None:
    _seed_users(db)
    assert app_client.get("/api/delivery/schedule").status_code == 401
    assert (
        app_client.post(
            f"/api/delivery/{SJ_ID}/loading", json={"idempotency_key": "x" * 10}
        ).status_code
        == 401
    )


def test_note_ingest_requires_operations_permission(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_packed(db)
    driver_csrf = _login(app_client, "driver@eternal.local")
    denied = app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_sj_payload()]},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert denied.status_code == 403

    owner_csrf = _login(app_client, "owner@eternal.local")
    ok = app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_sj_payload()]},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert ok.status_code == 200
    assert ok.json()["count"] == 1


def test_note_ingest_fail_closed_on_missing_line_reference(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_packed(db)
    csrf = _login(app_client, "owner@eternal.local")
    payload = _sj_payload()
    del payload["detailItem"][0]["salesOrderDetailId"]
    res = app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [payload]},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 422
    assert "accurate_line_id" in res.json()["detail"]


def test_loading_blocked_then_driver_flow_over_http(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_packed(db, qty=100)
    # Kontrak Owner terbaru: Kepala Produksi berhak view/confirm/edit/cancel.
    # Grant ini hanya fixture DB test; bootstrap Settings saat release milik M5.
    # Tidak ada input Qty Loading manual: hanya aksi Sesuai/Tidak Sesuai.
    # Hak confirm tidak boleh otomatis menyelesaikan tugas koordinasi selisih.
    from app.repositories import master_repository as master

    master.set_role_actions(
        db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    owner_csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_sj_payload()]},
        headers={"X-CSRF-Token": owner_csrf},
    )

    driver_csrf = _login(app_client, "driver@eternal.local")
    validation = app_client.get(f"/api/delivery/{SJ_ID}/loading-validation")
    assert validation.json()["all_matched"] is False

    blocked = app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-load-blocked"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert blocked.status_code == 409

    kp_csrf = _login(app_client, "kp@eternal.local")
    discrepancy = app_client.post(
        f"/api/delivery/{SJ_ID}/discrepancy", headers={"X-CSRF-Token": kp_csrf}
    )
    assert discrepancy.status_code == 200
    task = discrepancy.json()
    assert task["coordination"] is True
    # Meski Kepala Produksi sendiri memilih Tidak Sesuai, tugas selisih
    # harus tetap menunggu konfirmasi terpisah, bukan auto-complete.
    pending = db["tasks"].find_one({"task_key": task["task_key"]})
    assert pending is not None
    from app.repositories import task_repository as task_repo

    assert pending.get("status") != task_repo.TASK_STATUS_DONE
    assert (
        task["context"]["message"]
        == "Mohon konfirmasi ke administrasi karena qty loading dan SJ berbeda"
    )

    confirmed = app_client.post(
        "/api/delivery/coordination/confirm",
        json={"task_key": task["task_key"], "idempotency_key": "api-coord-1"},
        headers={"X-CSRF-Token": kp_csrf},
    )
    assert confirmed.status_code == 200

    # Koordinasi tidak membuka Loading.
    driver_csrf = _login(app_client, "driver@eternal.local")
    still_blocked = app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-load-after-coord"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert still_blocked.status_code == 409


def test_full_loading_and_delivery_over_http(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_packed(db, qty=200)
    owner_csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_sj_payload()]},
        headers={"X-CSRF-Token": owner_csrf},
    )

    driver_csrf = _login(app_client, "driver@eternal.local")
    loaded = app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-load-ok"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert loaded.status_code == 200
    assert loaded.json()["duplicate"] is False

    retry = app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-load-ok-again"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert retry.json()["duplicate"] is True

    one_photo = app_client.post(
        f"/api/delivery/{SJ_ID}/delivered",
        data={"recipient_name": "Penerima API"},
        files={
            "photos": ("p1.png", PNG, "image/png"),
            "signature": ("sig.png", PNG, "image/png"),
        },
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert one_photo.status_code == 422

    delivered = app_client.post(
        f"/api/delivery/{SJ_ID}/delivered",
        data={"recipient_name": "Penerima API"},
        files=[
            ("photos", ("p1.png", PNG, "image/png")),
            ("photos", ("p2.png", b"\x89PNG dua", "image/png")),
            ("signature", ("sig.png", b"\x89PNG ttd", "image/png")),
        ],
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert delivered.status_code == 200, delivered.text
    body = delivered.json()
    assert body["status"] == "berhasil"
    assert body["courier_email"] == "driver@eternal.local"
    assert len(body["photo_evidence_ids"]) == 2

    detail = app_client.get(f"/api/delivery/{SJ_ID}")
    assert detail.status_code == 200
    assert len(detail.json()["evidence"]["photos"]) == 2
    assert detail.json()["events"][0]["recipient_name"] == "Penerima API"


def test_photo_type_rejected(app_client: TestClient, db: Database) -> None:
    _seed_users(db)
    _seed_packed(db, qty=200)
    owner_csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_sj_payload()]},
        headers={"X-CSRF-Token": owner_csrf},
    )
    driver_csrf = _login(app_client, "driver@eternal.local")
    app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-load-type"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    res = app_client.post(
        f"/api/delivery/{SJ_ID}/delivered",
        data={"recipient_name": "Penerima"},
        files=[
            ("photos", ("p1.exe", b"MZ", "application/x-msdownload")),
            ("photos", ("p2.png", PNG, "image/png")),
            ("signature", ("sig.png", PNG, "image/png")),
        ],
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert res.status_code == 415


def test_failure_and_soft_delete_over_http(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_packed(db, qty=200)
    owner_csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_sj_payload()]},
        headers={"X-CSRF-Token": owner_csrf},
    )
    driver_csrf = _login(app_client, "driver@eternal.local")
    app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-load-fail"},
        headers={"X-CSRF-Token": driver_csrf},
    )

    failed = app_client.post(
        f"/api/delivery/{SJ_ID}/failed",
        json={"reason": "Penerima tidak di tempat"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert failed.status_code == 200
    assert failed.json()["event"]["status"] == "gagal"

    delivered = app_client.post(
        f"/api/delivery/{SJ_ID}/delivered",
        data={"recipient_name": "Penerima Kedua"},
        files=[
            ("photos", ("p1.png", PNG, "image/png")),
            ("photos", ("p2.png", b"\x89PNG dua", "image/png")),
            ("signature", ("sig.png", PNG, "image/png")),
        ],
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert delivered.status_code == 200
    evidence_id = delivered.json()["photo_evidence_ids"][0]

    deleted = app_client.post(
        "/api/delivery/evidence/soft-delete",
        json={"evidence_id": evidence_id, "reason": "Foto salah unggah"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert deleted.status_code == 200
    assert deleted.json()["delete_reason"] == "Foto salah unggah"

    detail = app_client.get(f"/api/delivery/{SJ_ID}")
    active_ids = [p["evidence_id"] for p in detail.json()["evidence"]["photos"]]
    assert evidence_id not in active_ids
    assert db["evidence_files"].count_documents({"evidence_id": evidence_id}) == 1


def test_revision_then_cancel_and_reload_over_http(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_packed(db, qty=200)
    owner_csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_sj_payload()]},
        headers={"X-CSRF-Token": owner_csrf},
    )
    driver_csrf = _login(app_client, "driver@eternal.local")
    app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-rev-load-1"},
        headers={"X-CSRF-Token": driver_csrf},
    )

    # Revisi Qty satu item setelah Loading.
    revised = _sj_payload()
    revised["detailItem"][0]["quantity"] = 150
    owner_csrf = _login(app_client, "owner@eternal.local")
    ingested = app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [revised]},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert ingested.json()["items"][0]["revision_status"] == "PERLU_LOADING_ULANG"

    # Driver tanpa izin cancel ditolak 403.
    driver_csrf = _login(app_client, "driver@eternal.local")
    denied = app_client.post(
        f"/api/delivery/{SJ_ID}/loading/cancel",
        json={"reason": "Revisi Qty dari administrasi"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert denied.status_code == 403

    # Pengiriman memakai Loading lama diblokir backend.
    blocked = app_client.post(
        f"/api/delivery/{SJ_ID}/delivered",
        data={"recipient_name": "Penerima"},
        files=[
            ("photos", ("p1.png", PNG, "image/png")),
            ("photos", ("p2.png", PNG, "image/png")),
            ("signature", ("sig.png", PNG, "image/png")),
        ],
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert blocked.status_code == 409

    # Owner memberikan izin cancel lewat Master, lalu membatalkan Loading.
    owner_csrf = _login(app_client, "owner@eternal.local")
    grant = app_client.put(
        "/api/master/role-actions",
        json={
            "role": "driver",
            "task_type": "pengiriman",
            "actions": ["view", "input", "confirm", "manage_evidence", "cancel"],
        },
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert grant.status_code == 200

    driver_csrf = _login(app_client, "driver@eternal.local")
    cancelled = app_client.post(
        f"/api/delivery/{SJ_ID}/loading/cancel",
        json={"reason": "Revisi Qty dari administrasi"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True

    reloaded = app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-rev-load-2"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert reloaded.status_code == 200
    assert reloaded.json()["duplicate"] is False

    detail = app_client.get(f"/api/delivery/{SJ_ID}")
    body = detail.json()
    assert body["delivery_note"]["sj_version"] == 2
    assert [l["status"] for l in body["loadings"]] == ["dibatalkan", "aktif"]
    assert len(body["revisions"]) == 2

    delivered = app_client.post(
        f"/api/delivery/{SJ_ID}/delivered",
        data={"recipient_name": "Penerima Setelah Revisi"},
        files=[
            ("photos", ("p1.png", PNG, "image/png")),
            ("photos", ("p2.png", b"\x89PNG dua", "image/png")),
            ("signature", ("sig.png", PNG, "image/png")),
        ],
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert delivered.status_code == 200


def test_schedule_endpoint_with_date_filter(app_client: TestClient, db: Database) -> None:
    _seed_users(db)
    _seed_packed(db, qty=200)
    csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={
            "delivery_notes": [
                _sj_payload(),
                _sj_payload(id="SJ-API-7002", number="SJ/API/0002", transDate="2026-12-01"),
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    all_rows = app_client.get("/api/delivery/schedule")
    assert all_rows.json()["summary"]["sj_count"] == 2

    window = app_client.get(
        "/api/delivery/schedule?ship_date_from=2026-11-30&ship_date_to=2026-12-02"
    )
    assert window.json()["summary"]["sj_count"] == 1
    assert window.json()["items"][0]["accurate_sj_id"] == "SJ-API-7002"


def _do_payload(**overrides) -> dict:
    """Payload TEST bergaya Accurate Delivery Order (objek lookup bersarang)."""
    payload = {
        "id": SJ_ID,
        "number": "SJ/API/0001",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-21",
        "toAddress": "ALAMAT-API-TEST",
        "customer": {"id": 5501, "name": "PELANGGAN-API-TEST"},
        "customerNo": "C-API-001",
        "poNumber": "PO-PELANGGAN-API-7",
        "shipment": {"name": "EKSPEDISI-API-TEST"},
        "branch": {"name": "CABANG-API-TEST"},
        "masterSalesman": {"name": "PENJUAL-API-TEST"},
        "charField3": "ZONA-API-TEST",
        "printedByUser": "user.cetak.api",
        "deliveryPrintedTime": "21/11/2026 08:00:00",
        "deliveryPackingNumber": "PACK-API-0001",
        "createdByUserName": "user.pembuat.api",
        "statusName": "Belum Terkirim",
        "attachmentExist": False,
        "description": "KETERANGAN-API-TEST",
        "detailItem": [
            {
                "id": "SJL-API-1",
                "salesOrderDetailId": LINE_PRINT,
                "item": {"no": "BRG-SIM-001", "name": "Item Cetak API"},
                "detailName": "Item Cetak API",
                "quantity": 200,
                "itemUnit": {"name": "PCS"},
                "warehouse": {"name": "GUDANG-API-TEST"},
                "detailNotes": "CATATAN-API-TEST",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_schedule_endpoint_exposes_full_sj_header(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_packed(db, qty=200)
    csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_do_payload()]},
        headers={"X-CSRF-Token": csrf},
    )

    row = app_client.get("/api/delivery/schedule").json()["items"][0]
    assert row["customer_name"] == "PELANGGAN-API-TEST"
    assert row["customer_no"] == "C-API-001"
    assert row["customer_po_no"] == "PO-PELANGGAN-API-7"
    assert row["shipment"] == "EKSPEDISI-API-TEST"
    assert row["branch_name"] == "CABANG-API-TEST"
    assert row["salesman_name"] == "PENJUAL-API-TEST"
    assert row["ship_zone"] == "ZONA-API-TEST"
    assert row["printed_by_user"] == "user.cetak.api"
    assert row["printed_time"] == "21/11/2026 08:00:00"
    assert row["packing_number"] == "PACK-API-0001"
    assert row["created_by_user_name"] == "user.pembuat.api"
    assert row["accurate_status_name"] == "Belum Terkirim"
    assert row["description"] == "KETERANGAN-API-TEST"
    assert row["attachment_exist"] is False
    assert row["contact_info"] == ""
    assert row["item_count"] == 1
    assert row["qty_by_unit"] == {"PCS": 200.0}


def test_detail_endpoint_matches_schedule_row_values(
    app_client: TestClient, db: Database
) -> None:
    _seed_users(db)
    _seed_packed(db, qty=200)
    csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_do_payload()]},
        headers={"X-CSRF-Token": csrf},
    )

    row = app_client.get("/api/delivery/schedule").json()["items"][0]
    note = app_client.get(f"/api/delivery/{SJ_ID}").json()["delivery_note"]

    for field in (
        "sj_no",
        "po_no",
        "customer_po_no",
        "customer_name",
        "customer_no",
        "destination",
        "contact_info",
        "salesman_name",
        "created_by_user_name",
        "shipment",
        "ship_zone",
        "branch_name",
        "description",
        "printed_by_user",
        "printed_time",
        "packing_number",
        "accurate_status_name",
        "sj_date",
        "ship_date",
    ):
        assert row[field] == note[field], field
    assert row["items"] == note["items"]
    assert note["items"][0]["warehouse_name"] == "GUDANG-API-TEST"
    assert note["items"][0]["detail_notes"] == "CATATAN-API-TEST"


def test_delivery_evidence_content_scoped_and_auth(
    app_client: TestClient, db: Database
) -> None:
    """GET /api/delivery/{sj}/evidence/{id}/content — session, scope SJ, soft-delete."""
    from app.repositories import evidence_repository as evidence
    from app.services import delivery_service

    _seed_users(db)
    _seed_packed(db, qty=200)
    owner_csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        "/api/delivery/notes",
        json={"delivery_notes": [_sj_payload()]},
        headers={"X-CSRF-Token": owner_csrf},
    )
    sj_other = "SJ-API-7002"
    app_client.post(
        "/api/delivery/notes",
        json={
            "delivery_notes": [
                _sj_payload(id=sj_other, number="SJ/API/0002")
            ]
        },
        headers={"X-CSRF-Token": owner_csrf},
    )

    driver_csrf = _login(app_client, "driver@eternal.local")
    app_client.post(
        f"/api/delivery/{SJ_ID}/loading",
        json={"idempotency_key": "api-load-evid-content"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    delivered = app_client.post(
        f"/api/delivery/{SJ_ID}/delivered",
        data={"recipient_name": "Penerima Content"},
        files=[
            ("photos", ("p1.png", PNG + b"-photo1", "image/png")),
            ("photos", ("p2.png", PNG + b"-photo2", "image/png")),
            ("signature", ("sig.png", PNG + b"-sig", "image/png")),
        ],
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert delivered.status_code == 200, delivered.text
    photo_id = delivered.json()["photo_evidence_ids"][0]
    sig_id = delivered.json()["signature_evidence_id"]

    # Tanpa session → 401
    bare = TestClient(app_client.app)
    unauth = bare.get(f"/api/delivery/{SJ_ID}/evidence/{photo_id}/content")
    assert unauth.status_code in (401, 403)

    # Driver boleh stream
    ok = app_client.get(f"/api/delivery/{SJ_ID}/evidence/{photo_id}/content")
    assert ok.status_code == 200, ok.text
    assert ok.headers.get("content-type", "").startswith("image/")
    assert ok.content.startswith(b"\x89PNG")

    sig_ok = app_client.get(f"/api/delivery/{SJ_ID}/evidence/{sig_id}/content")
    assert sig_ok.status_code == 200
    assert sig_ok.content.startswith(b"\x89PNG")

    # Cross-SJ: evidence SJ_ID dipanggil lewat SJ lain → 404/403
    cross = app_client.get(f"/api/delivery/{sj_other}/evidence/{photo_id}/content")
    assert cross.status_code in (403, 404)

    # Evidence ID asing
    fake = app_client.get(f"/api/delivery/{SJ_ID}/evidence/evid:fake/content")
    assert fake.status_code == 404

    # Helper tanpa izin view pengiriman → 403
    _login(app_client, "helper@eternal.local")
    denied = app_client.get(f"/api/delivery/{SJ_ID}/evidence/{photo_id}/content")
    assert denied.status_code == 403

    # Soft-delete → tidak bisa dibuka lewat content endpoint
    driver_csrf = _login(app_client, "driver@eternal.local")
    soft = app_client.post(
        "/api/delivery/evidence/soft-delete",
        json={"evidence_id": photo_id, "reason": "salah unggah uji"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert soft.status_code == 200, soft.text
    gone = app_client.get(f"/api/delivery/{SJ_ID}/evidence/{photo_id}/content")
    assert gone.status_code == 404
    meta = evidence.get_evidence(db, photo_id)
    assert meta is not None
    assert meta.get("deleted_at") is not None
    # Soft-deleted tidak lagi di active list
    active_ids = {
        str(p.get("evidence_id"))
        for p in delivery_service.active_delivery_evidence(db, SJ_ID)["photos"]
    }
    assert photo_id not in active_ids
