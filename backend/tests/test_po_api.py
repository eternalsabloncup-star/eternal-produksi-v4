"""API list/detail PO dari Mongo V4 (bukan Accurate live)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.services.po_sync import sync_sales_order_payload


def _seed_user(db: Database) -> None:
    create_admin_user(
        db,
        email="admin@eternal.local",
        display_name="Admin",
        password="password-kuat-123",
        allow_if_admin_exists=True,
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "password-kuat-123"},
    )
    assert response.status_code == 200, response.text


def test_list_and_detail_include_items_and_process(
    app_client: TestClient, db: Database
) -> None:
    sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)
    _seed_user(db)
    _login(app_client)

    listed = app_client.get("/api/po?page=1&pageSize=10")
    assert listed.status_code == 200
    body = listed.json()
    assert body["source"] == "mongodb"
    assert body["origin"] == "accurate"
    assert body["total"] == 1
    assert len(body["items"]) == 1
    row = body["items"][0]
    assert row["accurate_id"] == "95106"
    assert row["po_no"] == "PO/18/11/2026/000010"
    assert row["item_count"] == 2
    assert isinstance(row["header"]["items"], list)
    kinds = [i["process_kind"] for i in row["header"]["items"]]
    assert "Cetak" in kinds
    assert "Non-Cetak" in kinds

    detail = app_client.get("/api/po/95106")
    assert detail.status_code == 200
    d = detail.json()
    assert d["accurate_id"] == "95106"
    assert len(d["header"]["items"]) == 2

    # Path converter: slash di nomor PO tidak di-encode %2F.
    by_po = app_client.get("/api/po/PO/18/11/2026/000010")
    assert by_po.status_code == 200
    assert by_po.json()["accurate_id"] == "95106"


def test_list_search_and_pagination(app_client: TestClient, db: Database) -> None:
    sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)
    _seed_user(db)
    _login(app_client)

    hit = app_client.get("/api/po", params={"q": "95106", "page": 1, "pageSize": 1})
    assert hit.status_code == 200
    assert hit.json()["total"] == 1
    assert hit.json()["supportedFilters"] == ["q", "urgent"]

    miss = app_client.get("/api/po", params={"q": "TIDAK-ADA-PO"})
    assert miss.status_code == 200
    assert miss.json()["total"] == 0
    assert miss.json()["items"] == []


def test_list_urgent_filter_affects_results(
    app_client: TestClient, db: Database
) -> None:
    sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)
    # Sample default tidak urgent — pastikan filter urgent=true mengosongkan.
    _seed_user(db)
    _login(app_client)

    none_urgent = app_client.get("/api/po", params={"urgent": "true"})
    assert none_urgent.status_code == 200
    assert none_urgent.json()["total"] == 0

    # Set flag urgent nyata di header Accurate.
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "95106"},
        {"$set": {"header.urgent": True}},
    )
    hit = app_client.get("/api/po", params={"urgent": "true"})
    assert hit.status_code == 200
    assert hit.json()["total"] == 1
    assert hit.json()["items"][0]["accurate_id"] == "95106"


def test_list_requires_session(app_client: TestClient) -> None:
    res = app_client.get("/api/po")
    assert res.status_code == 401


def test_path_route_does_not_swallow_list(
    app_client: TestClient, db: Database
) -> None:
    """GET /api/po tetap list; detail slash tidak menelan endpoint list."""
    sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)
    _seed_user(db)
    _login(app_client)

    listed = app_client.get("/api/po")
    assert listed.status_code == 200
    assert listed.json()["source"] == "mongodb"
    assert "items" in listed.json()

    detail = app_client.get("/api/po/PO/18/11/2026/000010")
    assert detail.status_code == 200
    assert detail.json()["accurate_id"] == "95106"

    missing = app_client.get("/api/po/sync-payload")
    assert missing.status_code == 404
