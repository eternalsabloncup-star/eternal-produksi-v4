"""Reveal URL Live Tracking — salin ulang tanpa regenerate / tanpa plaintext token."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.repositories.tracking_link_repository import hash_token
from tests.test_tracking import _login, _seed_admin, _sync_po


def test_generate_then_reveal_same_url_twice(
    app_client: TestClient, db: Database
) -> None:
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
    path = body["path"]
    token = body["token"]
    assert path.endswith(token)

    stored = db["tracking_links"].find_one({"accurate_po_id": "95106", "status": "active"})
    assert stored is not None
    assert stored.get("token_sealed")
    assert token not in str(stored.get("token_sealed"))
    assert stored["token_hash"] == hash_token(token)
    # No plaintext token field.
    assert "token" not in stored
    assert "raw_token" not in stored

    listed = app_client.get("/api/tracking/95106/links")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["url_recoverable"] is True
    assert "token_sealed" not in item
    assert "token_hash" not in item
    assert "token" not in item

    first = app_client.get("/api/tracking/95106/links/active-url")
    assert first.status_code == 200, first.text
    assert first.json()["path"] == path
    assert first.headers.get("cache-control", "").startswith("no-store")

    second = app_client.get("/api/tracking/95106/links/active-url")
    assert second.status_code == 200
    assert second.json()["path"] == path
    # Still one active link — no double generate.
    assert db["tracking_links"].count_documents({"accurate_po_id": "95106", "status": "active"}) == 1

    # Public token validation still works.
    pub = app_client.get(
        f"/api/public/tracking/{body['customer_slug']}/{body['po_slug']}/{token}"
    )
    assert pub.status_code == 200

    # Audit reveal must not contain raw token.
    audits = list(
        db["audit_logs"].find({"action": "tracking.link.reveal_url"}).sort("created_at", -1)
    )
    assert audits
    for row in audits:
        blob = str(row)
        assert token not in blob
        assert "token_sealed" not in blob


def test_reveal_revoked_and_expired_rejected(
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

    revoke = app_client.post(
        f"/api/tracking/links/{gen['link']['id']}/revoke",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "test"},
    )
    assert revoke.status_code == 200
    gone = app_client.get("/api/tracking/95106/links/active-url")
    assert gone.status_code == 404

    # Fresh link then expire
    gen2 = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    db["tracking_links"].update_one(
        {"_id": __import__("bson").ObjectId(gen2["link"]["id"])},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}},
    )
    expired = app_client.get("/api/tracking/95106/links/active-url")
    assert expired.status_code == 404


def test_reveal_forbidden_without_ops_permission(
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
    app_client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})

    denied = app_client.get("/api/tracking/95106/links/active-url")
    assert denied.status_code in (401, 403)


def test_legacy_hash_only_requires_explicit_recovery(
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

    # Simulate pre-seal historical doc.
    db["tracking_links"].update_one(
        {"_id": __import__("bson").ObjectId(gen["link"]["id"])},
        {"$unset": {"token_sealed": ""}},
    )

    reveal = app_client.get("/api/tracking/95106/links/active-url")
    assert reveal.status_code == 409, reveal.text
    detail = reveal.json()["detail"]
    assert detail["code"] == "legacy_unrecoverable"
    assert detail["requires_explicit_recovery"] is True

    # Still one active — no auto regenerate.
    assert db["tracking_links"].count_documents(
        {"accurate_po_id": "95106", "status": "active"}
    ) == 1

    # Explicit regenerate recovers.
    regen = app_client.post(
        "/api/tracking/95106/links/regenerate",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert regen.status_code == 200, regen.text
    new_path = regen.json()["path"]
    assert new_path != gen["path"]

    ok = app_client.get("/api/tracking/95106/links/active-url")
    assert ok.status_code == 200
    assert ok.json()["path"] == new_path
    # Old token dead.
    old_pub = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
    )
    assert old_pub.status_code == 404
