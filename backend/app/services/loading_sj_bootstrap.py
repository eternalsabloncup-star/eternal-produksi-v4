"""Bootstrap terkontrol Master ``loading-sj`` + action grants (M5).

Hanya maintenance/release — bukan request path, bukan full seed.
Idempotent via $setOnInsert; STOP bila existing berbeda secara material.
Tidak createIndex baru; tidak mengubah task type / role lain.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from app.db_guard import _LEGACY_OR_OPS_DB_NAMES
from app.domain.operational import DEFAULT_TASK_TYPE_BY_ID
from app.repositories import master_repository as master

LOADING_SJ_TASK_TYPE = "loading-sj"

# Kontrak Owner (least privilege).
EXPECTED_LABEL = "Loading Surat Jalan"
EXPECTED_SCOPE = "delivery_note"
EXPECTED_ASSIGNED_ROLE = "helper"
# Spec Master menyimpan cancel; grant Helper tidak mendapat cancel.
EXPECTED_TASK_ACTIONS = ("view", "confirm", "edit", "cancel")
HELPER_ACTIONS = ("view", "confirm", "edit")
# KP: view/edit/cancel — confirm tidak di-expand otomatis.
PRODUCTION_HEAD_ACTIONS = ("view", "edit", "cancel")
# Driver / Admin / Director: tidak ada grant otomatis loading-sj.
FORBIDDEN_AUTO_GRANT_ROLES = frozenset({"driver", "admin", "director", "super_admin"})


class LoadingSjBootstrapError(RuntimeError):
    """Bootstrap loading-sj ditolak (target DB / material mismatch / index)."""

    code = "LOADING_SJ_BOOTSTRAP_REJECTED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


def _sorted_actions(actions: list[str] | tuple[str, ...] | None) -> list[str]:
    return sorted({str(a) for a in (actions or []) if str(a).strip()})


def _spec() -> Any:
    spec = DEFAULT_TASK_TYPE_BY_ID.get(LOADING_SJ_TASK_TYPE)
    if spec is None:
        raise LoadingSjBootstrapError(
            "Spek Master loading-sj tidak ada di DEFAULT_TASK_TYPES.",
            code="LOADING_SJ_SPEC_MISSING",
        )
    return spec


def assert_loading_sj_db_target(
    db: Database,
    *,
    expected_db_name: str,
    allow_production: bool = False,
) -> None:
    """Tolak DB salah / legacy. Production hanya dengan allow_production eksplisit."""
    name = str(db.name or "").strip()
    expected = str(expected_db_name or "").strip()
    if not expected:
        raise LoadingSjBootstrapError(
            "expected_db_name wajib (target database eksplisit).",
            code="LOADING_SJ_DB_TARGET_MISSING",
        )
    if name != expected:
        raise LoadingSjBootstrapError(
            f"DB aktual {name!r} ≠ expected_db_name {expected!r}.",
            code="LOADING_SJ_DB_TARGET_MISMATCH",
        )
    if name in _LEGACY_OR_OPS_DB_NAMES or name == "eternal_v4":
        if not allow_production:
            raise LoadingSjBootstrapError(
                f"Database ops/production {name!r} ditolak tanpa allow_production.",
                code="LOADING_SJ_DB_PRODUCTION_BLOCKED",
            )
        return
    if not (
        name.startswith("eternal_v4_test_")
        or name.startswith("eternal_produksi_v4_test")
        or allow_production
    ):
        raise LoadingSjBootstrapError(
            f"Database {name!r} bukan target test terisolasi dan "
            "bukan production yang diizinkan.",
            code="LOADING_SJ_DB_REJECTED",
        )


def audit_loading_sj(db: Database) -> dict[str, Any]:
    """Snapshot read-only Master + grants loading-sj (tanpa seed/index create)."""
    task = master.get_task_type(db, LOADING_SJ_TASK_TYPE)
    grants = [
        g
        for g in master.role_actions_collection(db).find(
            {"task_type": LOADING_SJ_TASK_TYPE}, {"_id": 0}
        )
    ]
    grants_sorted = sorted(grants, key=lambda g: (g.get("role") or "",))
    helper = next((g for g in grants_sorted if g.get("role") == "helper"), None)
    kp = next(
        (g for g in grants_sorted if g.get("role") == "production-head"), None
    )
    driver = next((g for g in grants_sorted if g.get("role") == "driver"), None)
    return {
        "task_type": task,
        "grants": grants_sorted,
        "helper_actions": _sorted_actions((helper or {}).get("actions")),
        "production_head_actions": _sorted_actions((kp or {}).get("actions")),
        "driver_actions": _sorted_actions((driver or {}).get("actions")),
        "driver_has_grant": driver is not None
        and bool(_sorted_actions((driver or {}).get("actions"))),
        "present": task is not None,
    }


def _material_task_mismatch(existing: dict[str, Any]) -> str | None:
    label = str(existing.get("label") or "")
    scope = str(existing.get("scope") or "")
    role = str(existing.get("assigned_role") or "")
    actions = _sorted_actions(existing.get("actions"))
    expected_actions = _sorted_actions(list(EXPECTED_TASK_ACTIONS))
    if label != EXPECTED_LABEL:
        return f"label {label!r} ≠ {EXPECTED_LABEL!r}"
    if scope != EXPECTED_SCOPE:
        return f"scope {scope!r} ≠ {EXPECTED_SCOPE!r}"
    if role != EXPECTED_ASSIGNED_ROLE:
        return f"assigned_role {role!r} ≠ {EXPECTED_ASSIGNED_ROLE!r}"
    if actions != expected_actions:
        return f"actions {actions!r} ≠ {expected_actions!r}"
    return None


def _material_grant_mismatch(
    existing: dict[str, Any] | None, *, expected: tuple[str, ...]
) -> str | None:
    if existing is None:
        return None
    got = _sorted_actions(existing.get("actions"))
    want = _sorted_actions(list(expected))
    if got != want:
        return f"actions {got!r} ≠ {want!r}"
    return None


def verify_loading_sj_contract(db: Database) -> dict[str, Any]:
    """Validasi kontrak Owner; raise jika Master/grants tidak valid."""
    audit = audit_loading_sj(db)
    task = audit["task_type"]
    if task is None:
        raise LoadingSjBootstrapError(
            "Master loading-sj belum ada.",
            code="LOADING_SJ_MASTER_MISSING",
        )
    mismatch = _material_task_mismatch(task)
    if mismatch:
        raise LoadingSjBootstrapError(
            f"Master loading-sj tidak sesuai kontrak: {mismatch}",
            code="LOADING_SJ_MASTER_MISMATCH",
        )
    helper = next(
        (g for g in audit["grants"] if g.get("role") == "helper"), None
    )
    if helper is None:
        raise LoadingSjBootstrapError(
            "Grant helper/loading-sj belum ada.",
            code="LOADING_SJ_HELPER_GRANT_MISSING",
        )
    hm = _material_grant_mismatch(helper, expected=HELPER_ACTIONS)
    if hm:
        raise LoadingSjBootstrapError(
            f"Grant helper/loading-sj tidak valid: {hm}",
            code="LOADING_SJ_HELPER_GRANT_INVALID",
        )
    # Helper wajib tanpa cancel.
    if "cancel" in audit["helper_actions"]:
        raise LoadingSjBootstrapError(
            "Helper tidak boleh punya cancel pada loading-sj.",
            code="LOADING_SJ_HELPER_CANCEL_FORBIDDEN",
        )
    kp = next(
        (g for g in audit["grants"] if g.get("role") == "production-head"),
        None,
    )
    if kp is None:
        raise LoadingSjBootstrapError(
            "Grant production-head/loading-sj belum ada.",
            code="LOADING_SJ_KP_GRANT_MISSING",
        )
    km = _material_grant_mismatch(kp, expected=PRODUCTION_HEAD_ACTIONS)
    if km:
        raise LoadingSjBootstrapError(
            f"Grant production-head/loading-sj tidak valid: {km}",
            code="LOADING_SJ_KP_GRANT_INVALID",
        )
    if audit["driver_has_grant"]:
        raise LoadingSjBootstrapError(
            "Driver tidak boleh mendapat grant loading-sj otomatis.",
            code="LOADING_SJ_DRIVER_GRANT_FORBIDDEN",
        )
    return {"ok": True, "audit": audit}


def bootstrap_loading_sj_master(
    db: Database,
    *,
    expected_db_name: str,
    allow_production: bool = False,
) -> dict[str, Any]:
    """Bootstrap satu Master loading-sj + grants Owner — idempotent.

    - Tidak full seed.
    - Tidak createIndex (require existing Master indexes).
    - Tidak overwrite material mismatch / custom mapping berbeda.
    - Tidak menghapus grant existing non-target.
    """
    assert_loading_sj_db_target(
        db,
        expected_db_name=expected_db_name,
        allow_production=allow_production,
    )
    # Fail-closed: indeks Master harus sudah ada (bukan dibuat di sini).
    master.require_master_indexes(db)

    before = audit_loading_sj(db)
    now = datetime.now(timezone.utc)
    types_coll = master.task_types_collection(db)
    actions_coll = master.role_actions_collection(db)
    spec = _spec()

    inserted_type = 0
    inserted_grants = 0
    skipped: list[str] = []
    stopped: list[str] = []

    existing_type = before["task_type"]
    if existing_type is None:
        doc = master._spec_to_doc(spec, now)
        # Pastikan kontrak eksplisit (bukan mengandalkan drift DEFAULT).
        doc["label"] = EXPECTED_LABEL
        doc["scope"] = EXPECTED_SCOPE
        doc["assigned_role"] = EXPECTED_ASSIGNED_ROLE
        doc["actions"] = list(EXPECTED_TASK_ACTIONS)
        res = types_coll.update_one(
            {"task_type": LOADING_SJ_TASK_TYPE},
            {"$setOnInsert": {**doc, "created_at": now}},
            upsert=True,
        )
        if res.upserted_id is not None:
            inserted_type = 1
        else:
            # Race: re-read and validate.
            existing_type = master.get_task_type(db, LOADING_SJ_TASK_TYPE)
    else:
        # Custom mapping: jangan timpa; STOP bila material beda.
        is_custom = existing_type.get("is_default_mapping") is False
        mismatch = _material_task_mismatch(existing_type)
        if mismatch:
            raise LoadingSjBootstrapError(
                (
                    "Master loading-sj existing berbeda secara material — "
                    f"STOP tanpa overwrite ({mismatch}). "
                    f"custom={is_custom}."
                ),
                code="LOADING_SJ_MASTER_MATERIAL_MISMATCH",
            )
        skipped.append("task_type_unchanged")

    def _upsert_grant(role: str, actions: tuple[str, ...]) -> None:
        nonlocal inserted_grants
        existing = actions_coll.find_one(
            {"role": role, "task_type": LOADING_SJ_TASK_TYPE}, {"_id": 0}
        )
        if existing is not None:
            mismatch = _material_grant_mismatch(existing, expected=actions)
            if mismatch:
                is_custom = existing.get("is_default_mapping") is False
                raise LoadingSjBootstrapError(
                    (
                        f"Grant {role}/loading-sj existing berbeda — "
                        f"STOP tanpa overwrite ({mismatch}). custom={is_custom}."
                    ),
                    code="LOADING_SJ_GRANT_MATERIAL_MISMATCH",
                )
            skipped.append(f"grant_{role}_unchanged")
            return
        res = actions_coll.update_one(
            {"role": role, "task_type": LOADING_SJ_TASK_TYPE},
            {
                "$setOnInsert": {
                    "role": role,
                    "task_type": LOADING_SJ_TASK_TYPE,
                    "actions": list(actions),
                    "is_default_mapping": True,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        if res.upserted_id is not None:
            inserted_grants += 1
        else:
            skipped.append(f"grant_{role}_race_noop")

    _upsert_grant("helper", HELPER_ACTIONS)
    _upsert_grant("production-head", PRODUCTION_HEAD_ACTIONS)

    # Pastikan tidak ada grant driver dengan aksi (kontrak).
    driver_doc = actions_coll.find_one(
        {"role": "driver", "task_type": LOADING_SJ_TASK_TYPE}, {"_id": 0}
    )
    if driver_doc and _sorted_actions(driver_doc.get("actions")):
        raise LoadingSjBootstrapError(
            "Driver memiliki grant loading-sj — STOP (least privilege).",
            code="LOADING_SJ_DRIVER_GRANT_PRESENT",
        )

    after = audit_loading_sj(db)
    # Verifikasi kontrak akhir.
    verify_loading_sj_contract(db)

    # Non-target: hitung task type lain tidak berubah jumlah (sanity).
    other_types = types_coll.count_documents(
        {"task_type": {"$ne": LOADING_SJ_TASK_TYPE}}
    )

    return {
        "ok": True,
        "noop": inserted_type == 0 and inserted_grants == 0,
        "inserted_task_types": inserted_type,
        "inserted_role_actions": inserted_grants,
        "skipped": skipped,
        "stopped": stopped,
        "before": before,
        "after": after,
        "non_target_task_type_count": other_types,
        "db_name": db.name,
    }
