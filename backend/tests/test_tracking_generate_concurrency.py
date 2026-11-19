"""Generate Live Tracking — conflict guard + concurrency with manual unique index."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import tracking_link_repository as links
from app.repositories.tracking_link_repository import (
    MANUAL_UNIQ_ACTIVE_PER_PO_INDEX,
    TrackingActiveLinkExists,
    clear_tracking_index_verification_cache,
    count_active_for_po,
    create_link,
    ensure_tracking_indexes,
)
from app.services.po_sync import sync_sales_order_payload


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
    return response.json()["csrf_token"]


def _sync_po(db: Database) -> None:
    sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)


def _ensure_manual_uniq_active_index(db: Database) -> None:
    """Test-only. Production must create this index manually after duplicate audit."""
    spec = MANUAL_UNIQ_ACTIVE_PER_PO_INDEX
    try:
        db[spec["collection"]].drop_index(spec["name"])
    except Exception:
        pass
    db[spec["collection"]].create_index(
        spec["keys"],
        name=spec["name"],
        unique=bool(spec["unique"]),
        partialFilterExpression=spec["partialFilterExpression"],
    )


def test_second_generate_returns_409(app_client: TestClient, db: Database) -> None:
    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)
    first = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert first.status_code == 201, first.text
    second = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert second.status_code == 409, second.text
    assert "sudah ada" in second.json()["detail"].lower()
    assert count_active_for_po(db, "95106") == 1


def test_create_link_rejects_when_active_exists(db: Database) -> None:
    ensure_tracking_indexes(db)
    clear_tracking_index_verification_cache(db)
    create_link(
        db,
        accurate_po_id="po-seq-1",
        po_no="PO/1",
        customer_name="A",
        customer_slug="a",
        po_slug="po-1",
        actor_email="a@test",
    )
    try:
        create_link(
            db,
            accurate_po_id="po-seq-1",
            po_no="PO/1",
            customer_name="A",
            customer_slug="a",
            po_slug="po-1",
            actor_email="a@test",
        )
        raise AssertionError("expected TrackingActiveLinkExists")
    except TrackingActiveLinkExists:
        pass
    assert count_active_for_po(db, "po-seq-1") == 1


def test_concurrent_generate_one_active_with_manual_unique_index(db: Database) -> None:
    """Hard concurrency PASS only when manual unique partial index is present."""
    from app.services import tracking_service as tracking

    _sync_po(db)
    ensure_tracking_indexes(db)
    _ensure_manual_uniq_active_index(db)
    clear_tracking_index_verification_cache(db)

    outcomes: list[str] = []

    def _gen() -> str:
        try:
            tracking.generate_tracking_link(
                db, accurate_po_id="95106", actor_email="race@test"
            )
            return "created"
        except tracking.TrackingConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_gen) for _ in range(8)]
        for fut in as_completed(futs):
            outcomes.append(fut.result())

    assert outcomes.count("created") == 1, outcomes
    assert outcomes.count("conflict") == 7, outcomes
    assert count_active_for_po(db, "95106") == 1


def test_manual_uniq_index_constant_not_in_required() -> None:
    names = {s["name"] for s in links.REQUIRED_TRACKING_INDEX_SPECS}
    assert MANUAL_UNIQ_ACTIVE_PER_PO_INDEX["name"] not in names
