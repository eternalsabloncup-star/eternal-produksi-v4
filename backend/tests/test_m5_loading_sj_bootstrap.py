"""M5 — bootstrap Master loading-sj + permission (Mongo test terisolasi)."""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from app.config import clear_settings_cache
from app.db import close_client, ensure_indexes
from app.repositories import master_repository as master
from app.services import loading_sj_bootstrap as boot
from app.services.task_engine import can_act, require_action, TaskPermissionError


@pytest.fixture()
def bare_db(
    mongo_client: MongoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Database, None, None]:
    """DB test dengan indexes saja — tanpa seed_master_defaults."""
    name = f"eternal_v4_test_m5_{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("MONGODB_DB", name)
    monkeypatch.setenv("DB_NAME", name)
    monkeypatch.setenv("APP_ENV", "test")
    clear_settings_cache()
    close_client()
    database = mongo_client[name]
    ensure_indexes(database)
    yield database
    close_client()
    clear_settings_cache()
    try:
        mongo_client.drop_database(name)
    except Exception:  # noqa: BLE001
        pass


HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["access.all_branches"],
}
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}
DRIVER = {
    "email_normalized": "driver@eternal.local",
    "role": "user",
    "operational_role": "driver",
    "effective_module_permissions": ["access.all_branches"],
}
STRANGER = {
    "email_normalized": "x@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": [],
}


def test_bootstrap_first_pass(bare_db: Database) -> None:
    before_types = bare_db["master_task_types"].count_documents({})
    assert before_types == 0
    result = boot.bootstrap_loading_sj_master(
        bare_db, expected_db_name=bare_db.name
    )
    assert result["ok"] is True
    assert result["inserted_task_types"] == 1
    assert result["inserted_role_actions"] == 2
    assert result["noop"] is False
    boot.verify_loading_sj_contract(bare_db)
    # Hanya satu task type.
    assert bare_db["master_task_types"].count_documents({}) == 1
    doc = master.get_task_type(bare_db, "loading-sj")
    assert doc is not None
    assert doc["assigned_role"] == "helper"
    assert doc["label"] == "Loading Surat Jalan"
    assert doc["scope"] == "delivery_note"


def test_bootstrap_second_idempotent(bare_db: Database) -> None:
    first = boot.bootstrap_loading_sj_master(
        bare_db, expected_db_name=bare_db.name
    )
    second = boot.bootstrap_loading_sj_master(
        bare_db, expected_db_name=bare_db.name
    )
    assert first["inserted_task_types"] == 1
    assert second["inserted_task_types"] == 0
    assert second["inserted_role_actions"] == 0
    assert second["noop"] is True
    assert bare_db["master_task_types"].count_documents({}) == 1


def test_bootstrap_rejects_wrong_db_name(bare_db: Database) -> None:
    with pytest.raises(boot.LoadingSjBootstrapError) as ei:
        boot.bootstrap_loading_sj_master(
            bare_db, expected_db_name="eternal_v4_test_other"
        )
    assert ei.value.code == "LOADING_SJ_DB_TARGET_MISMATCH"


def test_bootstrap_rejects_production_without_allow(bare_db: Database) -> None:
    # Simulasikan nama production dengan rename logical check langsung.
    with pytest.raises(boot.LoadingSjBootstrapError) as ei:
        boot.assert_loading_sj_db_target(
            type("D", (), {"name": "eternal_v4"})(),
            expected_db_name="eternal_v4",
            allow_production=False,
        )
    assert ei.value.code == "LOADING_SJ_DB_PRODUCTION_BLOCKED"


def test_bootstrap_stops_on_material_mismatch(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    bare_db["master_task_types"].update_one(
        {"task_type": "loading-sj"},
        {"$set": {"label": "Bukan Loading SJ", "is_default_mapping": True}},
    )
    with pytest.raises(boot.LoadingSjBootstrapError) as ei:
        boot.bootstrap_loading_sj_master(
            bare_db, expected_db_name=bare_db.name
        )
    assert ei.value.code == "LOADING_SJ_MASTER_MATERIAL_MISMATCH"
    # Tidak overwrite.
    doc = master.get_task_type(bare_db, "loading-sj")
    assert doc["label"] == "Bukan Loading SJ"


def test_bootstrap_stops_on_custom_grant_mismatch(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    master.set_role_actions(
        bare_db,
        role="helper",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    with pytest.raises(boot.LoadingSjBootstrapError) as ei:
        boot.bootstrap_loading_sj_master(
            bare_db, expected_db_name=bare_db.name
        )
    assert ei.value.code == "LOADING_SJ_GRANT_MATERIAL_MISMATCH"


def test_bootstrap_hold_when_kp_missing_confirm(bare_db: Database) -> None:
    """Grant KP existing tanpa confirm → FAIL-CLOSED, tidak overwrite diam-diam."""
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "edit", "cancel"],
    )
    with pytest.raises(boot.LoadingSjBootstrapError) as ei:
        boot.bootstrap_loading_sj_master(
            bare_db, expected_db_name=bare_db.name
        )
    assert ei.value.code == "LOADING_SJ_GRANT_MATERIAL_MISMATCH"
    grant = bare_db["master_role_actions"].find_one(
        {"role": "production-head", "task_type": "loading-sj"}
    )
    assert sorted(grant["actions"]) == ["cancel", "edit", "view"]


def test_bootstrap_does_not_change_non_target(bare_db: Database) -> None:
    # Sisipkan task lain sebelum bootstrap.
    bare_db["master_task_types"].insert_one(
        {
            "task_type": "cetak",
            "label": "Cetak",
            "scope": "po_line_allocation",
            "assigned_role": "operator",
            "actions": ["view", "confirm"],
            "is_default_mapping": False,
        }
    )
    bare_db["master_role_actions"].insert_one(
        {
            "role": "operator",
            "task_type": "cetak",
            "actions": ["view", "confirm"],
            "is_default_mapping": False,
        }
    )
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    cetak = master.get_task_type(bare_db, "cetak")
    assert cetak is not None
    assert cetak["assigned_role"] == "operator"
    assert cetak["is_default_mapping"] is False
    grant = bare_db["master_role_actions"].find_one(
        {"role": "operator", "task_type": "cetak"}
    )
    assert grant["actions"] == ["view", "confirm"]
    # Tidak membuat task lain.
    types = {d["task_type"] for d in bare_db["master_task_types"].find()}
    assert types == {"cetak", "loading-sj"}


def test_permission_helper_matrix(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    assert can_act(bare_db, HELPER, task_type="loading-sj", action="view")
    assert can_act(bare_db, HELPER, task_type="loading-sj", action="confirm")
    assert can_act(bare_db, HELPER, task_type="loading-sj", action="edit")
    assert not can_act(bare_db, HELPER, task_type="loading-sj", action="cancel")
    with pytest.raises(TaskPermissionError):
        require_action(bare_db, HELPER, task_type="loading-sj", action="cancel")


def test_permission_production_head_matrix(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    assert can_act(bare_db, HEAD, task_type="loading-sj", action="view")
    assert can_act(bare_db, HEAD, task_type="loading-sj", action="confirm")
    assert can_act(bare_db, HEAD, task_type="loading-sj", action="edit")
    assert can_act(bare_db, HEAD, task_type="loading-sj", action="cancel")


def test_permission_driver_no_auto_grant(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    audit = boot.audit_loading_sj(bare_db)
    assert audit["driver_has_grant"] is False
    assert not can_act(bare_db, DRIVER, task_type="loading-sj", action="confirm")
    assert not can_act(bare_db, DRIVER, task_type="loading-sj", action="view")


def test_permission_unauthorized_denied(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    assert not can_act(
        bare_db, STRANGER, task_type="loading-sj", action="confirm"
    )
    with pytest.raises(TaskPermissionError):
        require_action(
            bare_db, STRANGER, task_type="loading-sj", action="view"
        )
