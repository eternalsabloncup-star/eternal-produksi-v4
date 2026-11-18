"""Production Mongo sync guard — terpisah dari guard test."""

from __future__ import annotations

import pytest

from app.db_guard import (
    UnsafeMongoTargetError,
    assert_safe_mongo_production_sync_target,
    assert_safe_mongo_sync_target,
)


PROD_URI = (
    "mongodb://user:pass@127.0.0.1:27018/eternal_v4?authSource=eternal_v4"
)


def test_production_accepts_canonical_target() -> None:
    assert_safe_mongo_production_sync_target(
        uri=PROD_URI,
        db_name="eternal_v4",
        app_env="production",
    )


def test_production_rejects_27017() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_production_sync_target(
            uri="mongodb://user:pass@127.0.0.1:27017/eternal_v4?authSource=eternal_v4",
            db_name="eternal_v4",
            app_env="production",
        )
    assert exc.value.code == "mongo_legacy_port_blocked"


def test_production_rejects_wrong_db() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_production_sync_target(
            uri="mongodb://user:pass@127.0.0.1:27018/eternal_v4_test_manual"
            "?authSource=eternal_v4_test_manual",
            db_name="eternal_v4_test_manual",
            app_env="production",
        )
    assert exc.value.code == "mongo_prod_db_blocked"


def test_production_rejects_wrong_auth_source() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_production_sync_target(
            uri="mongodb://user:pass@127.0.0.1:27018/eternal_v4?authSource=admin",
            db_name="eternal_v4",
            app_env="production",
        )
    assert exc.value.code == "mongo_auth_source_mismatch"


def test_production_rejects_non_production_env() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_production_sync_target(
            uri=PROD_URI,
            db_name="eternal_v4",
            app_env="development",
        )
    assert exc.value.code == "mongo_prod_env_required"


def test_test_guard_still_blocks_ops_eternal_v4() -> None:
    """Guard test lama tetap menolak eternal_v4."""
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_safe_mongo_sync_target(
            uri=PROD_URI,
            db_name="eternal_v4",
            app_env="production",
        )
    assert exc.value.code == "mongo_legacy_db_blocked"
