"""Regresi: request tracking tidak createIndex; missing/wrong-spec → fail-closed."""

from __future__ import annotations

import os

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from app.repositories import tracking_link_repository as links
from app.repositories.tracking_link_repository import (
    MissingRequiredTrackingIndexError,
    REQUIRED_TRACKING_INDEX_SPECS,
    clear_tracking_index_verification_cache,
    create_link,
    ensure_tracking_indexes,
    inspect_tracking_index_specs,
    require_tracking_indexes,
    tracking_index_cache_key,
)


def test_ensure_tracking_indexes_is_explicit_maintenance(db: Database) -> None:
    clear_tracking_index_verification_cache(db)
    # Drop unique token index then recreate via maintenance only.
    try:
        db["tracking_links"].drop_index("uniq_tracking_token_hash")
    except Exception:
        pass
    clear_tracking_index_verification_cache(db)
    created = ensure_tracking_indexes(db)
    assert "uniq_tracking_token_hash" in created
    report = inspect_tracking_index_specs(db)
    assert report["ok"] is True
    assert report["writes"] is False


def test_require_fails_when_index_missing(db: Database) -> None:
    clear_tracking_index_verification_cache(db)
    db["tracking_links"].drop_index("uniq_tracking_token_hash")
    clear_tracking_index_verification_cache(db)
    with pytest.raises(MissingRequiredTrackingIndexError) as exc:
        require_tracking_indexes(db)
    assert "uniq_tracking_token_hash" in exc.value.missing


def test_require_fails_when_unique_spec_wrong(db: Database) -> None:
    clear_tracking_index_verification_cache(db)
    db["tracking_links"].drop_index("uniq_tracking_token_hash")
    # Indeks sama nama/keys tetapi TIDAK unique → conflict.
    db["tracking_links"].create_index(
        [("token_hash", 1)], name="uniq_tracking_token_hash", unique=False
    )
    clear_tracking_index_verification_cache(db)
    with pytest.raises(MissingRequiredTrackingIndexError) as exc:
        require_tracking_indexes(db)
    assert any(c["name"] == "uniq_tracking_token_hash" for c in exc.value.conflict)
    # Restore correct unique index for later fixtures in same DB lifecycle.
    db["tracking_links"].drop_index("uniq_tracking_token_hash")
    ensure_tracking_indexes(db)


def test_create_link_request_path_never_calls_ensure_or_create_index(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_tracking_indexes(db)
    clear_tracking_index_verification_cache(db)

    ensure_calls: list[str] = []
    create_calls: list[str] = []
    coll_cls = type(db["tracking_links"])
    original_create = coll_cls.create_index

    def _blocked_ensure(_db: Database) -> list[str]:
        ensure_calls.append("ensure")
        raise AssertionError("ensure_tracking_indexes must not run on request path")

    def _counting_create(self, *args, **kwargs):  # noqa: ANN001
        create_calls.append(str(kwargs.get("name") or args))
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(links, "ensure_tracking_indexes", _blocked_ensure)
    monkeypatch.setattr(coll_cls, "create_index", _counting_create, raising=True)

    raw, doc = create_link(
        db,
        accurate_po_id="95106",
        po_no="PO/TEST",
        customer_name="Test",
        customer_slug="test",
        po_slug="po-test",
        actor_email="admin@eternal.local",
    )
    assert raw
    assert doc["status"] == "active"
    assert ensure_calls == []
    assert create_calls == []


def test_create_link_fails_closed_without_indexes(db: Database) -> None:
    clear_tracking_index_verification_cache(db)
    for spec in REQUIRED_TRACKING_INDEX_SPECS:
        try:
            db[spec["collection"]].drop_index(spec["name"])
        except Exception:
            pass
    clear_tracking_index_verification_cache(db)
    with pytest.raises(MissingRequiredTrackingIndexError):
        create_link(
            db,
            accurate_po_id="95106",
            po_no="PO/TEST",
            customer_name="Test",
            customer_slug="test",
            po_slug="po-test",
            actor_email="admin@eternal.local",
        )
    # Restore so other tests sharing DB stay healthy if order changes.
    ensure_tracking_indexes(db)


def test_index_cache_isolated_across_same_db_name_different_clients(
    mongo_client: MongoClient,
) -> None:
    """Dua client/target berbeda dengan nama DB sama tidak berbagi cache verifikasi."""
    from pymongo import MongoClient as PMC

    import app.repositories.tracking_link_repository as links

    clear_tracking_index_verification_cache()
    name = "eternal_v4_cache_iso_shared_name"

    db_a = mongo_client[name]
    ensure_tracking_indexes(db_a)
    key_a = tracking_index_cache_key(db_a)
    assert key_a in links._VERIFIED_SPECS

    # Client kedua ke host yang sama → cid berbeda → kunci berbeda.
    uri = os.environ["MONGODB_URI"]
    client_b = PMC(uri, serverSelectionTimeoutMS=3000)
    try:
        db_b = client_b[name]
        key_b = tracking_index_cache_key(db_b)
        assert key_a != key_b
        assert key_b not in links._VERIFIED_SPECS
        # Tanpa ensure pada client B, require harus listIndexes ulang (boleh sukses
        # karena indeks fisik sudah ada) lalu mengisi cache B sendiri.
        require_tracking_indexes(db_b)
        assert key_b in links._VERIFIED_SPECS
        assert links._VERIFIED_SPECS[key_a] == links._VERIFIED_SPECS[key_b]
    finally:
        client_b.close()
        clear_tracking_index_verification_cache()
        mongo_client.drop_database(name)
