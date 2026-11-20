"""Shared fail-closed gates for PO branch verify/repair tooling.

Tidak melakukan write Mongo. Dipakai skrip verify + apply.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

KNOWN_BRANCHES = frozenset({"50", "150"})
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_LIVE_REPO = Path("/home/eternaladmin/eternal-production-v4")
REQUIRED_DB = "eternal_v4"
REQUIRED_PORT = 27018
FORBIDDEN_PORT = 27017

# Settings kanonis yang wajib OFF sebelum repair write.
# Invoice/receipt ikut gate: berbagi sync_locks / Accurate writer path.
# SJ: kill-switch + production SJ path.
REQUIRED_AUTO_SYNC_OFF_FIELDS: tuple[str, ...] = (
    "po_auto_sync_enabled",
    "invoice_auto_sync_enabled",
    "receipt_auto_sync_enabled",
    "sj_sync_enabled",
    "sj_sync_production_enabled",
)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_candidates_payload(items: list[dict[str, Any]]) -> bytes:
    """Canonical JSON bytes for checksum binding (sorted keys, compact)."""
    normalized = []
    for it in items:
        normalized.append(
            {
                "accurate_id": str(it.get("accurate_id") or ""),
                "expected_branch_id": str(it.get("expected_branch_id") or ""),
                "accurate_trans_date": it.get("accurate_trans_date"),
                "po_no": it.get("po_no"),
            }
        )
    normalized.sort(key=lambda x: x["accurate_id"])
    return json.dumps(normalized, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def candidates_checksum(items: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_candidates_payload(items)).hexdigest()


def require_full_sha(value: str) -> str:
    sha = (value or "").strip().lower()
    if not FULL_SHA_RE.match(sha):
        raise ValueError(f"invalid_live_sha:{value!r}")
    return sha


def resolve_live_sha(*, live_repo: Path, expect_sha: str) -> str:
    """Fail-closed: live repo must exist and HEAD must match expect."""
    expect = require_full_sha(expect_sha)
    if not live_repo.is_dir():
        raise ValueError(f"live_repo_missing:{live_repo}")
    git_dir = live_repo / ".git"
    if not git_dir.exists():
        raise ValueError(f"live_repo_not_git:{live_repo}")
    try:
        live = subprocess.check_output(
            ["git", "-C", str(live_repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=15,
        ).strip().lower()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError(f"live_sha_unreadable:{exc}") from exc
    if not FULL_SHA_RE.match(live):
        raise ValueError(f"live_sha_invalid:{live!r}")
    if live != expect:
        raise ValueError(f"live_sha_mismatch:live={live}:expect={expect}")
    return live


def parse_mongo_host_port(uri: str) -> tuple[str | None, int | None]:
    raw = (uri or "").strip()
    if not raw:
        return None, None
    # mongodb://user:pass@host:port/...
    if "://" not in raw:
        raw = "mongodb://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname
    port = parsed.port
    if port is None and parsed.netloc:
        # replica set / multi-host — scan for :27018 / :27017
        if f":{REQUIRED_PORT}" in parsed.netloc:
            port = REQUIRED_PORT
        elif f":{FORBIDDEN_PORT}" in parsed.netloc:
            port = FORBIDDEN_PORT
    return host, port


def require_safe_db_target(
    *,
    app_env: str,
    mongodb_uri: str,
    db_name: str,
    require_production: bool,
) -> dict[str, Any]:
    """Fail-closed DB/env gate for write path (and optional RO production)."""
    if require_production and app_env != "production":
        raise ValueError(f"app_env_not_production:{app_env}")
    if db_name != REQUIRED_DB:
        raise ValueError(f"db_name_mismatch:{db_name}")
    host, port = parse_mongo_host_port(mongodb_uri)
    if port == FORBIDDEN_PORT:
        raise ValueError("mongo_port_legacy_27017_forbidden")
    if port != REQUIRED_PORT:
        raise ValueError(f"mongo_port_not_27018:{port}")
    if "27017" in mongodb_uri:
        raise ValueError("mongo_uri_contains_27017")
    return {"db_name": db_name, "host": host, "port": port, "app_env": app_env}


def load_backup_meta(path: Path, *, max_age_hours: float = 24.0) -> dict[str, Any]:
    """Backup meta: archive file wajib ada; SHA256 wajib cocok; DB/port/scope/time."""
    if not path.is_file():
        raise ValueError(f"backup_meta_missing:{path}")
    meta = json.loads(path.read_text())
    if not isinstance(meta, dict):
        raise ValueError("backup_meta_invalid")
    sha = str(meta.get("archive_sha256") or meta.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("backup_sha256_missing")
    db = str(meta.get("db") or meta.get("db_name") or "")
    if db != REQUIRED_DB:
        raise ValueError(f"backup_db_mismatch:{db}")
    port = meta.get("port") or meta.get("mongo_port")
    try:
        port_i = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"backup_port_invalid:{port}") from exc
    if port_i != REQUIRED_PORT:
        raise ValueError(f"backup_port_mismatch:{port}")
    scope = str(meta.get("scope") or meta.get("PURPOSE") or meta.get("purpose") or "").lower()
    # Scope must explicitly name V4 DB dump (refuse ambiguous/legacy).
    if scope and ("legacy" in scope or "27017" in scope):
        raise ValueError(f"backup_scope_forbidden:{scope}")
    if meta.get("db_scope") and str(meta.get("db_scope")) != REQUIRED_DB:
        raise ValueError(f"backup_db_scope_mismatch:{meta.get('db_scope')}")
    created = meta.get("created_at") or meta.get("CREATED_AT")
    if not created:
        raise ValueError("backup_created_at_missing")
    ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 3600.0
    if age_h < 0 or age_h > max_age_hours:
        raise ValueError(f"backup_stale_or_future:age_h={age_h:.2f}")
    archive = meta.get("archive_path") or meta.get("path")
    if not archive:
        raise ValueError("backup_archive_path_missing")
    ap = Path(str(archive))
    if not ap.is_file():
        raise ValueError(f"backup_archive_missing:{ap}")
    live_sha = file_sha256(ap)
    if live_sha != sha:
        raise ValueError("backup_sha256_mismatch_vs_file")
    return {
        **meta,
        "archive_sha256": sha,
        "archive_path": str(ap),
        "age_hours": age_h,
        "port": port_i,
        "db": db,
    }


def require_snapshot_args(
    *, expect_total_po: int | None, expect_missing_po: int | None
) -> tuple[int, int]:
    """Production apply wajib memasok kedua argumen snapshot (integer >= 0)."""
    if expect_total_po is None or expect_missing_po is None:
        raise ValueError("snapshot_args_required")
    if int(expect_total_po) < 0 or int(expect_missing_po) < 0:
        raise ValueError("snapshot_args_invalid")
    if int(expect_missing_po) > int(expect_total_po):
        raise ValueError("snapshot_missing_gt_total")
    return int(expect_total_po), int(expect_missing_po)


def assert_snapshot_and_manifest_consistency(
    db: Any,
    *,
    items: list[dict[str, Any]],
    expect_total_po: int,
    expect_missing_po: int,
) -> dict[str, Any]:
    """Fail-closed: total/missing cocok; semua kandidat masih tanpa branch_id."""
    col = db["accurate_sales_orders"]
    total = col.count_documents({})
    missing_filter = {
        "$or": [
            {"branch_id": {"$exists": False}},
            {"branch_id": None},
            {"branch_id": ""},
        ]
    }
    missing = col.count_documents(missing_filter)
    if total != expect_total_po:
        raise ValueError(f"snapshot_total_drift:{total}!={expect_total_po}")
    if missing != expect_missing_po:
        raise ValueError(f"snapshot_missing_drift:{missing}!={expect_missing_po}")
    candidate_ids = [str(it["accurate_id"]) for it in items]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("manifest_duplicate_ids")
    # Kandidat tidak boleh bertambah di luar sealed list (sudah di-load).
    # Kandidat tidak boleh hilang: setiap id masih missing branch.
    still_missing = 0
    for aid in candidate_ids:
        doc = col.find_one({"accurate_id": aid}, {"branch_id": 1})
        if doc is None:
            raise ValueError(f"manifest_candidate_missing_in_db:{aid}")
        bid = doc.get("branch_id")
        if bid in (None, ""):
            still_missing += 1
        else:
            raise ValueError(f"manifest_candidate_already_branched:{aid}:{bid}")
    if still_missing != len(candidate_ids):
        raise ValueError("manifest_candidate_set_drift")
    if len(candidate_ids) > missing:
        raise ValueError("manifest_larger_than_missing")
    return {
        "total": total,
        "missing": missing,
        "candidates": len(candidate_ids),
    }


def load_maintenance_lock(path: Path, *, max_age_hours: float = 4.0) -> dict[str, Any]:
    """Operator-issued proof that PO sync writers are stopped."""
    if not path.is_file():
        raise ValueError(f"maintenance_lock_missing:{path}")
    lock = json.loads(path.read_text())
    if not isinstance(lock, dict):
        raise ValueError("maintenance_lock_invalid")
    if not lock.get("workers_cleared"):
        raise ValueError("maintenance_lock_workers_not_cleared")
    if lock.get("po_auto_sync") not in (False, 0, "0", "false", "off"):
        raise ValueError("maintenance_lock_po_auto_sync_not_off")
    token = str(lock.get("token") or "").strip()
    if len(token) < 16:
        raise ValueError("maintenance_lock_token_weak")
    created = lock.get("created_at")
    if not created:
        raise ValueError("maintenance_lock_created_at_missing")
    ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 3600.0
    if age_h < 0 or age_h > max_age_hours:
        raise ValueError(f"maintenance_lock_stale:age_h={age_h:.2f}")
    return lock


def require_auto_sync_flags_off(settings: Any) -> dict[str, bool]:
    """Fail-closed: Settings sync flags harus ada, bool, dan False.

    Tidak boleh ``getattr(..., False)`` — atribut hilang/unreadable/invalid
    wajib REFUSE (bukan dianggap OFF).
    """
    if settings is None:
        raise ValueError("auto_sync_settings_missing")
    verified: dict[str, bool] = {}
    for name in REQUIRED_AUTO_SYNC_OFF_FIELDS:
        # Jangan pakai hasattr(): property yang raise ikut terpropagasi.
        try:
            raw = getattr(settings, name)
        except AttributeError as exc:
            raise ValueError(f"auto_sync_flag_missing:{name}") from exc
        except Exception as exc:  # noqa: BLE001 — fail-closed on any read error
            raise ValueError(
                f"auto_sync_flag_unreadable:{name}:{type(exc).__name__}"
            ) from exc
        if type(raw) is not bool:
            raise ValueError(f"auto_sync_flag_invalid:{name}:{type(raw).__name__}")
        if raw is True:
            raise ValueError(f"auto_sync_must_be_off:{name}")
        verified[name] = False
    return verified


def assert_no_po_sync_worker() -> None:
    """Best-effort process check — terpisah dari env-flag gate; bukan pengganti lock."""
    try:
        out = subprocess.check_output(["ps", "-eo", "args="], text=True, timeout=10)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        raise ValueError("cannot_verify_workers")
    for line in out.splitlines():
        if "accurate_auto_sync_worker.py" in line:
            # Flag OFF ≠ worker sudah berhenti; tolak job Accurate yang masih hidup.
            if any(
                token in line
                for token in (
                    "--job po",
                    "--job invoice",
                    "--job receipt",
                    "--job all",
                )
            ):
                raise ValueError("accurate_auto_sync_worker_running")
        if "po_branch_repair_apply.py" in line and "--execute" in line:
            # allow self
            continue


def sealed_manifest_from_verify_report(report: dict[str, Any]) -> dict[str, Any]:
    items = list(report.get("valid_repair_candidates") or [])
    checksum = candidates_checksum(items)
    return {
        "ok": True,
        "sealed": True,
        "kind": "valid_repair_candidates_v1",
        "manifest_sha256": checksum,
        "candidate_count": len(items),
        "valid_repair_candidates": json.loads(canonical_candidates_payload(items)),
        "source_report_generated_at": report.get("generated_at"),
        "delta_sha256": report.get("delta_sha256"),
        "notes": [
            "Immutable candidate list — apply refuses foreign accurate_id.",
            "Checksum covers accurate_id+expected_branch_id+trans_date+po_no only.",
        ],
    }


def load_sealed_candidates(
    path: Path,
    *,
    expect_sha256: str,
    expect_count: int,
) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("candidates_not_object")
    if not raw.get("sealed"):
        raise ValueError("candidates_not_sealed")
    items = raw.get("valid_repair_candidates")
    if not isinstance(items, list):
        raise ValueError("candidates_missing_list")
    if len(items) != int(expect_count):
        raise ValueError(f"candidate_count_mismatch:{len(items)}!={expect_count}")
    calc = candidates_checksum(items)
    declared = str(raw.get("manifest_sha256") or "").lower()
    expect = str(expect_sha256).lower()
    if declared != calc:
        raise ValueError("manifest_internal_checksum_mismatch")
    if expect != calc:
        raise ValueError(f"manifest_sha256_mismatch:expect={expect}:calc={calc}")
    # Reject foreign/extra fields that could smuggle ids
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("candidate_item_invalid")
        aid = str(it.get("accurate_id") or "")
        branch = str(it.get("expected_branch_id") or "")
        if not aid or branch not in KNOWN_BRANCHES:
            raise ValueError(f"candidate_invalid:{aid}:{branch}")
        if aid in seen:
            raise ValueError(f"candidate_duplicate:{aid}")
        seen.add(aid)
        clean.append(
            {
                "accurate_id": aid,
                "expected_branch_id": branch,
                "accurate_trans_date": it.get("accurate_trans_date"),
                "po_no": it.get("po_no"),
            }
        )
    return clean


def prewrite_block_reason(
    db: Any,
    *,
    accurate_id: str,
    expected_branch_id: str,
    expected_po_no: str | None,
    expected_trans_date: Any,
) -> str | None:
    """Immediate pre-write checks. None = may attempt CAS."""
    from app.services.accurate_auto_sync.date_floor import parse_accurate_trans_date
    from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, is_po_accepted
    from app.services.po_ingest_guards import po_has_operational_dependencies

    aid = str(accurate_id)
    doc = db["accurate_sales_orders"].find_one(
        {"accurate_id": aid},
        {
            "branch_id": 1,
            "po_no": 1,
            "header.po_date": 1,
            "operational.acceptance.status": 1,
        },
    )
    if doc is None:
        return "po_missing"
    if doc.get("branch_id"):
        return "already_has_branch"
    if is_po_accepted(doc):
        return "po_accepted"
    acc = ((doc.get("operational") or {}).get("acceptance") or {}).get("status")
    if acc == ACCEPTANCE_ACCEPTED:
        return "po_accepted"
    if po_has_operational_dependencies(db, aid):
        return "operational_dependency_present"
    if expected_po_no and str(doc.get("po_no") or "") != str(expected_po_no):
        return "po_no_drift"
    stored_date = (doc.get("header") or {}).get("po_date")
    if expected_trans_date is not None:
        exp = parse_accurate_trans_date(expected_trans_date)
        got = parse_accurate_trans_date(stored_date)
        if exp is None or got is None or exp != got:
            return "trans_date_drift"
    if expected_branch_id not in KNOWN_BRANCHES:
        return "expected_branch_invalid"
    master = db["master_branches"].find_one(
        {"code": expected_branch_id}, {"is_active": 1}
    )
    if master is None or not master.get("is_active"):
        return "master_branch_inactive"
    return None
