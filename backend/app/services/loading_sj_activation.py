"""Release activation Loading SJ (M5) — fail-closed gates + flag upsert.

Tidak dijalankan dari request path. Aktivasi production hanya setelah
Owner approval + PR #26 guard Accepted terverifikasi pada exact SHA.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pymongo.database import Database

from app.config import clear_settings_cache, get_settings
from app.repositories import master_repository as master
from app.services.loading_sj_bootstrap import (
    LOADING_SJ_TASK_TYPE,
    LoadingSjBootstrapError,
    audit_loading_sj,
    bootstrap_loading_sj_master,
    verify_loading_sj_contract,
)

FLAG_KEY = "V4_LOADING_SJ_TASK_ENABLED"
ACCEPTED_GUARD_SYMBOL = "_require_accepted_po_for_note"
DELIVERY_SERVICE_REL = Path("backend/app/services/delivery_service.py")
DELIVERY_API_REL = Path("backend/app/api/delivery.py")

# Call sites yang wajib memanggil guard Accepted (kontrak PR #26).
REQUIRED_GUARD_CALL_SITES = (
    "confirm_loading",
    "confirm_delivery_success",
    "confirm_delivery_failure",
)


class LoadingSjActivationHold(RuntimeError):
    """Aktivasi HOLD — prerequisite gagal; flag tidak boleh ON."""

    code = "LOADING_SJ_ACTIVATION_HOLD"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        gate: str | None = None,
    ) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.gate = gate or self.code


@dataclass
class ActivationGateResult:
    ok: bool
    gate: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise LoadingSjActivationHold(
            f"git {' '.join(args)} gagal: {(proc.stderr or proc.stdout).strip()}",
            code="LOADING_SJ_GIT_FAILED",
            gate="git",
        )
    return (proc.stdout or "").strip()


def resolve_head_sha(repo: Path) -> str:
    return _run_git(repo, "rev-parse", "HEAD")


def gate_exact_sha(repo: Path, approved_sha: str) -> ActivationGateResult:
    head = resolve_head_sha(repo)
    want = str(approved_sha or "").strip().lower()
    have = head.lower()
    if not want or len(want) < 7:
        return ActivationGateResult(
            False, "exact_sha", "approved_sha wajib (exact release SHA)."
        )
    # Izinkan SHA penuh atau prefix singkat (≥7) yang saling mencocokkan.
    if not (have.startswith(want) or want.startswith(have)):
        return ActivationGateResult(
            False,
            "exact_sha",
            f"HEAD {head} ≠ approved_sha {approved_sha}",
            {"head": head, "approved_sha": approved_sha},
        )
    return ActivationGateResult(
        True, "exact_sha", "SHA cocok", {"head": head, "approved_sha": approved_sha}
    )


def gate_pr26_merged(repo: Path, *, merged_ref: str | None = None) -> ActivationGateResult:
    """Pastikan guard Accepted PR #26 ada di tree (merged ke target).

    Prefer bukti source: simbol + call sites. Opsional: merged_ref berisi
    merge commit / branch tip yang harus reachable dari HEAD.
    """
    source_gate = gate_accepted_guard_in_source(repo)
    if not source_gate.ok:
        return ActivationGateResult(
            False,
            "pr26_merged",
            f"PR #26 belum terbukti di target: {source_gate.detail}",
            source_gate.data,
        )
    if merged_ref:
        try:
            _run_git(repo, "merge-base", "--is-ancestor", merged_ref, "HEAD")
        except LoadingSjActivationHold as exc:
            return ActivationGateResult(
                False,
                "pr26_merged",
                f"merged_ref {merged_ref!r} bukan ancestor HEAD: {exc}",
            )
    return ActivationGateResult(
        True,
        "pr26_merged",
        "Guard Accepted PR #26 terbukti di source target",
        source_gate.data,
    )


def gate_accepted_guard_in_source(repo: Path) -> ActivationGateResult:
    """Fail-closed: definisi + pemanggilan guard Accepted ada di delivery_service."""
    path = repo / DELIVERY_SERVICE_REL
    if not path.is_file():
        return ActivationGateResult(
            False,
            "accepted_guard_source",
            f"File hilang: {DELIVERY_SERVICE_REL}",
        )
    text = path.read_text(encoding="utf-8")
    if f"def {ACCEPTED_GUARD_SYMBOL}" not in text:
        return ActivationGateResult(
            False,
            "accepted_guard_source",
            f"Fungsi {ACCEPTED_GUARD_SYMBOL} tidak ditemukan — PR #26 belum di target.",
        )
    missing_sites: list[str] = []
    # Heuristik aman: tiap fungsi target harus memuat panggilan guard di body-nya.
    for site in REQUIRED_GUARD_CALL_SITES:
        pattern = rf"def {site}\(.*?\n(?:.*?\n)*?.*?{ACCEPTED_GUARD_SYMBOL}\("
        if not re.search(pattern, text, flags=re.MULTILINE):
            missing_sites.append(site)
    if missing_sites:
        return ActivationGateResult(
            False,
            "accepted_guard_source",
            f"Call site guard hilang: {', '.join(missing_sites)}",
            {"missing": missing_sites},
        )
    api_path = repo / DELIVERY_API_REL
    api_ok = False
    if api_path.is_file():
        api_text = api_path.read_text(encoding="utf-8")
        api_ok = "PoAcceptanceError" in api_text
    return ActivationGateResult(
        True,
        "accepted_guard_source",
        "Guard Accepted + call sites hadir",
        {
            "symbol": ACCEPTED_GUARD_SYMBOL,
            "sites": list(REQUIRED_GUARD_CALL_SITES),
            "api_maps_po_acceptance_error": api_ok,
        },
    )


def gate_master_indexes(db: Database) -> ActivationGateResult:
    report = master.inspect_master_indexes(db)
    missing = report.get("missing") or []
    if missing:
        return ActivationGateResult(
            False,
            "mongo_indexes",
            f"Indeks Master hilang: {missing}",
            report,
        )
    return ActivationGateResult(True, "mongo_indexes", "Indeks Master OK", report)


def gate_master_and_grants(db: Database) -> ActivationGateResult:
    try:
        verified = verify_loading_sj_contract(db)
    except LoadingSjBootstrapError as exc:
        return ActivationGateResult(
            False,
            "master_grants",
            str(exc),
            {"code": getattr(exc, "code", None), "audit": audit_loading_sj(db)},
        )
    return ActivationGateResult(
        True, "master_grants", "Master + grants valid", verified
    )


def gate_permission_least_privilege(db: Database) -> ActivationGateResult:
    audit = audit_loading_sj(db)
    if "cancel" in audit["helper_actions"]:
        return ActivationGateResult(
            False,
            "permission_scope",
            "Helper punya cancel — terlalu luas.",
            audit,
        )
    if audit["driver_has_grant"]:
        return ActivationGateResult(
            False,
            "permission_scope",
            "Driver punya grant loading-sj — terlalu luas.",
            audit,
        )
    # Pastikan tidak ada wildcard role grant aneh.
    for g in audit["grants"]:
        role = str(g.get("role") or "")
        actions = list(g.get("actions") or [])
        if "*" in actions or "all" in actions:
            return ActivationGateResult(
                False,
                "permission_scope",
                f"Wildcard actions pada role {role}",
                audit,
            )
        if role not in {"helper", "production-head"} and actions:
            return ActivationGateResult(
                False,
                "permission_scope",
                f"Grant non-kontrak pada role {role}: {actions}",
                audit,
            )
    return ActivationGateResult(
        True, "permission_scope", "Least privilege OK", audit
    )


def read_env_flag(path: Path, key: str = FLAG_KEY) -> str | None:
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


def upsert_env_flag(
    path: Path,
    *,
    key: str = FLAG_KEY,
    value: str = "1",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Upsert key=value pada file env — tidak menghapus key lain."""
    before = read_env_flag(path, key) if path.is_file() else None
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    out: list[str] = []
    pattern = re.compile(rf"^(export\s+)?{re.escape(key)}\s*=")
    for raw in lines:
        stripped = raw.strip()
        if pattern.match(stripped):
            prefix = "export " if stripped.startswith("export ") else ""
            out.append(f"{prefix}{key}={value}")
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    text = "\n".join(out) + "\n"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return {
        "path": str(path),
        "key": key,
        "before": before,
        "after": value,
        "replaced": replaced,
        "dry_run": dry_run,
        "noop": before == value and replaced,
    }


def verify_effective_flag_on(*, environ: dict[str, str] | None = None) -> ActivationGateResult:
    """Baca Settings efektif — wajib ON setelah aktivasi."""
    clear_settings_cache()
    if environ is not None:
        for k, v in environ.items():
            os.environ[k] = v
        clear_settings_cache()
    settings = get_settings()
    enabled = bool(settings.loading_sj_task_enabled)
    summary = {"loading_sj_task_enabled": enabled}
    if not enabled:
        return ActivationGateResult(
            False,
            "effective_flag",
            "loading_sj_task_enabled masih OFF di Settings efektif",
            summary,
        )
    return ActivationGateResult(
        True,
        "effective_flag",
        "Settings efektif ON",
        summary,
    )


def evaluate_activation_gates(
    *,
    repo: Path,
    db: Database,
    approved_sha: str,
    expected_db_name: str,
    allow_production: bool,
    pr26_merged_ref: str | None = None,
    backup_ok: bool = False,
    no_other_deploy: bool = False,
    backend_healthy: bool = False,
    mongo_healthy: bool = False,
    guard_regression_ok: bool = False,
    require_flag_file: Path | None = None,
) -> list[ActivationGateResult]:
    """Evaluasi semua gate tanpa side-effect flag (kecuali baca)."""
    results: list[ActivationGateResult] = []

    results.append(gate_exact_sha(repo, approved_sha))
    results.append(gate_pr26_merged(repo, merged_ref=pr26_merged_ref))
    results.append(gate_accepted_guard_in_source(repo))

    if not guard_regression_ok:
        results.append(
            ActivationGateResult(
                False,
                "guard_regression",
                "Regression test guard Accepted belum ditandai PASS.",
            )
        )
    else:
        results.append(
            ActivationGateResult(
                True, "guard_regression", "Regression guard Accepted PASS (input)."
            )
        )

    try:
        from app.services.loading_sj_bootstrap import assert_loading_sj_db_target

        assert_loading_sj_db_target(
            db,
            expected_db_name=expected_db_name,
            allow_production=allow_production,
        )
        results.append(
            ActivationGateResult(
                True, "db_target", f"DB target OK: {db.name}"
            )
        )
    except LoadingSjBootstrapError as exc:
        results.append(
            ActivationGateResult(False, "db_target", str(exc), {"code": exc.code})
        )

    results.append(gate_master_indexes(db))

    if not backup_ok:
        results.append(
            ActivationGateResult(
                False,
                "backup",
                "Backup Master/grants/config/DB belum dikonfirmasi.",
            )
        )
    else:
        results.append(ActivationGateResult(True, "backup", "Backup dikonfirmasi."))

    if not no_other_deploy:
        results.append(
            ActivationGateResult(
                False,
                "deploy_isolation",
                "Status deployment lain belum diverifikasi bersih.",
            )
        )
    else:
        results.append(
            ActivationGateResult(
                True, "deploy_isolation", "Tidak ada deployment/repair lain."
            )
        )

    # Master may be missing before bootstrap step — reported separately.
    master_gate = gate_master_and_grants(db)
    results.append(master_gate)
    if master_gate.ok:
        results.append(gate_permission_least_privilege(db))
    else:
        results.append(
            ActivationGateResult(
                False,
                "permission_scope",
                "Dilewati — Master/grants belum valid.",
            )
        )

    if not backend_healthy:
        results.append(
            ActivationGateResult(False, "backend_health", "Backend belum healthy.")
        )
    else:
        results.append(ActivationGateResult(True, "backend_health", "Backend healthy."))

    if not mongo_healthy:
        results.append(
            ActivationGateResult(False, "mongo_health", "Mongo belum healthy.")
        )
    else:
        results.append(ActivationGateResult(True, "mongo_health", "Mongo healthy."))

    if require_flag_file is not None and require_flag_file.is_file():
        current = read_env_flag(require_flag_file)
        results.append(
            ActivationGateResult(
                True,
                "flag_file_readable",
                f"Flag file readable; current={current!r}",
                {"path": str(require_flag_file), "current": current},
            )
        )

    return results


def activate_loading_sj_release(
    *,
    repo: Path,
    db: Database,
    approved_sha: str,
    expected_db_name: str,
    env_file: Path,
    allow_production: bool = False,
    pr26_merged_ref: str | None = None,
    backup_ok: bool = False,
    no_other_deploy: bool = False,
    backend_healthy: bool = False,
    mongo_healthy: bool = False,
    guard_regression_ok: bool = False,
    dry_run: bool = False,
    skip_bootstrap: bool = False,
    restart_callback: Callable[[], dict[str, Any]] | None = None,
    apply_environ_for_verify: bool = True,
) -> dict[str, Any]:
    """Prosedur aktivasi penuh. HOLD → tidak set flag ON.

    Urutan: gates → bootstrap → verify master → set flag → verify efektif.
    """
    audit_log: list[dict[str, Any]] = []

    def _hold(gate: str, detail: str, **extra: Any) -> dict[str, Any]:
        payload = {
            "ok": False,
            "activated": False,
            "hold": True,
            "gate": gate,
            "detail": detail,
            "flag_written": False,
            "audit": audit_log,
            **extra,
        }
        return payload

    # Pre-bootstrap gates (Master missing is expected before bootstrap).
    pre_gates = evaluate_activation_gates(
        repo=repo,
        db=db,
        approved_sha=approved_sha,
        expected_db_name=expected_db_name,
        allow_production=allow_production,
        pr26_merged_ref=pr26_merged_ref,
        backup_ok=backup_ok,
        no_other_deploy=no_other_deploy,
        backend_healthy=backend_healthy,
        mongo_healthy=mongo_healthy,
        guard_regression_ok=guard_regression_ok,
        require_flag_file=env_file,
    )
    audit_log.append(
        {
            "stage": "pre_gates",
            "gates": [g.__dict__ for g in pre_gates],
        }
    )

    # Master/grants + permission gates dijalankan SETELAH bootstrap.
    hard_pre = [
        g
        for g in pre_gates
        if g.gate
        not in {"master_grants", "permission_scope", "flag_file_readable"}
    ]
    failed = [g for g in hard_pre if not g.ok]
    if failed:
        first = failed[0]
        return _hold(first.gate, first.detail, failed_gates=[g.__dict__ for g in failed])

    bootstrap_result: dict[str, Any] | None = None
    if not skip_bootstrap:
        if dry_run:
            audit_log.append({"stage": "bootstrap", "dry_run": True, "skipped_write": True})
        else:
            try:
                bootstrap_result = bootstrap_loading_sj_master(
                    db,
                    expected_db_name=expected_db_name,
                    allow_production=allow_production,
                )
            except LoadingSjBootstrapError as exc:
                return _hold(
                    "bootstrap",
                    str(exc),
                    code=exc.code,
                )
            audit_log.append({"stage": "bootstrap", "result": bootstrap_result})

    # Post-bootstrap master + permission.
    try:
        if not dry_run or skip_bootstrap:
            verify_loading_sj_contract(db)
            perm = gate_permission_least_privilege(db)
            if not perm.ok:
                return _hold(perm.gate, perm.detail, data=perm.data)
        elif dry_run:
            audit_log.append(
                {
                    "stage": "master_verify",
                    "dry_run": True,
                    "note": "verify ditunda sampai non-dry-run",
                }
            )
    except LoadingSjBootstrapError as exc:
        return _hold("master_grants", str(exc), code=exc.code)

    if dry_run:
        return {
            "ok": True,
            "activated": False,
            "hold": False,
            "dry_run": True,
            "flag_written": False,
            "detail": "Dry-run PASS — flag tidak ditulis.",
            "bootstrap": bootstrap_result,
            "audit": audit_log,
        }

    flag_result = upsert_env_flag(env_file, key=FLAG_KEY, value="1", dry_run=False)
    audit_log.append({"stage": "flag_upsert", "result": flag_result})

    restart_result = None
    if restart_callback is not None:
        restart_result = restart_callback()
        audit_log.append({"stage": "restart", "result": restart_result})

    if apply_environ_for_verify:
        os.environ[FLAG_KEY] = "1"
        # Alias historis.
        os.environ["LOADING_SJ_TASK_ENABLED"] = "1"
        eff = verify_effective_flag_on()
        audit_log.append({"stage": "effective_flag", "result": eff.__dict__})
        if not eff.ok:
            # Jangan klaim ON palsu — file sudah ditulis; caller harus rollback manual.
            return {
                "ok": False,
                "activated": False,
                "hold": True,
                "gate": "effective_flag",
                "detail": eff.detail,
                "flag_written": True,
                "flag_result": flag_result,
                "bootstrap": bootstrap_result,
                "audit": audit_log,
                "rollback_hint": f"Set {FLAG_KEY}=0 di {env_file} lalu restart.",
            }

    return {
        "ok": True,
        "activated": True,
        "hold": False,
        "flag_written": True,
        "flag_result": flag_result,
        "bootstrap": bootstrap_result,
        "restart": restart_result,
        "audit": audit_log,
        "task_type": LOADING_SJ_TASK_TYPE,
    }
