"""M5 — activation gates (SHA40, PR26 ancestor, running-backend probe)."""

from __future__ import annotations

import subprocess
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
    return repo


def _git_init_with_ancestor(repo: Path) -> tuple[str, str]:
    """Return (head_sha40, pr26_ancestor_sha40)."""
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
        ["git", "commit", "-m", "pr26-merge-sim"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    pr26 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Second commit = release HEAD (descendant of pr26 sim).
    marker = repo / "backend" / "app" / "services" / "delivery_service.py"
    marker.write_text(marker.read_text(encoding="utf-8") + "\n# release\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "m5-release"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert len(head) == 40 and len(pr26) == 40
    return head, pr26


def _ok_restart() -> dict:
    return {"ok": True, "detail": "restart-sim"}


def _ok_probe() -> dict:
    return {
        "ok": True,
        "healthy": True,
        "loading_sj_task_enabled": True,
        "source": "test-probe",
    }


def _base_kwargs(repo, bare_db, env_file, head, pr26, **extra):
    return {
        "repo": repo,
        "db": bare_db,
        "approved_sha": head,
        "expected_db_name": bare_db.name,
        "env_file": env_file,
        "pr26_merged_ref": pr26,
        "backup_ok": True,
        "no_other_deploy": True,
        "backend_healthy": True,
        "mongo_healthy": True,
        "guard_regression_ok": True,
        "dry_run": False,
        **extra,
    }


def test_gate_hold_when_accepted_guard_missing(tmp_path: Path) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=False)
    gate = act.gate_accepted_guard_in_source(repo)
    assert gate.ok is False


def test_gate_pass_when_accepted_guard_present(tmp_path: Path) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    gate = act.gate_accepted_guard_in_source(repo)
    assert gate.ok is True


def test_exact_sha_rejects_short_prefix(tmp_path: Path) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, _pr26 = _git_init_with_ancestor(repo)
    gate = act.gate_exact_sha(repo, head[:12])
    assert gate.ok is False
    assert "SHA40" in gate.detail or "prefix" in gate.detail.lower()


def test_exact_sha_requires_full_match(tmp_path: Path) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, _pr26 = _git_init_with_ancestor(repo)
    assert act.gate_exact_sha(repo, head).ok is True
    other = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert act.gate_exact_sha(repo, other).ok is False


def test_pr26_requires_ancestor_sha40(tmp_path: Path) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, pr26 = _git_init_with_ancestor(repo)
    assert act.gate_pr26_merged(repo, merged_ref=None).ok is False
    assert act.gate_pr26_merged(repo, merged_ref=pr26[:12]).ok is False
    assert act.gate_pr26_merged(repo, merged_ref=pr26).ok is True
    # Non-ancestor random SHA40
    foreign = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert act.gate_pr26_merged(repo, merged_ref=foreign).ok is False
    _ = head


def test_activation_hold_without_restart_or_probe(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, pr26 = _git_init_with_ancestor(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
        **_base_kwargs(repo, bare_db, env_file, head, pr26, skip_bootstrap=True)
    )
    assert result["hold"] is True
    assert result["activated"] is False
    assert result["gate"] == "restart_required"
    assert result["flag_written"] is True
    assert act.read_env_flag(env_file) == "1"


def test_activation_hold_when_probe_flag_off(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, pr26 = _git_init_with_ancestor(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")

    def bad_probe() -> dict:
        return {
            "ok": True,
            "healthy": True,
            "loading_sj_task_enabled": False,
        }

    result = act.activate_loading_sj_release(
        **_base_kwargs(
            repo,
            bare_db,
            env_file,
            head,
            pr26,
            skip_bootstrap=True,
            restart_callback=_ok_restart,
            running_backend_probe=bad_probe,
        )
    )
    assert result["hold"] is True
    assert result["activated"] is False
    assert result["gate"] == "running_backend_flag"


def test_activation_success_requires_running_probe(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, pr26 = _git_init_with_ancestor(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("OTHER=1\nV4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
        **_base_kwargs(
            repo,
            bare_db,
            env_file,
            head,
            pr26,
            skip_bootstrap=True,
            restart_callback=_ok_restart,
            running_backend_probe=_ok_probe,
        )
    )
    assert result["ok"] is True
    assert result["activated"] is True
    assert result["flag_written"] is True
    assert "OTHER=1" in env_file.read_text(encoding="utf-8")
    assert act.read_env_flag(env_file) == "1"


def test_activation_hold_master_missing(
    bare_db: Database, tmp_path: Path
) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, pr26 = _git_init_with_ancestor(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
        **_base_kwargs(
            repo,
            bare_db,
            env_file,
            head,
            pr26,
            skip_bootstrap=True,
            restart_callback=_ok_restart,
            running_backend_probe=_ok_probe,
        )
    )
    assert result["hold"] is True
    assert result["activated"] is False
    assert act.read_env_flag(env_file) == "0"


def test_activation_hold_when_guard_missing_in_target(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=False)
    head, pr26 = _git_init_with_ancestor(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
        **_base_kwargs(repo, bare_db, env_file, head, pr26)
    )
    assert result["hold"] is True
    assert result["gate"] in {"pr26_merged", "accepted_guard_source"}
    assert result["flag_written"] is False


def test_activation_hold_sha_mismatch(
    bare_db: Database, tmp_path: Path
) -> None:
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=True)
    _head, pr26 = _git_init_with_ancestor(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
        **_base_kwargs(
            repo,
            bare_db,
            env_file,
            "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            pr26,
        )
    )
    assert result["hold"] is True
    assert result["gate"] == "exact_sha"


def test_dry_run_does_not_write_flag(
    bare_db: Database, tmp_path: Path
) -> None:
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, pr26 = _git_init_with_ancestor(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    result = act.activate_loading_sj_release(
        **_base_kwargs(repo, bare_db, env_file, head, pr26, dry_run=True)
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["activated"] is False
    assert act.read_env_flag(env_file) == "0"


def test_script_env_alone_never_activates(
    bare_db: Database, tmp_path: Path
) -> None:
    """Tidak ada jalur apply_environ yang mengklaim activated=True."""
    boot.bootstrap_loading_sj_master(bare_db, expected_db_name=bare_db.name)
    repo = _repo_with_guard(tmp_path, with_guard=True)
    head, pr26 = _git_init_with_ancestor(repo)
    env_file = tmp_path / "backend.env"
    env_file.write_text("V4_LOADING_SJ_TASK_ENABLED=0\n", encoding="utf-8")
    # Hanya restart, tanpa probe → HOLD
    result = act.activate_loading_sj_release(
        **_base_kwargs(
            repo,
            bare_db,
            env_file,
            head,
            pr26,
            skip_bootstrap=True,
            restart_callback=_ok_restart,
            running_backend_probe=None,
        )
    )
    assert result["activated"] is False
    assert result["hold"] is True
    assert result["gate"] == "running_backend_probe_required"
