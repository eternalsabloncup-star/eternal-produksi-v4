"""M5 — activation gates + flag upsert (tanpa production write)."""

from __future__ import annotations

import textwrap
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from app.config import clear_settings_cache
from app.db import close_client, ensure_indexes
from app.services import loading_sj_activation as act
from app.services import loading_sj_bootstrap as boot


@pytest.fixture()
def bare_db(
    mongo_client: MongoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Database, None, None]:
    name = f"eternal_v4_test_m5act_{uuid.uuid4().hex[:10]}"
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


def _repo_with_guard(tmp_path: Path, *, with_guard: bool) -> Path:
    """Mini repo tree meniru lokasi delivery_service untuk gate source."""
    repo = tmp_path / "repo"
    svc = repo / "backend" / "app" / "services"
    api = repo / "backend" / "app" / "api"
    svc.mkdir(parents=True)
    api.mkdir(parents=True)
    if with_guard:
        (svc / "delivery_service.py").write_text(
            textwrap.dedent(
                """
                def _require_accepted_po_for_note(db, note):
                    return note

                def confirm_loading(db, user, *, accurate_sj_id, idempotency_key):
                    note = {"accurate_sj_id": accurate_sj_id}
                    _require_accepted_po_for_note(db, note)
                    return {}

                def confirm_delivery_success(db, user, *, accurate_sj_id):
                    note = {"accurate_sj_id": accurate_sj_id}
                    _require_accepted_po_for_note(db, note)
                    return {}

                def confirm_delivery_failure(db, user, *, accurate_sj_id):
                    note = {"accurate_sj_id": accurate_sj_id}
                    _require_accepted_po_for_note(db, note)
                    return {}
                """
            ).lstrip(),
            encoding="utf-8",
        )
        (api / "delivery.py").write_text(
            "from app.services.po_acceptance import PoAcceptanceError\n",
            encoding="utf-8",
        )
    else:
        (svc / "delivery_service.py").write_text(
            "def confirm_loading(db, user, *, accurate_sj_id, idempotency_key):\n"
            "    return {}\n",
            encoding="utf-8",
        )
        (api / "delivery.py").write_text("# no guard\n", encoding="utf-8")
    # git repo minimal for SHA gates — init if needed by caller.
    return repo


def _git_init(repo: Path) -> str:
    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "m5@test.local"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "M5 Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "m5 fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return sha


def test_gate_hold_when_accepted_guard_missing(tmp_path: Path) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=False)
    gate = act.gate_accepted_guard_in_source(repo)
    assert gate.ok is False
    assert gate.gate == "accepted_guard_source"


def test_gate_pass_when_accepted_guard_present(tmp_path: Path) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    gate = act.gate_accepted_guard_in_source(repo)
    assert gate.ok is True


def test_activation_hold_master_missing(
    bare_db: Database, tmp_path: Path
) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    sha = _git_init(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("APP_ENV=test\nV4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    # Skip bootstrap → master missing → HOLD before flag.
    result = act.activate_loading_sj_release(
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
    assert result["hold"] is True
    assert result["flag_written"] is False
    assert act.read_env_flag(env_file) == "0"


def test_activation_hold_when_guard_missing_in_target(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=False)
    sha = _git_init(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
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
    )
    assert result["hold"] is True
    assert result["gate"] in {"pr26_merged", "accepted_guard_source"}
    assert result["flag_written"] is False
    assert act.read_env_flag(env_file) == "0"


def test_activation_hold_permission_invalid(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    # Rusak grant helper (cancel) → bootstrap ulang akan STOP; simulate post-bootstrap.
    from app.repositories import master_repository as master

    master.set_role_actions(
        bare_db,
        role="helper",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    repo = _repo_with_guard(tmp_path, with_guard=True)
    sha = _git_init(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
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
    assert result["hold"] is True
    assert result["flag_written"] is False


def test_activation_hold_sha_mismatch(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=True)
    _git_init(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
        repo=repo,
        db=bare_db,
        approved_sha="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        expected_db_name=bare_db.name,
        env_file=env_file,
        backup_ok=True,
        no_other_deploy=True,
        backend_healthy=True,
        mongo_healthy=True,
        guard_regression_ok=True,
        dry_run=False,
    )
    assert result["hold"] is True
    assert result["gate"] == "exact_sha"
    assert act.read_env_flag(env_file) == "0"


def test_activation_all_prereq_pass_sets_flag(
    bare_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    sha = _git_init(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text(
        "APP_ENV=test\nOTHER=1\nV4_LOADING_SJ_TASK_ENABLED=0\n",
        encoding="utf-8",
    )
    result = act.activate_loading_sj_release(
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
        apply_environ_for_verify=True,
    )
    assert result["ok"] is True
    assert result["activated"] is True
    assert result["flag_written"] is True
    assert act.read_env_flag(env_file) == "1"
    # OTHER tetap ada.
    text = env_file.read_text(encoding="utf-8")
    assert "OTHER=1" in text
    boot.verify_loading_sj_contract(bare_db)
    clear_settings_cache()
    from app.config import get_settings

    assert get_settings().loading_sj_task_enabled is True


def test_activation_second_run_idempotent(
    bare_db: Database, tmp_path: Path
) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    sha = _git_init(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    kwargs = dict(
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
        apply_environ_for_verify=True,
    )
    first = act.activate_loading_sj_release(**kwargs)
    second = act.activate_loading_sj_release(**kwargs)
    assert first["activated"] is True
    assert second["ok"] is True
    assert second["activated"] is True
    assert act.read_env_flag(env_file) == "1"
    # Bootstrap kedua noop.
    assert second["bootstrap"]["noop"] is True


def test_dry_run_does_not_write_flag(
    bare_db: Database, tmp_path: Path
) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    sha = _git_init(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
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
        dry_run=True,
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["flag_written"] is False
    assert act.read_env_flag(env_file) == "0"
    # Bootstrap tidak jalan di dry-run → master masih kosong.
    assert boot.audit_loading_sj(bare_db)["present"] is False


def test_failed_prereq_leaves_flag_off(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=True)
    sha = _git_init(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
        repo=repo,
        db=bare_db,
        approved_sha=sha,
        expected_db_name=bare_db.name,
        env_file=env_file,
        backup_ok=False,  # HOLD
        no_other_deploy=True,
        backend_healthy=True,
        mongo_healthy=True,
        guard_regression_ok=True,
        dry_run=False,
    )
    assert result["hold"] is True
    assert result["gate"] == "backup"
    assert act.read_env_flag(env_file) == "0"
