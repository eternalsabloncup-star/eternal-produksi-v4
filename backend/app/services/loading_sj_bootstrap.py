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
# KP (Owner final): view/confirm/edit/cancel — confirm WAJIB, bukan wildcard lintas task.
PRODUCTION_HEAD_ACTIONS = ("view", "confirm", "edit", "cancel")
# Kontrak lama (pra-M5 reconciliation) — hanya CASE C yang diizinkan di-reconcile.
LEGACY_PRODUCTION_HEAD_ACTIONS = ("view", "edit", "cancel")
# Driver / Admin / Director: tidak ada grant otomatis loading-sj.
FORBIDDEN_AUTO_GRANT_ROLES = frozenset({"driver", "admin", "director", "super_admin"})

RECONCILE_RESULT_MISSING = "MISSING"
RECONCILE_RESULT_NO_OP = "NO_OP"
RECONCILE_RESULT_RECONCILED = "RECONCILED"
RECONCILE_RESULT_HOLD = "HOLD"


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


def _actions_set_equal(
    got: list[str] | tuple[str, ...] | None, expected: tuple[str, ...]
) -> bool:
    return _sorted_actions(got) == _sorted_actions(list(expected))


def classify_production_head_loading_sj_grant(db: Database) -> dict[str, Any]:
    """Audit read-only grant production-head/loading-sj → case A–D."""
    coll = master.role_actions_collection(db)
    docs = list(
        coll.find(
            {"role": "production-head", "task_type": LOADING_SJ_TASK_TYPE},
            {"_id": 1, "role": 1, "task_type": 1, "actions": 1, "is_default_mapping": 1},
        )
    )
    if len(docs) > 1:
        return {
            "case": "D",
            "result": RECONCILE_RESULT_HOLD,
            "reason": "Dokumen grant production-head/loading-sj duplikat/ambigu.",
            "code": "LOADING_SJ_KP_GRANT_AMBIGUOUS",
            "count": len(docs),
            "before": None,
        }
    if not docs:
        return {
            "case": "A",
            "result": RECONCILE_RESULT_MISSING,
            "reason": "Grant production-head/loading-sj belum ada.",
            "code": "LOADING_SJ_KP_GRANT_MISSING",
            "count": 0,
            "before": None,
        }
    doc = docs[0]
    actions = _sorted_actions(doc.get("actions"))
    before = {
        "role": doc.get("role"),
        "task_type": doc.get("task_type"),
        "actions": actions,
        "is_default_mapping": doc.get("is_default_mapping"),
    }
    if _actions_set_equal(actions, PRODUCTION_HEAD_ACTIONS):
        return {
            "case": "B",
            "result": RECONCILE_RESULT_NO_OP,
            "reason": "Grant sudah sesuai kontrak Owner.",
            "code": "LOADING_SJ_KP_GRANT_OK",
            "count": 1,
            "before": before,
        }
    if _actions_set_equal(actions, LEGACY_PRODUCTION_HEAD_ACTIONS):
        return {
            "case": "C",
            "result": "LEGACY",
            "reason": "Grant versi lama (view/edit/cancel) — boleh tambah confirm.",
            "code": "LOADING_SJ_KP_GRANT_LEGACY",
            "count": 1,
            "before": before,
        }
    return {
        "case": "D",
        "result": RECONCILE_RESULT_HOLD,
        "reason": (
            f"Grant custom/berbeda: actions={actions!r} "
            f"(bukan legacy {list(LEGACY_PRODUCTION_HEAD_ACTIONS)!r} "
            f"dan bukan target {list(PRODUCTION_HEAD_ACTIONS)!r})."
        ),
        "code": "LOADING_SJ_KP_GRANT_CUSTOM",
        "count": 1,
        "before": before,
    }


def reconcile_production_head_loading_sj_grant(
    db: Database,
    *,
    expected_db_name: str,
    allow_production: bool = False,
    approved_release: bool = False,
) -> dict[str, Any]:
    """Reconciliation terbatas CASE C — hanya di approved release path.

    Menambah ``confirm`` ke grant KP legacy (view/edit/cancel) via CAS atomic.
    Tidak mengubah Helper/Driver/Admin. Tidak full seed. Tidak createIndex.
    """
    assert_loading_sj_db_target(
        db,
        expected_db_name=expected_db_name,
        allow_production=allow_production,
    )
    try:
        master.require_master_indexes(db)
    except Exception as exc:  # noqa: BLE001 — index preflight fail-closed
        return {
            "ok": False,
            "result": RECONCILE_RESULT_HOLD,
            "case": "E",
            "code": "LOADING_SJ_RECONCILE_PREFLIGHT",
            "reason": f"Preflight index/Master gagal: {exc}",
            "changed": False,
        }

    if not approved_release:
        return {
            "ok": False,
            "result": RECONCILE_RESULT_HOLD,
            "case": "E",
            "code": "LOADING_SJ_RECONCILE_NOT_APPROVED",
            "reason": (
                "Reconciliation hanya boleh pada approved release path "
                "(approved_release=True)."
            ),
            "changed": False,
            "classification": classify_production_head_loading_sj_grant(db),
        }

    classification = classify_production_head_loading_sj_grant(db)
    case = classification["case"]
    before = classification.get("before")

    if case == "A":
        return {
            "ok": True,
            "result": RECONCILE_RESULT_MISSING,
            "case": "A",
            "code": classification["code"],
            "reason": classification["reason"],
            "changed": False,
            "before": before,
            "after": None,
            "note": "Bootstrap yang membuat grant; reconcile tidak insert.",
        }
    if case == "B":
        return {
            "ok": True,
            "result": RECONCILE_RESULT_NO_OP,
            "case": "B",
            "code": classification["code"],
            "reason": classification["reason"],
            "changed": False,
            "before": before,
            "after": before,
        }
    if case == "D":
        return {
            "ok": False,
            "result": RECONCILE_RESULT_HOLD,
            "case": "D",
            "code": classification["code"],
            "reason": classification["reason"],
            "changed": False,
            "before": before,
            "after": before,
        }

    # CASE C — legacy → tambah confirm saja (CAS).
    from pymongo import ReturnDocument

    now = datetime.now(timezone.utc)
    target_actions = list(PRODUCTION_HEAD_ACTIONS)
    coll = master.role_actions_collection(db)
    # Exact-set match: $all + $size (order-insensitive) pada kontrak lama.
    legacy_filter: dict[str, Any] = {
        "role": "production-head",
        "task_type": LOADING_SJ_TASK_TYPE,
        "actions": {
            "$all": list(LEGACY_PRODUCTION_HEAD_ACTIONS),
            "$size": len(LEGACY_PRODUCTION_HEAD_ACTIONS),
        },
    }
    updated = coll.find_one_and_update(
        legacy_filter,
        {
            "$set": {
                "actions": target_actions,
                "updated_at": now,
                "reconciled_at": now,
                "reconciled_from": "legacy_kp_loading_sj_no_confirm",
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        # Possible race / concurrent modification — reclassify.
        again = classify_production_head_loading_sj_grant(db)
        if again["case"] == "B":
            # Concurrent actor already reconciled — idempotent NO_OP.
            return {
                "ok": True,
                "result": RECONCILE_RESULT_NO_OP,
                "case": "B",
                "code": "LOADING_SJ_KP_RECONCILE_RACE_OK",
                "reason": "Setelah CAS miss, grant sudah sesuai kontrak (race aman).",
                "changed": False,
                "before": before,
                "after": again.get("before"),
            }
        return {
            "ok": False,
            "result": RECONCILE_RESULT_HOLD,
            "case": "D" if again["case"] == "D" else "E",
            "code": "LOADING_SJ_KP_RECONCILE_CAS_MISS",
            "reason": (
                "CAS gagal — dokumen berubah di tengah proses atau bukan legacy. "
                f"class={again.get('code')}: {again.get('reason')}"
            ),
            "changed": False,
            "before": before,
            "after": again.get("before"),
            "classification_after": again,
        }

    after_doc = {
        "role": updated.get("role"),
        "task_type": updated.get("task_type"),
        "actions": _sorted_actions(updated.get("actions")),
        "is_default_mapping": updated.get("is_default_mapping"),
        "reconciled_from": updated.get("reconciled_from"),
    }
    # Verifikasi kontrak grant KP (bukan full master bila helper belum ada).
    if not _actions_set_equal(after_doc["actions"], PRODUCTION_HEAD_ACTIONS):
        return {
            "ok": False,
            "result": RECONCILE_RESULT_HOLD,
            "case": "E",
            "code": "LOADING_SJ_KP_RECONCILE_VERIFY_FAIL",
            "reason": f"Setelah update, actions={after_doc['actions']!r} ≠ target.",
            "changed": True,
            "before": before,
            "after": after_doc,
            "rollback_claimed": False,
        }

    return {
        "ok": True,
        "result": RECONCILE_RESULT_RECONCILED,
        "case": "C",
        "code": "LOADING_SJ_KP_RECONCILED",
        "reason": "Menambahkan confirm ke grant KP legacy (CAS).",
        "changed": True,
        "before": before,
        "after": after_doc,
    }


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
    needs_reconcile: list[str] = []

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
            # KP legacy: jangan overwrite di bootstrap — defer ke reconciliation release.
            if role == "production-head" and _actions_set_equal(
                existing.get("actions"), LEGACY_PRODUCTION_HEAD_ACTIONS
            ):
                skipped.append("grant_production-head_legacy_needs_reconcile")
                needs_reconcile.append("production-head")
                return
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
    contract_verified = False
    if not needs_reconcile:
        verify_loading_sj_contract(db)
        contract_verified = True

    # Non-target: hitung task type lain tidak berubah jumlah (sanity).
    other_types = types_coll.count_documents(
        {"task_type": {"$ne": LOADING_SJ_TASK_TYPE}}
    )

    return {
        "ok": True,
        "noop": inserted_type == 0
        and inserted_grants == 0
        and not needs_reconcile,
        "inserted_task_types": inserted_type,
        "inserted_role_actions": inserted_grants,
        "skipped": skipped,
        "stopped": stopped,
        "needs_reconcile": needs_reconcile,
        "contract_verified": contract_verified,
        "before": before,
        "after": after,
        "non_target_task_type_count": other_types,
        "db_name": db.name,
    }
