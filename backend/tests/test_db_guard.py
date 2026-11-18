"""Fail-closed Mongo target untuk sync Accurate → V4."""

from __future__ import annotations

import pytest

from app.db_guard import (
    UnsafeMongoTargetError,
    assert_safe_mongo_sync_target,
    extract_mongo_ports,
)


def test_extract_ports() -> None:
    assert 27018 in extract_mongo_ports(
        "mongodb://u:p@127.0.0.1:27018/?authSource=admin"
    )
    assert 27017 in extract_mongo_ports(
        "mongodb://u:p@127.0.0.1:27017/?authSource=admin&replicaSet=rs0"
    )


def test_block_legacy_port_outside_test() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_sync_target(
            uri="mongodb://127.0.0.1:27017",
            db_name="eternal_v4_test_abc",
            app_env="development",
        )
    assert exc.value.code == "mongo_legacy_port_blocked"


def test_allow_test_db_on_27017_only_in_app_env_test() -> None:
    assert_safe_mongo_sync_target(
        uri="mongodb://127.0.0.1:27017",
        db_name="eternal_v4_test_ci",
        app_env="test",
    )


def test_block_ops_db_name_even_on_27018() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_sync_target(
            uri="mongodb://127.0.0.1:27018",
            db_name="eternal_v4",
            app_env="development",
        )
    assert exc.value.code == "mongo_legacy_db_blocked"


def test_block_non_test_db_on_safe_port() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_sync_target(
            uri="mongodb://127.0.0.1:27018",
            db_name="random_db",
            app_env="development",
        )
    assert exc.value.code == "mongo_non_test_db_blocked"


def test_allow_isolated_test_on_27018() -> None:
    assert_safe_mongo_sync_target(
        uri=(
            "mongodb://user:pass@127.0.0.1:27018/"
            "?authSource=eternal_v4_test_manual"
        ),
        db_name="eternal_v4_test_manual",
        app_env="development",
    )


def test_auth_source_required_outside_test() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_sync_target(
            uri="mongodb://user:pass@127.0.0.1:27018/",
            db_name="eternal_v4_test_manual",
            app_env="development",
        )
    assert exc.value.code == "mongo_auth_source_missing"


def test_auth_source_must_match_test_db() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_sync_target(
            uri="mongodb://user:pass@127.0.0.1:27018/?authSource=admin",
            db_name="eternal_v4_test_manual",
            app_env="development",
        )
    assert exc.value.code == "mongo_auth_source_mismatch"
