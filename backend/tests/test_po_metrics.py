"""KPI Inbox Accurate — agregasi seluruh PO (bukan sample ≤500)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.repositories.po_repository import (
    COLLECTION_NAME,
    aggregate_inbox_metrics,
)


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


def _seed_many_pos(db: Database, count: int = 501) -> None:
    now = datetime.now(timezone.utc)
    docs = []
    for i in range(count):
        docs.append(
            {
                "accurate_id": str(20000 + i),
                "source_po_id": f"accurate:{20000 + i}",
                "po_no": f"PO/METRICS/{i:04d}",
                "origin": "accurate",
                "header": {
                    "po_no": f"PO/METRICS/{i:04d}",
                    "customer": f"Cust {i}",
                    "urgent": i % 10 == 0,
                    "items": [
                        {
                            "line_id": f"L{i}",
                            "item_code": f"SKU-{i}",
                            "item_name": f"Item {i}",
                            "qty": 2,
                            "unit": "PCS",
                        }
                    ],
                },
                "line_ids": [f"L{i}"],
                "operational": {},
                "created_at": now,
                "updated_at": now,
            }
        )
    db[COLLECTION_NAME].insert_many(docs)


def test_aggregate_inbox_metrics_covers_more_than_500(db: Database) -> None:
    _seed_many_pos(db, 501)
    metrics = aggregate_inbox_metrics(db, origin="accurate")
    assert metrics["total"] == 501
    assert metrics["baru"] == 501
    assert metrics["qty"] == 1002
    assert metrics["lines"] == 501
    # 0,10,...,500 → 51 urgent
    assert metrics["urgent"] == 51
    assert metrics["printed"] == 0


def test_metrics_endpoint_covers_more_than_500(
    app_client: TestClient, db: Database
) -> None:
    _seed_many_pos(db, 501)
    _seed_user(db)
    _login(app_client)

    res = app_client.get("/api/po/metrics")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "mongodb"
    assert body["origin"] == "accurate"
    assert body["total"] == 501
    assert body["qty"] == 1002
    assert body["lines"] == 501
    assert body["urgent"] == 51
    assert body["baru"] == 501
    assert body["printed"] == 0

    # List sample tetap terbatas; total count tetap 501.
    listed = app_client.get("/api/po", params={"page": 1, "pageSize": 50})
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["total"] == 501
    assert len(listed_body["items"]) == 50
    assert listed_body["supportedFilters"] == ["q", "urgent"]


def test_metrics_requires_session(app_client: TestClient) -> None:
    res = app_client.get("/api/po/metrics")
    assert res.status_code == 401


def test_metrics_route_not_swallowed_by_po_key(
    app_client: TestClient, db: Database
) -> None:
    _seed_user(db)
    _login(app_client)
    res = app_client.get("/api/po/metrics")
    assert res.status_code == 200
    assert res.json()["total"] == 0
