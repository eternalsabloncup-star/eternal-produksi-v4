"""M5 — permission reconciliation terbatas production-head/loading-sj."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from app.config import clear_settings_cache
from app.db import close_client, ensure_indexes
from app.repositories import master_repository as master
from app.services import loading_sj_activation as act
from app.services import loading_sj_bootstrap as boot


@pytest.fixture()
def bare_db(
    mongo_client: MongoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Database, None, None]:
    name = f"eternal_v4_test_m5rec_{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("MONGODB_DB", name)
    monkeypatch.setenv("DB_NAME", name)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("V4_LOADING_SJ_TASK_ENABLED", "0")
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


def _seed_other_role_grant(db: Database) -> None:
    db["master_role_actions"].insert_one(
        {
            "role": "operator",
            "task_type": "cetak",
            "actions": ["view", "confirm"],
            "is_default_mapping": False,
        }
    )


def test_case_a_missing_grant_bootstrap(bare_db: Database) -> None:
    assert boot.classify_production_head_loading_sj_grant(bare_db)["case"] == "A"
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    boot.verify_loading_sj_contract(bare_db)
    cls = boot.classify_production_head_loading_sj_grant(bare_db)
    assert cls["case"] == "B"
    assert sorted(cls["before"]["actions"]) == [
        "cancel",
        "confirm",
        "edit",
        "view",
    ]


def test_case_b_already_correct_noop(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    first = boot.reconcile_production_head_loading_sj_grant(
        bare_db,
        expected_db_name=bare_db.name,
        approved_release=True,
    )
    assert first["result"] == boot.RECONCILE_RESULT_NO_OP
    assert first["changed"] is False
    second = boot.reconcile_production_head_loading_sj_grant(
        bare_db,
        expected_db_name=bare_db.name,
        approved_release=True,
    )
    assert second["result"] == boot.RECONCILE_RESULT_NO_OP


def test_case_c_legacy_reconciled_then_idempotent(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "edit", "cancel"],
    )
    _seed_other_role_grant(bare_db)
    before_other = bare_db["master_role_actions"].find_one(
        {"role": "operator", "task_type": "cetak"}
    )
    rec = boot.reconcile_production_head_loading_sj_grant(
        bare_db,
        expected_db_name=bare_db.name,
        approved_release=True,
    )
    assert rec["ok"] is True
    assert rec["result"] == boot.RECONCILE_RESULT_RECONCILED
    assert rec["changed"] is True
    assert rec["before"]["actions"] == ["cancel", "edit", "view"]
    assert sorted(rec["after"]["actions"]) == [
        "cancel",
        "confirm",
        "edit",
        "view",
    ]
    boot.verify_loading_sj_contract(bare_db)
    again = boot.reconcile_production_head_loading_sj_grant(
        bare_db,
        expected_db_name=bare_db.name,
        approved_release=True,
    )
    assert again["result"] == boot.RECONCILE_RESULT_NO_OP
    after_other = bare_db["master_role_actions"].find_one(
        {"role": "operator", "task_type": "cetak"}
    )
    assert after_other["actions"] == before_other["actions"]


def test_case_d_custom_grant_hold_no_overwrite(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "confirm", "manage_evidence"],
    )
    rec = boot.reconcile_production_head_loading_sj_grant(
        bare_db,
        expected_db_name=bare_db.name,
        approved_release=True,
    )
    assert rec["ok"] is False
    assert rec["result"] == boot.RECONCILE_RESULT_HOLD
    assert rec["case"] == "D"
    grant = bare_db["master_role_actions"].find_one(
        {"role": "production-head", "task_type": "loading-sj"}
    )
    assert sorted(grant["actions"]) == ["confirm", "manage_evidence", "view"]


def test_case_e_not_approved_hold(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "edit", "cancel"],
    )
    rec = boot.reconcile_production_head_loading_sj_grant(
        bare_db,
        expected_db_name=bare_db.name,
        approved_release=False,
    )
    assert rec["result"] == boot.RECONCILE_RESULT_HOLD
    assert rec["code"] == "LOADING_SJ_RECONCILE_NOT_APPROVED"
    grant = bare_db["master_role_actions"].find_one(
        {"role": "production-head", "task_type": "loading-sj"}
    )
    assert sorted(grant["actions"]) == ["cancel", "edit", "view"]


def test_concurrent_modification_cas_hold(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "edit", "cancel"],
    )
    # Simulasikan concurrent change: actions berubah sebelum CAS legacy match.
    # Ubah ke custom di tengah — CAS harus miss → HOLD tanpa lost update ke target.
    bare_db["master_role_actions"].update_one(
        {"role": "production-head", "task_type": "loading-sj"},
        {"$set": {"actions": ["view", "edit", "cancel", "delete"]}},
    )
    rec = boot.reconcile_production_head_loading_sj_grant(
        bare_db,
        expected_db_name=bare_db.name,
        approved_release=True,
    )
    assert rec["result"] == boot.RECONCILE_RESULT_HOLD
    grant = bare_db["master_role_actions"].find_one(
        {"role": "production-head", "task_type": "loading-sj"}
    )
    assert "delete" in grant["actions"]
    assert "confirm" not in grant["actions"] or sorted(grant["actions"]) != [
        "cancel",
        "confirm",
        "edit",
        "view",
    ]


def test_helper_no_cancel_driver_no_grant(bare_db: Database) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "edit", "cancel"],
    )
    boot.reconcile_production_head_loading_sj_grant(
        bare_db, expected_db_name=bare_db.name, approved_release=True
    )
    audit = boot.audit_loading_sj(bare_db)
    assert "cancel" not in audit["helper_actions"]
    assert audit["driver_has_grant"] is False


def test_release_reconcile_legacy_then_flag(
    bare_db: Database, tmp_path: Path
) -> None:
    """Approved release: legacy KP → reconcile → flag ON; failure path flag OFF."""
    import textwrap
    import subprocess

    repo = tmp_path / "repo"
    svc = repo / "backend" / "app" / "services"
    api = repo / "backend" / "app" / "api"
    svc.mkdir(parents=True)
    api.mkdir(parents=True)
    (svc / "delivery_service.py").write_text(
        textwrap.dedent(
            """
            def _require_accepted_po_for_note(db, note):
                return note
            def confirm_loading(db, user, *, accurate_sj_id, idempotency_key):
                _require_accepted_po_for_note(db, {"accurate_sj_id": accurate_sj_id})
                return {}
            def confirm_delivery_success(db, user, *, accurate_sj_id):
                _require_accepted_po_for_note(db, {"accurate_sj_id": accurate_sj_id})
                return {}
            def confirm_delivery_failure(db, user, *, accurate_sj_id):
                _require_accepted_po_for_note(db, {"accurate_sj_id": accurate_sj_id})
                return {}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (api / "delivery.py").write_text(
        "from app.services.po_acceptance import PoAcceptanceError\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "m5@test.local"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "M5"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "fix"], cwd=repo, check=True, capture_output=True
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Legacy KP grant + helper via bootstrap then strip KP confirm.
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "edit", "cancel"],
    )
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")

    # Failure path: custom grant → HOLD, flag tetap OFF.
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "delete"],
    )
    hold = act.activate_loading_sj_release(
        repo=repo,
        db=bare_db,
        approved_sha=sha,
        expected_db_name=bare_db.name,
        env_file=env_file,
        backup_ok=True,
        no_other_deploy=True,
        backend_healthy=True,
        mongo_healthy=True,
        guard_regression_ok=True,
        dry_run=False,
        skip_bootstrap=True,
        apply_environ_for_verify=False,
    )
    assert hold["hold"] is True
    assert hold["gate"] == "reconcile"
    assert act.read_env_flag(env_file) == "0"

    # Success path: restore legacy → reconcile in release.
    master.set_role_actions(
        bare_db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "edit", "cancel"],
    )
    ok = act.activate_loading_sj_release(
        repo=repo,
        db=bare_db,
        approved_sha=sha,
        expected_db_name=bare_db.name,
        env_file=env_file,
        backup_ok=True,
        no_other_deploy=True,
        backend_healthy=True,
        mongo_healthy=True,
        guard_regression_ok=True,
        dry_run=False,
        skip_bootstrap=True,
        apply_environ_for_verify=True,
        restore_environ_after_verify=True,
    )
    assert ok["activated"] is True
    assert ok["reconcile"]["result"] == boot.RECONCILE_RESULT_RECONCILED
    assert act.read_env_flag(env_file) == "1"
    boot.verify_loading_sj_contract(bare_db)
    # Scrub process env.
    import os

    os.environ["V4_LOADING_SJ_TASK_ENABLED"] = "0"
    os.environ.pop("LOADING_SJ_TASK_ENABLED", None)
    clear_settings_cache()
