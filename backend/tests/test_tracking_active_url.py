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

    # Explicit regenerate recovers only when expected link id matches.
    regen = app_client.post(
        "/api/tracking/95106/links/regenerate",
        headers={"X-CSRF-Token": csrf},
        json={"expected_active_link_id": gen["link"]["id"]},
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


def test_legacy_recovery_rejects_stale_expected_link_id(
    app_client: TestClient, db: Database
) -> None:
    """Race: operator confirms legacy A, but active becomes B → conflict, no revoke of B."""
    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    legacy_id = gen["link"]["id"]
    db["tracking_links"].update_one(
        {"_id": __import__("bson").ObjectId(legacy_id)},
        {"$unset": {"token_sealed": ""}},
    )

    # Another actor regenerates first (new sealed active).
    other = app_client.post(
        "/api/tracking/95106/links/regenerate",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert other.status_code == 200, other.text
    new_id = other.json()["link"]["id"]
    new_path = other.json()["path"]
    assert new_id != legacy_id

    stale = app_client.post(
        "/api/tracking/95106/links/regenerate",
        headers={"X-CSRF-Token": csrf},
        json={"expected_active_link_id": legacy_id},
    )
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail["code"] == "conflict"
    assert "berubah" in detail["message"].lower() or "dibatalkan" in detail["message"].lower()

    # New active link intact.
    assert (
        db["tracking_links"].count_documents(
            {"_id": __import__("bson").ObjectId(new_id), "status": "active"}
        )
        == 1
    )
    still = app_client.get("/api/tracking/95106/links/active-url")
    assert still.status_code == 200
    assert still.json()["path"] == new_path


def test_seal_key_preflight_and_wrong_key_fail_closed(
    app_client: TestClient, db: Database, monkeypatch
) -> None:
    from cryptography.fernet import Fernet

    from app.config import clear_settings_cache
    from app.services.tracking_token_seal import (
        clear_seal_cache,
        preflight_tracking_seal_key,
        unseal_raw_token,
    )

    _sync_po(db)
    _seed_admin(db)
    csrf = _login(app_client)

    key_a = Fernet.generate_key().decode()
    monkeypatch.setenv("V4_TRACKING_LINK_SEAL_KEY", key_a)
    clear_settings_cache()
    clear_seal_cache()

    pf = preflight_tracking_seal_key()
    assert pf["configured"] is True
    assert pf["key_format_ok"] is True
    assert pf["roundtrip_ok"] is True
    assert "key" not in str(pf).lower() or "configured" in str(pf)
    # Never embed secret material.
    assert key_a not in str(pf)

    health = app_client.get("/api/health")
    assert health.status_code == 200
    seal = health.json()["tracking_seal"]
    assert seal["roundtrip_ok"] is True
    assert key_a not in health.text

    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert gen.status_code == 201, gen.text
    stored = db["tracking_links"].find_one({"accurate_po_id": "95106", "status": "active"})
    assert stored and stored.get("token_sealed")

    # Wrong/missing key → cannot unseal (recovery = restore correct key, not regenerate auto).
    key_b = Fernet.generate_key().decode()
    monkeypatch.setenv("V4_TRACKING_LINK_SEAL_KEY", key_b)
    clear_settings_cache()
    clear_seal_cache()
    try:
        unseal_raw_token(stored["token_sealed"])
        raise AssertionError("expected TrackingSealError")
    except Exception as exc:
        assert "TRACKING_SEAL" in getattr(exc, "code", "") or "kunci" in str(exc).lower() or "Ciphertext" in str(exc)

    reveal = app_client.get("/api/tracking/95106/links/active-url")
    assert reveal.status_code in (500, 503)
    assert key_b not in reveal.text
