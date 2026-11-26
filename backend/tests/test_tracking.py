"""Live Tracking — token security, milestone honesty, dokumen, progres."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.domain.tracking_milestones import (
    CUSTOMER_MILESTONES,
    slugify_customer,
    slugify_po_reference,
)
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.po_repository import upsert_sales_order
from app.services import tracking_service as tracking
from app.services.tracking_progress import compute_progress, evaluate_milestones
from app.services.tracking_workflow_adapter import load_workflow_snapshot

_TEST_BRANCH = "HQ"


def _seed_admin(db: Database) -> None:
    create_admin_user(
        db,
        email="admin@eternal.local",
        display_name="Admin",
        password="password-kuat-123",
        allow_if_admin_exists=True,
    )


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "password-kuat-123"},
    )
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    return csrf


def _sync_po(db: Database, payload: dict | None = None) -> dict:
    """Seed PO uji tanpa melewati reject missing_branch_id pada ingest live."""
    import copy

    raw = copy.deepcopy(payload or SAMPLE_SALES_ORDER_STRUCTURE)
    po = normalize_sales_order(raw)
    stored = upsert_sales_order(db, po, branch_id_on_insert=_TEST_BRANCH)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": str(po.accurate_id)},
        {"$set": {"branch_id": _TEST_BRANCH, "branch_code": _TEST_BRANCH}},
    )
    return {"document": stored, "accurate_id": str(po.accurate_id), "outcome": "created"}


def test_milestone_catalog_is_exactly_15() -> None:
    assert len(CUSTOMER_MILESTONES) == 15
    assert [m.seq for m in CUSTOMER_MILESTONES] == list(range(1, 16))


def test_public_tracking_valid_link(app_client: TestClient, db: Database) -> None:
    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)

    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert gen.status_code == 201, gen.text
    body = gen.json()
    assert body["token"]
    assert "token_hash" not in body["link"]
    assert body["path"].startswith("/t/")
    assert len(body["token"]) >= 20

    # path is /t/slug/po/token → API is /api/public/tracking/slug/po/token
    public = app_client.get(
        f"/api/public/tracking/{body['customer_slug']}/{body['po_slug']}/{body['token']}"
    )
    assert public.status_code == 200, public.text
    payload = public.json()
    assert payload["view"] == "customer"
    assert payload["order"]["po_no"]
    assert payload["order"]["accurate_po_id"] is None  # jangan expose ke customer
    assert len(payload["milestones"]) == 15
    assert payload["milestones"][0]["state"] == "completed"  # pesanan diterima
    # Verifikasi belum ada kontrak → pending
    assert payload["milestones"][1]["state"] == "pending"
    # Owner M4: faktur/receipt boleh di publik bila ada data PO-scoped (kosong = waiting)
    invoice = next(d for d in payload["documents"] if d["category"] == "sales_invoice")
    assert invoice["status"] == "waiting"
    receipt = next(d for d in payload["documents"] if d["category"] == "sales_receipt")
    assert receipt["status"] == "waiting"
    design = next(d for d in payload["documents"] if d["category"] == "design")
    assert design["status"] == "waiting"
    assert public.headers.get("cache-control", "").startswith("no-store")
    assert public.headers.get("referrer-policy") == "no-referrer"


def test_wrong_slug_or_po_does_not_expand_access(
    app_client: TestClient, db: Database
) -> None:
    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()

    bad_slug = app_client.get(
        f"/api/public/tracking/pelanggan-salah/{gen['po_slug']}/{gen['token']}"
    )
    assert bad_slug.status_code == 404

    bad_po = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/po-salah/{gen['token']}"
    )
    assert bad_po.status_code == 404


def test_invalid_revoked_expired_tokens(
    app_client: TestClient, db: Database
) -> None:
    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()

    guess = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/AAAAAAAABBBBCCCCDDDD"
    )
    assert guess.status_code == 404

    revoked = app_client.post(
        f"/api/tracking/links/{gen['link']['id']}/revoke",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "uji"},
    )
    assert revoked.status_code == 200
    after = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
    )
    assert after.status_code == 404

    # Expired
    gen2 = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={"ttl_seconds": 60},
    ).json()
    db["tracking_links"].update_one(
        {},
        {
            "$set": {
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=5),
            }
        },
        # update the new active one by token path via list
    )
    # Set expiry on the specific link
    from app.repositories.tracking_link_repository import hash_token

    db["tracking_links"].update_one(
        {"token_hash": hash_token(gen2["token"])},
        {
            "$set": {
                "expires_at": datetime.now(timezone.utc) - timedelta(seconds=5),
            }
        },
    )
    expired = app_client.get(
        f"/api/public/tracking/{gen2['customer_slug']}/{gen2['po_slug']}/{gen2['token']}"
    )
    assert expired.status_code == 404


def test_regenerate_revokes_previous(app_client: TestClient, db: Database) -> None:
    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)
    first = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    second = app_client.post(
        "/api/tracking/95106/links/regenerate",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    assert second["token"] != first["token"]
    assert second["revoked_previous"] >= 1
    old = app_client.get(
        f"/api/public/tracking/{first['customer_slug']}/{first['po_slug']}/{first['token']}"
    )
    assert old.status_code == 404
    neu = app_client.get(
        f"/api/public/tracking/{second['customer_slug']}/{second['po_slug']}/{second['token']}"
    )
    assert neu.status_code == 200


def test_non_print_marks_design_milestones_na(db: Database) -> None:
    import copy

    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    for line in payload["detailItem"]:
        line["charField1"] = "Tidak Cetak"
    _sync_po(db, payload)
    from app.repositories.po_repository import get_by_accurate_id

    po = get_by_accurate_id(db, "95106")
    assert po is not None
    wf = load_workflow_snapshot(db, "95106")
    milestones = evaluate_milestones(po, wf)
    by_id = {m["id"]: m for m in milestones}
    assert by_id["design_prep"]["state"] == "not_applicable"
    assert by_id["design_approved"]["state"] == "not_applicable"
    assert by_id["print_prep"]["state"] == "not_applicable"
    assert by_id["printing"]["state"] == "not_applicable"
    assert by_id["order_received"]["state"] == "completed"
    assert by_id["order_complete"]["state"] == "pending"


def test_milestone_progress_not_used_as_production_percent(db: Database) -> None:
    _sync_po(db)
    from app.repositories.po_repository import get_by_accurate_id

    po = get_by_accurate_id(db, "95106")
    assert po is not None
    # Seed partial cetak task tanpa klaim order complete
    if "operational_tasks" not in db.list_collection_names():
        db.create_collection("operational_tasks")
    db["operational_tasks"].insert_one(
        {
            "task_type": "cetak",
            "status": "sebagian",
            "qty_target": 1000,
            "qty_confirmed": 250,
            "ref": {"accurate_po_id": "95106"},
            "updated_at": datetime.now(timezone.utc),
        }
    )
    wf = load_workflow_snapshot(db, "95106")
    milestones = evaluate_milestones(po, wf)
    progress = compute_progress(milestones, po, wf)
    assert progress["milestone"]["completed"] >= 1
    assert progress["production"]["qty_produced"] == 250
    assert progress["production"]["ratio"] is not None
    # Milestone ratio != production ratio secara umum
    assert progress["order_complete"] is False
    assert progress["completion_blocked_reason"]


def _seed_pr6_delivery(
    db: Database,
    *,
    accurate_sj_id: str,
    qty: float,
    superseded_by: str | None = None,
    with_loading: bool = False,
    with_success: bool = False,
    soft_delete_evidence: bool = False,
) -> None:
    """Fixture bentuk dokumen mengikuti kontrak PR #6 (tanpa mengimpor modulnya)."""
    for name in (
        "accurate_delivery_notes",
        "loading_events",
        "delivery_events",
        "evidence_files",
    ):
        if name not in db.list_collection_names():
            db.create_collection(name)

    db["accurate_delivery_notes"].insert_one(
        {
            "accurate_sj_id": accurate_sj_id,
            "accurate_po_id": "95106",
            "sj_no": f"SJ/{accurate_sj_id}",
            "items": [{"accurate_line_id": "line-1", "qty": qty, "item_name": "Cup"}],
            "superseded_by": superseded_by,
        }
    )
    if with_loading:
        db["loading_events"].insert_one(
            {
                "loading_id": f"loading:{accurate_sj_id}:v1",
                "accurate_sj_id": accurate_sj_id,
                "accurate_po_id": "95106",
                "status": "aktif",
            }
        )
    if with_success:
        photo_ids = [f"evid:delivery-photo:{accurate_sj_id}:v1", f"evid:delivery-photo:{accurate_sj_id}:v2"]
        sig_id = f"evid:delivery-signature:{accurate_sj_id}:v1"
        db["delivery_events"].insert_one(
            {
                "delivery_event_id": f"delivery:{accurate_sj_id}:attempt-1",
                "accurate_sj_id": accurate_sj_id,
                "accurate_po_id": "95106",
                "attempt": 1,
                "status": "berhasil",
                "recipient_name": "Penerima Uji",
                "photo_evidence_ids": photo_ids,
                "signature_evidence_id": sig_id,
                "created_at": datetime.now(timezone.utc),
            }
        )
        event_id = f"delivery:{accurate_sj_id}:attempt-1"
        for eid in photo_ids + [sig_id]:
            scope = "delivery-signature" if "signature" in eid else "delivery-photo"
            db["evidence_files"].insert_one(
                {
                    "evidence_id": eid,
                    "scope": scope,
                    "scope_id": accurate_sj_id,
                    "deleted_at": datetime.now(timezone.utc) if soft_delete_evidence else None,
                    "context": {
                        "delivery_commit": "committed",
                        "accurate_sj_id": accurate_sj_id,
                        "delivery_event_id": event_id,
                        "delivery_attempt": 1,
                    },
                }
            )


def test_order_complete_requires_validated_delivery_qty(db: Database) -> None:
    """Selesai hanya jika Qty diterima sah (bukan sekadar Qty SJ)."""
    _sync_po(db)
    from app.repositories.po_repository import get_by_accurate_id
    from app.services.tracking_progress import qty_po_total

    po = get_by_accurate_id(db, "95106")
    assert po is not None
    total = qty_po_total(po)

    # SJ dibuat, belum Loading → belum selesai
    _seed_pr6_delivery(db, accurate_sj_id="SJ-A", qty=total, with_loading=False, with_success=False)
    wf = load_workflow_snapshot(db, "95106")
    progress = compute_progress(evaluate_milestones(po, wf), po, wf)
    assert progress["shipping"]["qty_delivered"] == 0.0
    assert progress["order_complete"] is False

    # Loading selesai, belum delivered → belum selesai
    db["loading_events"].insert_one(
        {
            "loading_id": "loading:SJ-A:v1",
            "accurate_sj_id": "SJ-A",
            "accurate_po_id": "95106",
            "status": "aktif",
        }
    )
    wf = load_workflow_snapshot(db, "95106")
    progress = compute_progress(evaluate_milestones(po, wf), po, wf)
    assert progress["shipping"]["qty_delivered"] == 0.0
    assert progress["order_complete"] is False
    by_id = {m["id"]: m for m in evaluate_milestones(po, wf)}
    assert by_id["ready_to_ship"]["state"] == "completed"
    assert by_id["order_complete"]["state"] != "completed"

    # Pengiriman parsial → belum selesai
    for name in ("accurate_delivery_notes", "loading_events", "delivery_events", "evidence_files"):
        db[name].delete_many({})
    half = total / 2
    _seed_pr6_delivery(
        db, accurate_sj_id="SJ-P1", qty=half, with_loading=True, with_success=True
    )
    wf = load_workflow_snapshot(db, "95106")
    progress = compute_progress(evaluate_milestones(po, wf), po, wf)
    assert progress["shipping"]["qty_delivered"] == half
    assert progress["order_complete"] is False

    # SJ direvisi/dibatalkan (superseded) → tidak double-count
    _seed_pr6_delivery(
        db,
        accurate_sj_id="SJ-OLD",
        qty=total,
        superseded_by="SJ-P1",
        with_loading=True,
        with_success=True,
    )
    wf = load_workflow_snapshot(db, "95106")
    progress = compute_progress(evaluate_milestones(po, wf), po, wf)
    assert progress["shipping"]["qty_delivered"] == half
    assert progress["order_complete"] is False

    # Bukti soft-deleted → tidak dihitung
    for name in ("accurate_delivery_notes", "loading_events", "delivery_events", "evidence_files"):
        db[name].delete_many({})
    _seed_pr6_delivery(
        db,
        accurate_sj_id="SJ-SOFT",
        qty=total,
        with_loading=True,
        with_success=True,
        soft_delete_evidence=True,
    )
    wf = load_workflow_snapshot(db, "95106")
    progress = compute_progress(evaluate_milestones(po, wf), po, wf)
    assert progress["shipping"]["qty_delivered"] == 0.0
    assert progress["order_complete"] is False

    # Semua Qty diterima dengan bukti valid → selesai
    for name in ("accurate_delivery_notes", "loading_events", "delivery_events", "evidence_files"):
        db[name].delete_many({})
    _seed_pr6_delivery(
        db, accurate_sj_id="SJ-FULL", qty=total, with_loading=True, with_success=True
    )
    wf = load_workflow_snapshot(db, "95106")
    progress = compute_progress(evaluate_milestones(po, wf), po, wf)
    assert progress["shipping"]["qty_delivered"] == total
    assert progress["order_complete"] is True
    assert progress["shipping"]["source"] == "delivery_events+accurate_delivery_notes"


def test_design_gate_maps_approval_milestone(db: Database) -> None:
    _sync_po(db)
    from app.repositories.po_repository import get_by_accurate_id

    po = get_by_accurate_id(db, "95106")
    assert po is not None
    if "po_gates" not in db.list_collection_names():
        db.create_collection("po_gates")
    db["po_gates"].insert_one(
        {
            "accurate_po_id": "95106",
            "design": {
                "state": "open",
                "reason": "approved",
                "updated_at": datetime.now(timezone.utc),
            },
        }
    )
    if "design_submissions" not in db.list_collection_names():
        db.create_collection("design_submissions")
    db["design_submissions"].insert_one(
        {
            "accurate_po_id": "95106",
            "version": 1,
            "confirmed_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
    )
    wf = load_workflow_snapshot(db, "95106")
    milestones = evaluate_milestones(po, wf)
    by_id = {m["id"]: m for m in milestones}
    assert by_id["design_prep"]["state"] == "completed"
    assert by_id["design_approved"]["state"] == "completed"


def test_internal_view_includes_gaps_and_links(
    app_client: TestClient, db: Database
) -> None:
    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)
    app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    detail = app_client.get("/api/tracking/95106")
    assert detail.status_code == 200
    body = detail.json()
    assert body["view"] == "internal"
    assert "internal" in body
    assert "gaps" in body["internal"]
    assert isinstance(body["links"], list)
    assert body["order"]["accurate_po_id"] == "95106"
    invoice = next(d for d in body["documents"] if d["category"] == "sales_invoice")
    assert invoice["status"] == "waiting"


def test_unauthenticated_cannot_manage_links(
    app_client: TestClient, db: Database
) -> None:
    _sync_po(db)
    denied = app_client.post("/api/tracking/95106/links", json={})
    assert denied.status_code in (401, 403)


def test_slug_helpers() -> None:
    assert slugify_customer("Kopi Nusantara!") == "kopi-nusantara"
    assert "000010" in slugify_po_reference("PO/18/11/2026/000010")


def test_service_rejects_cross_customer_token(db: Database) -> None:
    _sync_po(db)
    result = tracking.generate_tracking_link(
        db, accurate_po_id="95106", actor_email="admin@eternal.local"
    )
    try:
        tracking.public_tracking_view(
            db,
            customer_slug="bukan-customer",
            po_slug=result["po_slug"],
            raw_token=result["token"],
        )
        assert False, "expected TrackingNotFound"
    except tracking.TrackingNotFound:
        pass


def test_public_documents_invoice_receipt_design_and_multi_sj(
    app_client: TestClient, db: Database
) -> None:
    """Owner M4: kartu dokumen terhubung Accurate + multi-SJ; multi-SO ditahan."""
    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)

    for name in (
        "accurate_sales_invoices",
        "accurate_payment_receipts",
        "design_submissions",
        "design_approval_links",
        "design_approvals",
        "accurate_delivery_notes",
    ):
        if name not in db.list_collection_names():
            db.create_collection(name)

    # Invoice single-PO → preview OK
    db["accurate_sales_invoices"].insert_one(
        {
            "accurate_invoice_id": "INV-1",
            "invoice_no": "SI/001",
            "trans_date": "01/11/2026",
            "status": "OPEN",
            "total_amount": 1000.0,
            "accurate_po_ids": ["95106"],
            "primary_accurate_po_id": "95106",
            "lines": [{"sales_order_id": "95106", "qty": 1}],
            "review_required": False,
        }
    )
    # Invoice multi-SO → tidak preview penuh
    db["accurate_sales_invoices"].insert_one(
        {
            "accurate_invoice_id": "INV-MULTI",
            "invoice_no": "SI/MULTI",
            "trans_date": "02/11/2026",
            "status": "OPEN",
            "total_amount": 9999.0,
            "accurate_po_ids": ["95106", "99999"],
            "primary_accurate_po_id": "",
            "lines": [
                {"sales_order_id": "95106", "qty": 1},
                {"sales_order_id": "99999", "qty": 9},
            ],
            "review_required": True,
            "review_reason": "multi-SO",
        }
    )
    # Receipt PO-scoped
    db["accurate_payment_receipts"].insert_many(
        [
            {
                "accurate_receipt_id": "RCP-1",
                "accurate_po_id": "95106",
                "po_no": "PO-TEST",
                "invoice_no": "SI/001",
                "amount": 400.0,
                "receipt_date": "03/11/2026",
                "raw_ref": {
                    "receipt_no": "SR/001",
                    "review_required": False,
                    "void_cheq": False,
                },
            },
            {
                "accurate_receipt_id": "RCP-2",
                "accurate_po_id": "95106",
                "invoice_no": "SI/001",
                "amount": 600.0,
                "receipt_date": "04/11/2026",
                "raw_ref": {
                    "receipt_no": "SR/002",
                    "review_required": False,
                    "void_cheq": False,
                },
            },
            {
                "accurate_receipt_id": "RCP-OTHER",
                "accurate_po_id": "99999",
                "amount": 1.0,
                "receipt_date": "04/11/2026",
                "raw_ref": {"receipt_no": "SR/OTHER", "review_required": False},
            },
        ]
    )
    # Design: v1 shared (link), v2 internal only (no link), v1 not yet approved
    db["design_submissions"].insert_many(
        [
            {
                "accurate_po_id": "95106",
                "version": 1,
                "filename": "mockup-v1.pdf",
                "confirmed_at": datetime.now(timezone.utc),
                "submitted_at": datetime.now(timezone.utc),
            },
            {
                "accurate_po_id": "95106",
                "version": 2,
                "filename": "internal-only.pdf",
                "confirmed_at": None,
                "submitted_at": datetime.now(timezone.utc),
            },
        ]
    )
    db["design_approval_links"].insert_one(
        {
            "token_hash": "hash-dummy",
            "accurate_po_id": "95106",
            "design_version": 1,
            "created_at": datetime.now(timezone.utc),
            "revoked_at": None,
        }
    )
    # 3 SJ aktif + 1 superseded
    _seed_pr6_delivery(db, accurate_sj_id="SJ-1", qty=10)
    _seed_pr6_delivery(db, accurate_sj_id="SJ-2", qty=20)
    _seed_pr6_delivery(db, accurate_sj_id="SJ-3", qty=30)
    _seed_pr6_delivery(
        db, accurate_sj_id="SJ-OLD", qty=99, superseded_by="SJ-1"
    )

    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    public = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
    )
    assert public.status_code == 200, public.text
    docs = {d["category"]: d for d in public.json()["documents"]}

    assert docs["sales_invoice"]["status"] == "available"
    inv_items = docs["sales_invoice"]["items"]
    single = next(i for i in inv_items if i["id"] == "INV-1")
    assert single["preview_allowed"] is True
    assert single["file_available"] is True
    assert single["download_allowed"] is True
    multi = next(i for i in inv_items if i["id"] == "INV-MULTI")
    assert multi["preview_allowed"] is False
    assert multi["file_available"] is False
    assert multi["download_allowed"] is False
    assert multi["amount"] is None

    assert docs["sales_receipt"]["status"] == "available"
    assert len(docs["sales_receipt"]["items"]) == 2
    assert all(i["preview_allowed"] for i in docs["sales_receipt"]["items"])
    assert {i["number"] for i in docs["sales_receipt"]["items"]} == {"SR/001", "SR/002"}

    assert docs["design"]["status"] == "available"
    assert len(docs["design"]["items"]) == 1
    assert docs["design"]["items"][0]["version"] == 1
    assert docs["design"]["items"][0]["status"] == "active_share"
    assert docs["design"]["items"][0]["approval_status"] == "not_approved"
    assert docs["design"]["items"][0]["preview_allowed"] is True

    assert docs["delivery_note"]["status"] == "available"
    sj_ids = {i["id"] for i in docs["delivery_note"]["items"]}
    assert sj_ids == {"SJ-1", "SJ-2", "SJ-3"}
    assert "SJ-OLD" not in sj_ids


def test_invoice_review_and_incomplete_mapping_fail_closed(db: Database) -> None:
    _sync_po(db)
    if "accurate_sales_invoices" not in db.list_collection_names():
        db.create_collection("accurate_sales_invoices")
    db["accurate_sales_invoices"].insert_many(
        [
            {
                "accurate_invoice_id": "INV-REV",
                "invoice_no": "SI/REV",
                "trans_date": "01/11/2026",
                "total_amount": 500.0,
                "accurate_po_ids": ["95106"],
                "primary_accurate_po_id": "95106",
                "lines": [{"sales_order_id": "95106", "qty": 1}],
                "review_required": True,
                "review_reason": "qty line invalid",
            },
            {
                "accurate_invoice_id": "INV-NOLINE",
                "invoice_no": "SI/NOLINE",
                "trans_date": "01/11/2026",
                "total_amount": 100.0,
                "accurate_po_ids": ["95106"],
                "primary_accurate_po_id": "95106",
                "lines": [],
                "review_required": False,
            },
            {
                "accurate_invoice_id": "INV-CONFLICT",
                "invoice_no": "SI/CONFLICT",
                "total_amount": 200.0,
                "accurate_po_ids": ["95106"],
                "primary_accurate_po_id": "OTHER",
                "lines": [{"sales_order_id": "95106"}],
                "review_required": False,
            },
        ]
    )
    result = tracking.generate_tracking_link(
        db, accurate_po_id="95106", actor_email="admin@eternal.local"
    )
    payload = tracking.public_tracking_view(
        db,
        customer_slug=result["customer_slug"],
        po_slug=result["po_slug"],
        raw_token=result["token"],
    )
    inv = next(d for d in payload["documents"] if d["category"] == "sales_invoice")
    assert inv["status"] == "waiting" or all(
        not i["preview_allowed"] for i in inv["items"]
    )
    for item in inv["items"]:
        assert item["preview_allowed"] is False
        assert item["amount"] is None


def test_receipt_boolean_void_and_multi_invoice_allocation(db: Database) -> None:
    _sync_po(db)
    if "accurate_payment_receipts" not in db.list_collection_names():
        db.create_collection("accurate_payment_receipts")
    db["accurate_payment_receipts"].insert_many(
        [
            {
                "accurate_receipt_id": "RCP-FALSE-STR",
                "accurate_po_id": "95106",
                "amount": 10.0,
                "receipt_date": "01/11/2026",
                "invoice_no": "SI/A",
                "raw_ref": {
                    "receipt_no": "SR/F",
                    "void_cheq": "false",
                    "review_required": "false",
                    "invoice_ids": ["INV-A"],
                },
            },
            {
                "accurate_receipt_id": "RCP-TRUE-STR",
                "accurate_po_id": "95106",
                "amount": 20.0,
                "raw_ref": {
                    "receipt_no": "SR/T",
                    "void_cheq": "true",
                    "review_required": False,
                    "invoice_ids": ["INV-A"],
                },
            },
            {
                "accurate_receipt_id": "RCP-UNKNOWN",
                "accurate_po_id": "95106",
                "amount": 30.0,
                "raw_ref": {
                    "receipt_no": "SR/U",
                    "void_cheq": "maybe",
                    "review_required": False,
                    "invoice_ids": ["INV-A"],
                },
            },
            {
                "accurate_receipt_id": "RCP-MULTI",
                "accurate_po_id": "95106",
                "amount": 40.0,
                "raw_ref": {
                    "receipt_no": "SR/M",
                    "void_cheq": False,
                    "review_required": False,
                    "invoice_ids": ["INV-A", "INV-B"],
                },
            },
            {
                "accurate_receipt_id": "RCP-NEG",
                "accurate_po_id": "95106",
                "amount": -5.0,
                "raw_ref": {
                    "receipt_no": "SR/N",
                    "void_cheq": False,
                    "review_required": False,
                    "invoice_ids": ["INV-A"],
                },
            },
        ]
    )
    result = tracking.generate_tracking_link(
        db, accurate_po_id="95106", actor_email="admin@eternal.local"
    )
    payload = tracking.public_tracking_view(
        db,
        customer_slug=result["customer_slug"],
        po_slug=result["po_slug"],
        raw_token=result["token"],
    )
    receipt = next(d for d in payload["documents"] if d["category"] == "sales_receipt")
    by_id = {i["id"]: i for i in receipt["items"]}
    assert by_id["RCP-FALSE-STR"]["status"] == "verified"
    assert by_id["RCP-FALSE-STR"]["amount"] == 10.0
    assert by_id["RCP-TRUE-STR"]["status"] == "void"
    assert by_id["RCP-TRUE-STR"]["amount"] is None
    assert by_id["RCP-TRUE-STR"]["preview_allowed"] is False
    assert by_id["RCP-UNKNOWN"]["status"] == "review"
    assert by_id["RCP-UNKNOWN"]["amount"] is None
    assert by_id["RCP-MULTI"]["status"] == "review"
    assert by_id["RCP-MULTI"]["amount"] is None
    assert by_id["RCP-MULTI"]["invoice_no"] is None
    assert by_id["RCP-NEG"]["status"] == "review"
    assert by_id["RCP-NEG"]["amount"] is None


def test_design_revoked_vs_active_visibility(db: Database) -> None:
    _sync_po(db)
    for name in ("design_submissions", "design_approval_links", "design_approvals"):
        if name not in db.list_collection_names():
            db.create_collection(name)
    now = datetime.now(timezone.utc)
    db["design_submissions"].insert_many(
        [
            {
                "accurate_po_id": "95106",
                "version": 1,
                "filename": "v1.pdf",
                "confirmed_at": now,
            },
            {
                "accurate_po_id": "95106",
                "version": 2,
                "filename": "v2.pdf",
                "confirmed_at": now,
            },
            {
                "accurate_po_id": "95106",
                "version": 3,
                "filename": "v3.pdf",
                "confirmed_at": now,
            },
        ]
    )
    db["design_approval_links"].insert_many(
        [
            {
                "token_hash": "hash-revoked-v1",
                "accurate_po_id": "95106",
                "design_version": 1,
                "created_at": now,
                "revoked_at": now,
                "expires_at": now + timedelta(hours=24),
                "used_at": None,
            },
            {
                "token_hash": "hash-active-v2",
                "accurate_po_id": "95106",
                "design_version": 2,
                "created_at": now,
                "revoked_at": None,
                "expires_at": now + timedelta(hours=24),
                "used_at": None,
            },
            {
                "token_hash": "hash-hist-v3",
                "accurate_po_id": "95106",
                "design_version": 3,
                "created_at": now - timedelta(days=5),
                "revoked_at": None,
                "expires_at": now - timedelta(hours=1),
                "used_at": now - timedelta(days=1),
            },
        ]
    )
    db["design_approvals"].insert_one(
        {
            "accurate_po_id": "95106",
            "design_version": 3,
            "approved_at": now - timedelta(days=1),
        }
    )
    result = tracking.generate_tracking_link(
        db, accurate_po_id="95106", actor_email="admin@eternal.local"
    )
    payload = tracking.public_tracking_view(
        db,
        customer_slug=result["customer_slug"],
        po_slug=result["po_slug"],
        raw_token=result["token"],
    )
    design = next(d for d in payload["documents"] if d["category"] == "design")
    by_ver = {i["version"]: i for i in design["items"]}
    assert by_ver[1]["status"] == "revoked"
    assert by_ver[1]["preview_allowed"] is False
    assert by_ver[1]["filename"] is None
    assert by_ver[2]["status"] == "active_share"
    assert by_ver[2]["preview_allowed"] is True
    assert by_ver[2]["approval_status"] == "not_approved"
    assert by_ver[3]["status"] == "historical_share"
    assert by_ver[3]["preview_allowed"] is True
    assert by_ver[3]["approval_status"] == "approved"
    assert by_ver[3]["file_available"] is False


def test_public_tracking_hides_other_branch_receipt(db: Database) -> None:
    _sync_po(db)
    if "accurate_payment_receipts" not in db.list_collection_names():
        db.create_collection("accurate_payment_receipts")
    db["accurate_payment_receipts"].insert_one(
        {
            "accurate_receipt_id": "RCP-X",
            "accurate_po_id": "OTHER-PO",
            "amount": 50,
            "raw_ref": {"receipt_no": "SR/X", "review_required": False},
        }
    )
    result = tracking.generate_tracking_link(
        db, accurate_po_id="95106", actor_email="admin@eternal.local"
    )
    payload = tracking.public_tracking_view(
        db,
        customer_slug=result["customer_slug"],
        po_slug=result["po_slug"],
        raw_token=result["token"],
    )
    receipt = next(d for d in payload["documents"] if d["category"] == "sales_receipt")
    assert receipt["items"] == []
