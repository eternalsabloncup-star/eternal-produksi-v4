"""Focused security tests — Live Tracking Approval Design deep-link (Option B).

Isolated Mongo only. No production data/tokens.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.database import Database

from app.config import get_settings
from app.domain.tracking_milestones import slugify_customer, slugify_po_reference
from app.repositories import design_repository as design
from app.repositories import tracking_link_repository as track_links
from app.repositories.po_repository import COLLECTION_NAME as PO_COLLECTION
from app.repositories.po_repository import get_by_accurate_id
from app.services import design_service
from app.services import tracking_service as tracking
from app.services.design_link_crypto import (
    DesignLinkCryptoError,
    decrypt_approval_token,
    encrypt_approval_token,
    parse_recovery_key,
)
from app.services.tracking_service import TrackingNotFound

SAMPLE_PO_ID = "95106"
SAMPLE_PO_NO = "PO/18/11/2026/000010"
DESIGNER = {
    "email_normalized": "designer@test.local",
    "role": "designer",
    "operational_role": "designer",
}


@pytest.fixture()
def recovery_key_b64(monkeypatch: pytest.MonkeyPatch) -> str:
    key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv("V4_DESIGN_APPROVAL_LINK_RECOVERY_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def _sync_po(db: Database) -> dict:
    """Minimal PO fixture — tidak bergantung Accurate sync / branch gate."""
    doc = {
        "accurate_id": SAMPLE_PO_ID,
        "po_no": SAMPLE_PO_NO,
        "origin": "accurate",
        "branch_id": "HQ",
        "header": {
            "accurate_id": SAMPLE_PO_ID,
            "po_no": SAMPLE_PO_NO,
            "customer": "PT Contoh Kemasan",
            "po_date": "2026-11-10",
            "ship_date": "2026-11-25",
            "items": [
                {
                    "item_name": "Cup Uji",
                    "item_code": "CUP-U",
                    "qty": 1000,
                    "unit": "PCS",
                    "process_kind": "Cetak",
                    "color": "Putih",
                }
            ],
        },
    }
    db[PO_COLLECTION].replace_one({"accurate_id": SAMPLE_PO_ID}, doc, upsert=True)
    found = get_by_accurate_id(db, SAMPLE_PO_ID)
    assert found is not None
    return found


def _confirm_v1(db: Database, po_no: str) -> None:
    design.create_submission(
        db,
        accurate_po_id=SAMPLE_PO_ID,
        po_no=po_no,
        evidence_id="evid:test:1",
        filename="design.pdf",
        submitted_by="designer@test.local",
    )
    design.confirm_submission(
        db,
        accurate_po_id=SAMPLE_PO_ID,
        version=1,
        confirmed_by="designer@test.local",
    )


def _make_tracking_link(db: Database, po_doc: dict) -> tuple[str, dict]:
    header = po_doc.get("header") or {}
    customer = str(header.get("customer") or "Pelanggan")
    po_no = str(po_doc.get("po_no") or header.get("po_no") or "")
    return track_links.create_link(
        db,
        accurate_po_id=str(po_doc["accurate_id"]),
        po_no=po_no,
        customer_name=customer,
        customer_slug=slugify_customer(customer),
        po_slug=slugify_po_reference(po_no),
        actor_email="ops@test.local",
        scopes=["operational"],
    )


def test_crypto_roundtrip_and_aad_binding(recovery_key_b64: str) -> None:
    key = parse_recovery_key(recovery_key_b64)
    assert key is not None
    ct = encrypt_approval_token(
        "raw-token-abc", accurate_po_id=SAMPLE_PO_ID, design_version=1, key=key
    )
    assert (
        decrypt_approval_token(
            ct, accurate_po_id=SAMPLE_PO_ID, design_version=1, key=key
        )
        == "raw-token-abc"
    )
    with pytest.raises(DesignLinkCryptoError):
        decrypt_approval_token(ct, accurate_po_id="other-po", design_version=1, key=key)
    with pytest.raises(DesignLinkCryptoError):
        decrypt_approval_token(
            ct, accurate_po_id=SAMPLE_PO_ID, design_version=2, key=key
        )


def test_tampered_ciphertext_fail_closed(recovery_key_b64: str) -> None:
    key = parse_recovery_key(recovery_key_b64)
    assert key is not None
    ct = encrypt_approval_token(
        "tok", accurate_po_id=SAMPLE_PO_ID, design_version=1, key=key
    )
    bad = ("A" if ct[0] != "A" else "B") + ct[1:]
    with pytest.raises(DesignLinkCryptoError):
        decrypt_approval_token(
            bad, accurate_po_id=SAMPLE_PO_ID, design_version=1, key=key
        )


def test_missing_key_fail_closed(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_DESIGN_APPROVAL_LINK_RECOVERY_KEY", "")
    get_settings.cache_clear()
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    raw, link = design.create_approval_link(
        db, accurate_po_id=SAMPLE_PO_ID, design_version=1, created_by="d"
    )
    assert "token_ciphertext" not in link
    elig = design_service.build_design_approval_eligibility(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert elig["eligible"] is False
    with pytest.raises(design_service.DesignLinkRecoveryError):
        design_service.recover_approval_token_for_po(db, accurate_po_id=SAMPLE_PO_ID)
    assert design.find_valid_link(db, raw) is not None
    get_settings.cache_clear()


def test_legacy_hash_only_not_recoverable(
    db: Database, recovery_key_b64: str
) -> None:
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    raw, _link = design.create_approval_link(
        db, accurate_po_id=SAMPLE_PO_ID, design_version=1, created_by="d"
    )
    assert design.find_valid_link(db, raw) is not None
    elig = design_service.build_design_approval_eligibility(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert elig["eligible"] is False
    assert elig["link_status"] == "unavailable"
    with pytest.raises(design_service.DesignLinkRecoveryError):
        design_service.recover_approval_token_for_po(db, accurate_po_id=SAMPLE_PO_ID)


def test_create_link_stores_ciphertext_and_strips_secrets(
    db: Database, recovery_key_b64: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    monkeypatch.setattr(design_service, "require_action", lambda *_a, **_k: None)
    result = design_service.create_customer_link(
        db, DESIGNER, accurate_po_id=SAMPLE_PO_ID
    )
    assert result["token"]
    assert "token_hash" not in result["link"]
    assert "token_ciphertext" not in result["link"]
    stored = design.links_collection(db).find_one({"accurate_po_id": SAMPLE_PO_ID})
    assert stored is not None
    assert stored.get("token_ciphertext")
    assert design.hash_token(result["token"]) == stored["token_hash"]
    elig = design_service.build_design_approval_eligibility(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert elig["eligible"] is True
    assert elig["pending_approval"] is True
    assert elig["design_version"] == 1
    assert elig["link_status"] == "active"
    assert result["token"] not in str(elig)


def test_no_link_no_eligibility(db: Database, recovery_key_b64: str) -> None:
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    elig = design_service.build_design_approval_eligibility(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert elig["eligible"] is False


def test_approved_not_eligible(
    db: Database, recovery_key_b64: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    monkeypatch.setattr(design_service, "require_action", lambda *_a, **_k: None)
    created = design_service.create_customer_link(
        db, DESIGNER, accurate_po_id=SAMPLE_PO_ID
    )
    design.record_approval(
        db,
        accurate_po_id=SAMPLE_PO_ID,
        design_version=1,
        signer_name="Cust",
        signature_evidence_id="sig1",
        token_hash=design.hash_token(created["token"]),
    )
    elig = design_service.build_design_approval_eligibility(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert elig["eligible"] is False
    assert elig["link_status"] == "approved"


def test_expired_link_not_eligible(db: Database, recovery_key_b64: str) -> None:
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    key = parse_recovery_key(recovery_key_b64)
    assert key is not None
    raw = "expired-token-raw-value-xxxxx"
    ct = encrypt_approval_token(
        raw, accurate_po_id=SAMPLE_PO_ID, design_version=1, key=key
    )
    now = datetime.now(timezone.utc)
    design.links_collection(db).insert_one(
        {
            "token_hash": design.hash_token(raw),
            "token_ciphertext": ct,
            "accurate_po_id": SAMPLE_PO_ID,
            "design_version": 1,
            "created_by": "d",
            "created_at": now - timedelta(hours=80),
            "expires_at": now - timedelta(hours=1),
            "used_at": None,
            "revoked_at": None,
        }
    )
    elig = design_service.build_design_approval_eligibility(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert elig["eligible"] is False


def test_revoked_link_not_eligible(db: Database, recovery_key_b64: str) -> None:
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    key = parse_recovery_key(recovery_key_b64)
    assert key is not None
    raw = "revoked-token-raw-value-xxxxx"
    ct = encrypt_approval_token(
        raw, accurate_po_id=SAMPLE_PO_ID, design_version=1, key=key
    )
    now = datetime.now(timezone.utc)
    design.links_collection(db).insert_one(
        {
            "token_hash": design.hash_token(raw),
            "token_ciphertext": ct,
            "accurate_po_id": SAMPLE_PO_ID,
            "design_version": 1,
            "created_by": "d",
            "created_at": now,
            "expires_at": now + timedelta(hours=72),
            "used_at": None,
            "revoked_at": now,
        }
    )
    elig = design_service.build_design_approval_eligibility(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert elig["eligible"] is False


def test_obsolete_version_not_selected(
    db: Database, recovery_key_b64: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    po = _sync_po(db)
    po_no = str(po["po_no"])
    monkeypatch.setattr(design_service, "require_action", lambda *_a, **_k: None)
    design.create_submission(
        db,
        accurate_po_id=SAMPLE_PO_ID,
        po_no=po_no,
        evidence_id="e1",
        filename="v1.pdf",
        submitted_by="d",
    )
    design.confirm_submission(
        db, accurate_po_id=SAMPLE_PO_ID, version=1, confirmed_by="d"
    )
    v1 = design_service.create_customer_link(db, DESIGNER, accurate_po_id=SAMPLE_PO_ID)
    design.create_submission(
        db,
        accurate_po_id=SAMPLE_PO_ID,
        po_no=po_no,
        evidence_id="e2",
        filename="v2.pdf",
        submitted_by="d",
    )
    design.confirm_submission(
        db, accurate_po_id=SAMPLE_PO_ID, version=2, confirmed_by="d"
    )
    v2 = design_service.create_customer_link(db, DESIGNER, accurate_po_id=SAMPLE_PO_ID)
    elig = design_service.build_design_approval_eligibility(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert elig["design_version"] == 2
    recovered, link = design_service.recover_approval_token_for_po(
        db, accurate_po_id=SAMPLE_PO_ID
    )
    assert recovered == v2["token"]
    assert recovered != v1["token"]
    assert int(link["design_version"]) == 2


def test_tracking_payload_no_approval_token_leak(
    db: Database, recovery_key_b64: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    monkeypatch.setattr(design_service, "require_action", lambda *_a, **_k: None)
    created = design_service.create_customer_link(
        db, DESIGNER, accurate_po_id=SAMPLE_PO_ID
    )
    raw_track, link = _make_tracking_link(db, po)
    payload = tracking.public_tracking_view(
        db,
        customer_slug=link["customer_slug"],
        po_slug=link["po_slug"],
        raw_token=raw_track,
    )
    blob = str(payload)
    assert created["token"] not in blob
    assert "token_ciphertext" not in blob
    assert payload["design_approval"]["eligible"] is True
    assert "token" not in payload["design_approval"]


def test_resolve_success_and_slug_mismatch_deny(
    db: Database, recovery_key_b64: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    monkeypatch.setattr(design_service, "require_action", lambda *_a, **_k: None)
    created = design_service.create_customer_link(
        db, DESIGNER, accurate_po_id=SAMPLE_PO_ID
    )
    raw_track, link = _make_tracking_link(db, po)
    resolved = tracking.resolve_public_design_approval_path(
        db,
        customer_slug=link["customer_slug"],
        po_slug=link["po_slug"],
        raw_token=raw_track,
    )
    assert resolved["path"] == f"/approval/{created['token']}"
    with pytest.raises(TrackingNotFound):
        tracking.resolve_public_design_approval_path(
            db,
            customer_slug="wrong-customer",
            po_slug=link["po_slug"],
            raw_token=raw_track,
        )
    with pytest.raises(TrackingNotFound):
        tracking.resolve_public_design_approval_path(
            db,
            customer_slug=link["customer_slug"],
            po_slug="wrong-po",
            raw_token=raw_track,
        )


def test_invalid_tracking_token_deny(db: Database, recovery_key_b64: str) -> None:
    _sync_po(db)
    with pytest.raises(TrackingNotFound):
        tracking.resolve_public_design_approval_path(
            db,
            customer_slug="x",
            po_slug="y",
            raw_token="not-a-real-tracking-token",
        )


def test_http_resolve_no_store_and_existing_approval_flow(
    app_client, db: Database, recovery_key_b64: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approval tetap hanya lewat POST existing; resolve read-only."""
    po = _sync_po(db)
    _confirm_v1(db, str(po["po_no"]))
    monkeypatch.setattr(design_service, "require_action", lambda *_a, **_k: None)
    created = design_service.create_customer_link(
        db, DESIGNER, accurate_po_id=SAMPLE_PO_ID
    )
    raw_track, link = _make_tracking_link(db, po)
    pub = app_client.get(
        f"/api/public/tracking/{link['customer_slug']}/{link['po_slug']}/{raw_track}"
    )
    assert pub.status_code == 200
    assert created["token"] not in pub.text
    assert pub.json()["design_approval"]["eligible"] is True
    assert pub.headers.get("cache-control", "").startswith("no-store")

    resolved = app_client.get(
        f"/api/public/tracking/{link['customer_slug']}/{link['po_slug']}/{raw_track}/design-approval"
    )
    assert resolved.status_code == 200
    assert resolved.headers.get("cache-control", "").startswith("no-store")
    assert resolved.json()["path"] == f"/approval/{created['token']}"

    # Existing public approval context still works with raw token.
    ctx = app_client.get(f"/api/public/design-approval/{created['token']}")
    assert ctx.status_code == 200
    assert ctx.json()["already_approved"] is False
