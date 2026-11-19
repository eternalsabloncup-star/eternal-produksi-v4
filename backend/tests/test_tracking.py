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
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.services import tracking_service as tracking
from app.services.po_sync import sync_sales_order_payload
from app.services.tracking_progress import compute_progress, evaluate_milestones
from app.services.tracking_workflow_adapter import load_workflow_snapshot


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


def _sync_po(db: Database) -> dict:
    return sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)


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
    # Faktur terkunci di publik
    invoice = next(d for d in payload["documents"] if d["category"] == "sales_invoice")
    assert invoice["status"] == "locked"
    receipt = next(d for d in payload["documents"] if d["category"] == "sales_receipt")
    assert receipt["status"] == "locked"
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
    sync_sales_order_payload(db, payload)
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


def test_order_complete_requires_shipped_qty(db: Database) -> None:
    _sync_po(db)
    from app.repositories.po_repository import get_by_accurate_id

    po = get_by_accurate_id(db, "95106")
    assert po is not None
    db.create_collection("delivery_notes")
    # Partial SJ — jangan double count revisi
    db["delivery_notes"].insert_many(
        [
            {
                "accurate_po_id": "95106",
                "sj_no": "SJ-1",
                "accurate_delivery_note_id": "dn-1",
                "qty_shipped_net": 400,
                "status": "delivered",
            },
            {
                "accurate_po_id": "95106",
                "sj_no": "SJ-1-REV",
                "accurate_delivery_note_id": "dn-1b",
                "qty_shipped_net": 400,
                "is_superseded": True,
            },
            {
                "accurate_po_id": "95106",
                "sj_no": "SJ-2",
                "accurate_delivery_note_id": "dn-2",
                "qty_shipped_net": 200,
                "status": "delivered",
                "soft_deleted": True,  # harus diabaikan adapter
            },
        ]
    )
    wf = load_workflow_snapshot(db, "95106")
    # soft_deleted filtered → only 400 from dn-1 (dn-1b superseded, dn-2 soft deleted)
    assert len(wf["delivery_notes"]) == 2  # superseded still in list but qty helper skips
    milestones = evaluate_milestones(po, wf)
    progress = compute_progress(milestones, po, wf)
    assert progress["shipping"]["qty_shipped"] == 400
    assert progress["order_complete"] is False

    # Full ship
    db["delivery_notes"].insert_one(
        {
            "accurate_po_id": "95106",
            "sj_no": "SJ-3",
            "accurate_delivery_note_id": "dn-3",
            "qty_shipped_net": 3600,  # sample total qty ~4000? check sample
            "status": "delivered",
        }
    )
    # Recalculate with enough qty
    from app.services.tracking_progress import qty_po_total

    total = qty_po_total(po)
    db["delivery_notes"].delete_many({})
    db["delivery_notes"].insert_one(
        {
            "accurate_po_id": "95106",
            "sj_no": "SJ-FULL",
            "accurate_delivery_note_id": "dn-full",
            "qty_shipped_net": total,
            "status": "delivered",
        }
    )
    wf2 = load_workflow_snapshot(db, "95106")
    m2 = evaluate_milestones(po, wf2)
    p2 = compute_progress(m2, po, wf2)
    assert p2["order_complete"] is True
    assert p2["shipping"]["qty_shipped"] == total


def test_design_gate_maps_approval_milestone(db: Database) -> None:
    _sync_po(db)
    from app.repositories.po_repository import get_by_accurate_id

    po = get_by_accurate_id(db, "95106")
    assert po is not None
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
    # financial docs unlocked for internal but still waiting (no Accurate invoice yet)
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
