"""Release activation Loading SJ (M5) — fail-closed gates + flag upsert.

Tidak dijalankan dari request path. Aktivasi production hanya setelah
Owner approval + PR #26 guard Accepted terverifikasi pada exact SHA.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pymongo.database import Database

from app.repositories import master_repository as master
from app.services.loading_sj_bootstrap import (
    LOADING_SJ_TASK_TYPE,
    RECONCILE_RESULT_HOLD,
    LoadingSjBootstrapError,
    audit_loading_sj,
    bootstrap_loading_sj_master,
    classify_production_head_loading_sj_grant,
    reconcile_production_head_loading_sj_grant,
    verify_loading_sj_contract,
)

FLAG_KEY = "V4_LOADING_SJ_TASK_ENABLED"
ACCEPTED_GUARD_SYMBOL = "_require_accepted_po_for_note"
DELIVERY_SERVICE_REL = Path("backend/app/services/delivery_service.py")
DELIVERY_API_REL = Path("backend/app/api/delivery.py")

# Merge commit PR #26 di main (Owner). Wajib ancestor target release (SHA40).
PR26_MERGE_COMMIT_SHA = "cff64838d62ef233fd7c02f73b6ca403913d3f26"

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

# Call sites yang wajib memanggil guard Accepted (kontrak PR #26).
# Loading + Delivery — verifikasi tree gabungan, bukan simbol semata.
REQUIRED_GUARD_CALL_SITES = (
    "confirm_loading",
    "confirm_delivery_success",
    "confirm_delivery_failure",
)


def normalize_sha40(value: str | None) -> str | None:
    """SHA40 penuh saja — prefix pendek ditolak."""
    text = str(value or "").strip().lower()
    if not _SHA40_RE.fullmatch(text):
        return None
    return text


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
    """Wajib SHA40 penuh — bandingkan exact approved SHA dengan HEAD."""
    head = resolve_head_sha(repo)
    want = normalize_sha40(approved_sha)
    have = normalize_sha40(head)
    if want is None:
        return ActivationGateResult(
            False,
            "exact_sha",
            "approved_sha wajib SHA40 penuh (40 hex); prefix pendek ditolak.",
            {"approved_sha": approved_sha},
        )
    if have is None:
        return ActivationGateResult(
            False,
            "exact_sha",
            f"HEAD bukan SHA40 valid: {head!r}",
            {"head": head},
        )
    if want != have:
        return ActivationGateResult(
            False,
            "exact_sha",
            f"HEAD {have} ≠ approved_sha {want}",
            {"head": have, "approved_sha": want},
        )
    return ActivationGateResult(
        True,
        "exact_sha",
        "SHA40 exact cocok",
        {"head": have, "approved_sha": want},
    )


def gate_pr26_merged(repo: Path, *, merged_ref: str | None = None) -> ActivationGateResult:
    """Wajib: merge commit PR #26 (SHA40) adalah ancestor HEAD + guard di tree.

    Tidak mengandalkan pencarian simbol semata — ancestor merge commit
    dan call sites Loading/Delivery harus lulus.
    """
    ref = normalize_sha40(merged_ref)
    if ref is None:
        return ActivationGateResult(
            False,
            "pr26_merged",
            (
                "pr26_merged_ref wajib SHA40 penuh (merge commit PR #26). "
                f"Contoh main: {PR26_MERGE_COMMIT_SHA}"
            ),
            {"merged_ref": merged_ref},
        )
    try:
        _run_git(repo, "merge-base", "--is-ancestor", ref, "HEAD")
    except LoadingSjActivationHold as exc:
        return ActivationGateResult(
            False,
            "pr26_merged",
            f"Merge commit PR #26 {ref} bukan ancestor HEAD: {exc}",
            {"merged_ref": ref},
        )

    source_gate = gate_accepted_guard_in_source(repo)
    if not source_gate.ok:
        return ActivationGateResult(
            False,
            "pr26_merged",
            (
                "Merge commit ancestor OK, tetapi guard Loading/Delivery "
                f"belum efektif di tree: {source_gate.detail}"
            ),
            {"merged_ref": ref, "source": source_gate.data},
        )
    return ActivationGateResult(
        True,
        "pr26_merged",
        "PR #26 merge commit ancestor + guard Loading/Delivery efektif",
        {
            "merged_ref": ref,
            "source": source_gate.data,
            "canonical_pr26_merge": PR26_MERGE_COMMIT_SHA,
        },
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


def verify_running_backend_activation(
    probe: Callable[[], dict[str, Any]],
) -> ActivationGateResult:
    """Verifikasi flag efektif di **backend yang sedang berjalan** + health PASS.

    Jangan memakai env proses script sebagai bukti aktivasi.
    ``probe`` harus mengembalikan dict minimal:
      ok, healthy, loading_sj_task_enabled
    """
    try:
        data = probe()
    except Exception as exc:  # noqa: BLE001
        return ActivationGateResult(
            False,
            "running_backend_probe",
            f"Probe backend berjalan gagal: {exc}",
        )
    if not isinstance(data, dict):
        return ActivationGateResult(
            False,
            "running_backend_probe",
            "Probe harus mengembalikan dict.",
        )
    if not data.get("ok") or not data.get("healthy"):
        return ActivationGateResult(
            False,
            "running_backend_health",
            "Backend berjalan tidak healthy / probe ok=false.",
            dict(data),
        )
    if not bool(data.get("loading_sj_task_enabled")):
        return ActivationGateResult(
            False,
            "running_backend_flag",
            "Flag loading_sj_task_enabled masih OFF di backend berjalan.",
            dict(data),
        )
    return ActivationGateResult(
        True,
        "running_backend_flag",
        "Backend berjalan healthy + flag ON",
        dict(data),
    )


def probe_running_backend_http(
    base_url: str,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Probe GET {base_url}/api/health — baca status + features.loading_sj_task_enabled."""
    import json
    import urllib.error
    import urllib.request

    url = str(base_url or "").rstrip("/") + "/api/health"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
            payload = json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        return {
            "ok": False,
            "healthy": False,
            "loading_sj_task_enabled": False,
            "error": str(exc),
            "url": url,
        }
    features = payload.get("features") if isinstance(payload, dict) else None
    enabled = False
    if isinstance(features, dict):
        enabled = bool(features.get("loading_sj_task_enabled"))
    healthy = (
        isinstance(payload, dict)
        and str(payload.get("status") or "").lower() == "ok"
        and bool(payload.get("mongodb"))
    )
    return {
        "ok": True,
        "healthy": healthy,
        "loading_sj_task_enabled": enabled,
        "url": url,
        "payload": payload,
    }


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
    running_backend_probe: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prosedur aktivasi penuh. HOLD → tidak set flag ON / tidak klaim SUCCESS palsu.

    Urutan: gates (SHA40 + PR26 ancestor) → bootstrap → reconcile → verify →
    set flag → **restart wajib** → **probe backend berjalan** (health + flag).

    ``activated=True`` hanya jika probe backend berjalan membuktikan flag ON.
    Env proses script **bukan** bukti aktivasi.
    """
    audit_log: list[dict[str, Any]] = []

    def _hold(gate: str, detail: str, **extra: Any) -> dict[str, Any]:
        payload = {
            "ok": False,
            "activated": False,
            "hold": True,
            "gate": gate,
            "detail": detail,
            "flag_written": bool(extra.get("flag_written", False)),
            "audit": audit_log,
            **{k: v for k, v in extra.items() if k != "flag_written"},
        }
        # Pastikan flag_written konsisten di top-level.
        if "flag_written" in extra:
            payload["flag_written"] = bool(extra["flag_written"])
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

    # Permission reconciliation terbatas (CASE C) — hanya approved release path.
    reconcile_result: dict[str, Any] | None = None
    if dry_run:
        classification = classify_production_head_loading_sj_grant(db)
        audit_log.append(
            {
                "stage": "reconcile",
                "dry_run": True,
                "classification": classification,
                "note": "Tidak menulis — dry-run.",
            }
        )
    else:
        reconcile_result = reconcile_production_head_loading_sj_grant(
            db,
            expected_db_name=expected_db_name,
            allow_production=allow_production,
            approved_release=True,
        )
        audit_log.append({"stage": "reconcile", "result": reconcile_result})
        if reconcile_result.get("result") == RECONCILE_RESULT_HOLD:
            return _hold(
                "reconcile",
                str(reconcile_result.get("reason") or "Reconciliation HOLD"),
                code=reconcile_result.get("code"),
                reconcile=reconcile_result,
            )

    # Post-bootstrap + reconcile: verifikasi kontrak penuh.
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
            "reconcile": reconcile_result,
            "audit": audit_log,
        }

    flag_result = upsert_env_flag(env_file, key=FLAG_KEY, value="1", dry_run=False)
    audit_log.append({"stage": "flag_upsert", "result": flag_result})

    # Restart backend wajib sebelum klaim aktivasi efektif.
    if restart_callback is None:
        return _hold(
            "restart_required",
            (
                "Restart backend wajib untuk verifikasi flag efektif di proses "
                "berjalan. Tanpa restart_callback → HOLD (bukan SUCCESS palsu)."
            ),
            flag_written=True,
            flag_result=flag_result,
            bootstrap=bootstrap_result,
            reconcile=reconcile_result,
            rollback_hint=f"Set {FLAG_KEY}=0 di {env_file} bila perlu.",
        )

    try:
        restart_result = restart_callback()
    except Exception as exc:  # noqa: BLE001
        return _hold(
            "restart_failed",
            f"Restart backend gagal: {exc}",
            flag_written=True,
            flag_result=flag_result,
            bootstrap=bootstrap_result,
            reconcile=reconcile_result,
        )
    audit_log.append({"stage": "restart", "result": restart_result})
    if isinstance(restart_result, dict) and restart_result.get("ok") is False:
        return _hold(
            "restart_failed",
            str(restart_result.get("detail") or "Restart callback ok=false"),
            flag_written=True,
            flag_result=flag_result,
            bootstrap=bootstrap_result,
            reconcile=reconcile_result,
            restart=restart_result,
        )

    if running_backend_probe is None:
        return _hold(
            "running_backend_probe_required",
            (
                "Probe backend berjalan wajib (health + loading_sj_task_enabled). "
                "Env proses script bukan bukti aktivasi."
            ),
            flag_written=True,
            flag_result=flag_result,
            bootstrap=bootstrap_result,
            reconcile=reconcile_result,
            restart=restart_result,
        )

    probe_gate = verify_running_backend_activation(running_backend_probe)
    audit_log.append({"stage": "running_backend_probe", "result": probe_gate.__dict__})
    if not probe_gate.ok:
        return _hold(
            probe_gate.gate,
            probe_gate.detail,
            flag_written=True,
            flag_result=flag_result,
            bootstrap=bootstrap_result,
            reconcile=reconcile_result,
            restart=restart_result,
            probe=probe_gate.data,
            rollback_hint=f"Set {FLAG_KEY}=0 di {env_file} lalu restart.",
        )

    return {
        "ok": True,
        "activated": True,
        "hold": False,
        "flag_written": True,
        "flag_result": flag_result,
        "bootstrap": bootstrap_result,
        "reconcile": reconcile_result,
        "restart": restart_result,
        "running_backend": probe_gate.data,
        "audit": audit_log,
        "task_type": LOADING_SJ_TASK_TYPE,
    }
