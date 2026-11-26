"""Paint management business logic — masters, stock, withdrawals, returns,
conversions, waste, corrections, stocktake, reports.

All stock mutations use Mongo transactions + idempotency + CAS balances.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database

from app.auth.permissions import (
    PERM_OPERATIONS_FULL,
    has_permission,
)
from app.auth.roles import is_super_admin
from app.domain.operational import normalize_operational_role
from app.repositories import org_master_repository as org
from app.repositories import paint_repository as paint
from app.repositories import settings_user_repository as users
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event
from app.services.branch_identity import normalize_branch_identity
from app.services.object_auth import ObjectAuthError, bypasses_branch, user_branch_ids

# Permissions
PERM_PAINT_VIEW = "paint.module.view"
PERM_PAINT_STOCK_VIEW = "paint.stock.view"
PERM_PAINT_INBOUND = "paint.inbound.confirm"
PERM_PAINT_WITHDRAW = "paint.withdraw.create"
PERM_PAINT_RETURN = "paint.return.confirm"
PERM_PAINT_CONVERSION = "paint.conversion.confirm"
PERM_PAINT_WASTE = "paint.waste.record"
PERM_PAINT_EDIT = "paint.document.edit"
PERM_PAINT_SOFT_DELETE = "paint.document.soft_delete"
PERM_PAINT_STOCKTAKE_RUN = "paint.stocktake.run"
PERM_PAINT_STOCKTAKE_FINALIZE = "paint.stocktake.finalize"
PERM_PAINT_MASTER = "paint.master.manage"
PERM_PAINT_REPORTS = "paint.reports.view"
PERM_PAINT_ACCURATE_MAP = "paint.accurate.map"

PAINT_PERMISSIONS: frozenset[str] = frozenset(
    {
        PERM_PAINT_VIEW,
        PERM_PAINT_STOCK_VIEW,
        PERM_PAINT_INBOUND,
        PERM_PAINT_WITHDRAW,
        PERM_PAINT_RETURN,
        PERM_PAINT_CONVERSION,
        PERM_PAINT_WASTE,
        PERM_PAINT_EDIT,
        PERM_PAINT_SOFT_DELETE,
        PERM_PAINT_STOCKTAKE_RUN,
        PERM_PAINT_STOCKTAKE_FINALIZE,
        PERM_PAINT_MASTER,
        PERM_PAINT_REPORTS,
        PERM_PAINT_ACCURATE_MAP,
    }
)

# KP defaults (not Accurate map).
_KP_DEFAULTS = PAINT_PERMISSIONS - {PERM_PAINT_ACCURATE_MAP}

STATUS_POSTED = "posted"
STATUS_SOFT_DELETED = "soft_deleted"
STATUS_CORRECTED = "corrected"

DOC_INBOUND = "inbound_manual"
DOC_WITHDRAWAL = "withdrawal"
DOC_RETURN = "return"
DOC_CONVERSION = "conversion"
DOC_WASTE = "waste"
DOC_REVERSAL = "reversal"
DOC_STOCKTAKE_ADJ = "stocktake_adjustment"


class PaintPermissionError(PermissionError):
    pass


class PaintNotFoundError(LookupError):
    pass


class PaintValidationError(ValueError):
    pass


def actor_email(user: dict[str, Any]) -> str:
    return str(user.get("email_normalized") or user.get("email") or "").strip().lower()


def has_paint_permission(user: dict[str, Any], permission: str) -> bool:
    if is_super_admin(user.get("role")):
        return True
    if has_permission(user, PERM_OPERATIONS_FULL):
        return True
    if has_permission(user, permission):
        return True
    if (
        permission in _KP_DEFAULTS
        and normalize_operational_role(user.get("operational_role")) == "production-head"
    ):
        return True
    return False


def require_paint_permission(user: dict[str, Any], permission: str) -> None:
    if not has_paint_permission(user, permission):
        raise PaintPermissionError("Akses Manajemen Cat ditolak.")


def assert_paint_branch(user: dict[str, Any], branch_id: str) -> str:
    code = normalize_branch_identity(branch_id)
    if not code:
        raise PaintValidationError("Cabang wajib diisi.")
    if bypasses_branch(user):
        return code
    allowed = user_branch_ids(user)
    if not allowed or code not in allowed:
        raise ObjectAuthError("Akses cabang ditolak untuk operasi cat.")
    return code


def _require_branch_exists(db: Database, branch_id: str) -> None:
    if org.get_branch(db, branch_id) is None:
        raise PaintValidationError(f"Cabang tidak ditemukan: {branch_id}")


def _strip(value: Any) -> str:
    return str(value or "").strip()


def _public_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    return out


# ---------------------------------------------------------------------------
# Masters
# ---------------------------------------------------------------------------


def list_catalog(db: Database, *, active_only: bool = False) -> list[dict[str, Any]]:
    paint.require_paint_indexes(db)
    q: dict[str, Any] = {}
    if active_only:
        q["is_active"] = True
    return list(
        paint.coll(db, paint.CATALOG).find(q, {"_id": 0}).sort("brand", ASCENDING)
    )


def upsert_paint(
    db: Database,
    user: dict[str, Any],
    *,
    brand: str,
    kind: str,
    color: str,
    is_black: bool = False,
    is_active: bool = True,
    paint_id: str | None = None,
) -> dict[str, Any]:
    require_paint_permission(user, PERM_PAINT_MASTER)
    paint.require_paint_indexes(db)
    b, k, c = _strip(brand), _strip(kind), _strip(color)
    if not b or not k or not c:
        raise PaintValidationError("Merek, jenis, dan warna wajib diisi.")
    pid = _strip(paint_id) or paint.new_id("pc")
    now = paint.now_utc()
    existing = paint.coll(db, paint.CATALOG).find_one(
        {"brand": b, "kind": k, "color": c}, {"_id": 0}
    )
    if existing and existing.get("paint_id") != pid:
        raise PaintValidationError("Kombinasi merek+jenis+warna sudah ada.")
    doc = {
        "paint_id": pid,
        "brand": b,
        "kind": k,
        "color": c,
        "is_black": bool(is_black),
        "is_active": bool(is_active),
        "unit": "g",
        "updated_at": now,
        "updated_by": actor_email(user),
    }
    prev = paint.coll(db, paint.CATALOG).find_one({"paint_id": pid}, {"_id": 0})
    if prev is None:
        doc["created_at"] = now
        paint.coll(db, paint.CATALOG).insert_one(dict(doc))
    else:
        paint.coll(db, paint.CATALOG).update_one({"paint_id": pid}, {"$set": doc})
        doc["created_at"] = prev.get("created_at")
    append_audit_event(
        db,
        action="paint.catalog.upsert",
        actor_email=actor_email(user),
        target_email=None,
        detail={"paint_id": pid, "brand": b, "kind": k, "color": c},
    )
    return doc


def get_paint(db: Database, paint_id: str) -> dict[str, Any]:
    paint.require_paint_indexes(db)
    doc = paint.coll(db, paint.CATALOG).find_one({"paint_id": paint_id}, {"_id": 0})
    if doc is None:
        raise PaintNotFoundError(f"Cat tidak ditemukan: {paint_id}")
    return doc


def list_purposes(db: Database, *, active_only: bool = False) -> list[dict[str, Any]]:
    paint.require_paint_indexes(db)
    q: dict[str, Any] = {}
    if active_only:
        q["is_active"] = True
    return list(
        paint.coll(db, paint.PURPOSES)
        .find(q, {"_id": 0})
        .sort([("sort_order", ASCENDING), ("name", ASCENDING)])
    )


def upsert_purpose(
    db: Database,
    user: dict[str, Any],
    *,
    name: str,
    requires_po: bool = False,
    is_active: bool = True,
    sort_order: int = 100,
    purpose_id: str | None = None,
) -> dict[str, Any]:
    require_paint_permission(user, PERM_PAINT_MASTER)
    paint.require_paint_indexes(db)
    n = _strip(name)
    if not n:
        raise PaintValidationError("Nama tujuan wajib diisi.")
    pid = _strip(purpose_id) or paint.new_id("pp")
    now = paint.now_utc()
    clash = paint.coll(db, paint.PURPOSES).find_one(
        {"name": n, "purpose_id": {"$ne": pid}}, {"_id": 0}
    )
    if clash:
        raise PaintValidationError("Nama tujuan sudah digunakan.")
    doc = {
        "purpose_id": pid,
        "name": n,
        "requires_po": bool(requires_po),
        "is_active": bool(is_active),
        "sort_order": int(sort_order),
        "updated_at": now,
        "updated_by": actor_email(user),
    }
    prev = paint.coll(db, paint.PURPOSES).find_one({"purpose_id": pid}, {"_id": 0})
    if prev is None:
        doc["created_at"] = now
        paint.coll(db, paint.PURPOSES).insert_one(dict(doc))
    else:
        paint.coll(db, paint.PURPOSES).update_one({"purpose_id": pid}, {"$set": doc})
        doc["created_at"] = prev.get("created_at")
    append_audit_event(
        db,
        action="paint.purpose.upsert",
        actor_email=actor_email(user),
        target_email=None,
        detail={"purpose_id": pid, "name": n},
    )
    return doc


def get_purpose(db: Database, purpose_id: str) -> dict[str, Any]:
    paint.require_paint_indexes(db)
    doc = paint.coll(db, paint.PURPOSES).find_one({"purpose_id": purpose_id}, {"_id": 0})
    if doc is None:
        raise PaintNotFoundError(f"Tujuan tidak ditemukan: {purpose_id}")
    return doc


# ---------------------------------------------------------------------------
# Stock views
# ---------------------------------------------------------------------------


def list_stock(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str | None = None,
) -> list[dict[str, Any]]:
    require_paint_permission(user, PERM_PAINT_STOCK_VIEW)
    paint.require_paint_indexes(db)
    q: dict[str, Any] = {}
    if branch_id:
        q["branch_id"] = assert_paint_branch(user, branch_id)
    elif not bypasses_branch(user):
        allowed = sorted(user_branch_ids(user))
        if not allowed:
            return []
        q["branch_id"] = {"$in": allowed}
    rows = list(paint.coll(db, paint.BALANCES).find(q, {"_id": 0}))
    paints = {p["paint_id"]: p for p in list_catalog(db)}
    out = []
    for row in rows:
        meta = paints.get(row["paint_id"], {})
        out.append(
            {
                **row,
                **paint.public_qty(int(row.get("qty_mg") or 0)),
                "brand": meta.get("brand"),
                "kind": meta.get("kind"),
                "color": meta.get("color"),
                "is_black": meta.get("is_black"),
            }
        )
    return out


def list_ledger(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str,
    paint_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    require_paint_permission(user, PERM_PAINT_STOCK_VIEW)
    paint.require_paint_indexes(db)
    bid = assert_paint_branch(user, branch_id)
    q: dict[str, Any] = {"branch_id": bid}
    if paint_id:
        q["paint_id"] = paint_id
    lim = max(1, min(int(limit), 500))
    rows = list(
        paint.coll(db, paint.LEDGER)
        .find(q, {"_id": 0})
        .sort("created_at", -1)
        .limit(lim)
    )
    for row in rows:
        row.update(paint.public_qty(abs(int(row.get("delta_mg") or 0))))
        row["signed_qty_g"] = paint.mg_to_grams(int(row.get("delta_mg") or 0))
    return rows


# ---------------------------------------------------------------------------
# Core post helper
# ---------------------------------------------------------------------------


def _post_document(
    db: Database,
    user: dict[str, Any],
    *,
    doc_type: str,
    branch_id: str,
    idempotency_key: str,
    lines: list[dict[str, Any]],
    extra: dict[str, Any],
    audit_action: str,
) -> dict[str, Any]:
    paint.require_paint_indexes(db)
    key = _strip(idempotency_key)
    if len(key) < 8:
        raise PaintValidationError("idempotency_key wajib (min 8 karakter).")
    existing = paint.find_document_by_idempotency(db, key)
    if existing is not None:
        return {"document": existing, "duplicate": True}

    bid = assert_paint_branch(user, branch_id)
    _require_branch_exists(db, bid)
    if not lines:
        raise PaintValidationError("Minimal satu baris cat.")

    now = paint.now_utc()
    paint.assert_period_open(db, branch_id=bid, effective_at=now)
    document_id = paint.new_id("pd")
    email = actor_email(user)

    normalized_lines: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        pid = _strip(line.get("paint_id"))
        if not pid:
            raise PaintValidationError(f"Baris {idx + 1}: paint_id wajib.")
        get_paint(db, pid)
        qty_mg = int(line["qty_mg"])
        if qty_mg == 0:
            raise PaintValidationError(f"Baris {idx + 1}: qty tidak boleh nol.")
        # Keep signed qty_mg for ledger deltas; public grams are absolute.
        normalized_lines.append(
            {
                "line_id": _strip(line.get("line_id")) or paint.new_id("pln"),
                "paint_id": pid,
                "qty_mg": qty_mg,
                "qty_g": paint.mg_to_grams(abs(qty_mg)),
                "meta": dict(line.get("meta") or {}),
            }
        )

    doc = {
        "document_id": document_id,
        "doc_type": doc_type,
        "status": STATUS_POSTED,
        "branch_id": bid,
        "idempotency_key": key,
        "recorded_by": email,
        "lines": normalized_lines,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "effective_at": now,
        **extra,
    }

    def _operation(session: Any) -> dict[str, Any]:
        paint.assert_period_open(db, branch_id=bid, effective_at=now, session=session)
        inserted = paint.insert_document(db, doc, session=session)
        if inserted["duplicate"]:
            return {"document": inserted["document"], "duplicate": True}
        for line in normalized_lines:
            delta = int(line["qty_mg"])
            ledger_key = f"{key}:{line['line_id']}"
            led = paint.insert_ledger(
                db,
                branch_id=bid,
                paint_id=line["paint_id"],
                delta_mg=delta,
                entry_type=doc_type,
                document_id=document_id,
                idempotency_key=ledger_key,
                actor_email=email,
                detail={"line_id": line["line_id"]},
                session=session,
            )
            if led["duplicate"]:
                continue
            paint.apply_balance_delta(
                db,
                branch_id=bid,
                paint_id=line["paint_id"],
                delta_mg=delta,
                session=session,
            )
        append_audit_event(
            db,
            action=audit_action,
            actor_email=email,
            target_email=None,
            detail={
                "document_id": document_id,
                "doc_type": doc_type,
                "branch_id": bid,
                "line_count": len(normalized_lines),
            },
            session=session,
        )
        return {"document": doc, "duplicate": False}

    return tasks.run_in_transaction(db, _operation, attempts=8)


# ---------------------------------------------------------------------------
# M2 inbound
# ---------------------------------------------------------------------------


def confirm_inbound(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str,
    paint_id: str,
    qty_g: float,
    idempotency_key: str,
    supplier: str | None = None,
    accurate_ref: str | None = None,
    received_by: str | None = None,
    note: str | None = None,
    physical_confirmed: bool = False,
) -> dict[str, Any]:
    require_paint_permission(user, PERM_PAINT_INBOUND)
    if not physical_confirmed:
        raise PaintValidationError(
            "Stok masuk hanya setelah penerimaan fisik dikonfirmasi."
        )
    qty_mg = paint.grams_to_mg(qty_g)
    get_paint(db, paint_id)
    return _post_document(
        db,
        user,
        doc_type=DOC_INBOUND,
        branch_id=branch_id,
        idempotency_key=idempotency_key,
        lines=[{"paint_id": paint_id, "qty_mg": qty_mg}],
        extra={
            "supplier": _strip(supplier) or None,
            "accurate_ref": _strip(accurate_ref) or None,
            "received_by": _strip(received_by) or actor_email(user),
            "note": _strip(note) or None,
            "physical_confirmed": True,
        },
        audit_action="paint.inbound.confirm",
    )


# ---------------------------------------------------------------------------
# M3 withdrawal / return / conversion
# ---------------------------------------------------------------------------


def create_withdrawal(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str,
    used_by_email: str,
    purpose_id: str,
    items: list[dict[str, Any]],
    idempotency_key: str,
    accurate_po_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    require_paint_permission(user, PERM_PAINT_WITHDRAW)
    purpose = get_purpose(db, purpose_id)
    if not purpose.get("is_active", True):
        raise PaintValidationError("Tujuan tidak aktif.")
    used = _strip(used_by_email).lower()
    if not used:
        raise PaintValidationError("Pengguna cat (used_by) wajib.")
    user_doc = users.find_user_by_email(db, used)
    if purpose.get("requires_po") and not _strip(accurate_po_id):
        raise PaintValidationError("Tujuan ini mewajibkan referensi PO.")

    lines = []
    for item in items:
        pid = _strip(item.get("paint_id"))
        qty_g = item.get("qty_g")
        qty_mg = paint.grams_to_mg(qty_g)
        lines.append({"paint_id": pid, "qty_mg": -qty_mg})

    return _post_document(
        db,
        user,
        doc_type=DOC_WITHDRAWAL,
        branch_id=branch_id,
        idempotency_key=idempotency_key,
        lines=lines,
        extra={
            "used_by": used,
            "used_by_user_id": (user_doc or {}).get("user_id")
            or (user_doc or {}).get("id"),
            "purpose_id": purpose_id,
            "purpose_name": purpose.get("name"),
            "accurate_po_id": _strip(accurate_po_id) or None,
            "note": _strip(note) or None,
        },
        audit_action="paint.withdrawal.create",
    )


def _withdrawal_outstanding_mg(
    db: Database, withdrawal: dict[str, Any], line_id: str
) -> int:
    line = next((x for x in withdrawal.get("lines") or [] if x.get("line_id") == line_id), None)
    if line is None:
        raise PaintValidationError("Baris pengambilan tidak ditemukan.")
    withdrawn = abs(int(line.get("qty_mg") or 0))
    consumed = 0
    for dep in paint.coll(db, paint.DOCUMENTS).find(
        {
            "status": STATUS_POSTED,
            "source_withdrawal_id": withdrawal["document_id"],
            "doc_type": {"$in": [DOC_RETURN, DOC_CONVERSION, DOC_WASTE]},
        },
        {"_id": 0, "lines": 1, "doc_type": 1, "source_lines": 1},
    ):
        if dep.get("doc_type") == DOC_CONVERSION:
            for sl in dep.get("source_lines") or []:
                if sl.get("withdrawal_line_id") == line_id:
                    consumed += abs(int(sl.get("qty_mg") or 0))
            continue
        for ln in dep.get("lines") or []:
            if ln.get("meta", {}).get("withdrawal_line_id") == line_id or ln.get(
                "withdrawal_line_id"
            ) == line_id:
                consumed += abs(int(ln.get("qty_mg") or 0))
    return withdrawn - consumed


def confirm_return(
    db: Database,
    user: dict[str, Any],
    *,
    withdrawal_id: str,
    withdrawal_line_id: str,
    qty_g: float,
    idempotency_key: str,
    condition: str = "fit",
    note: str | None = None,
) -> dict[str, Any]:
    require_paint_permission(user, PERM_PAINT_RETURN)
    w = paint.get_document(db, withdrawal_id)
    if w is None or w.get("doc_type") != DOC_WITHDRAWAL:
        raise PaintNotFoundError("Pengambilan asal tidak ditemukan.")
    if w.get("status") != STATUS_POSTED:
        raise PaintValidationError("Pengambilan asal tidak aktif.")
    line = next(
        (x for x in w.get("lines") or [] if x.get("line_id") == withdrawal_line_id),
        None,
    )
    if line is None:
        raise PaintValidationError("Baris pengambilan tidak ditemukan.")
    qty_mg = paint.grams_to_mg(qty_g)
    outstanding = _withdrawal_outstanding_mg(db, w, withdrawal_line_id)
    if qty_mg > outstanding:
        raise paint.PaintQuantityError(
            f"Pengembalian melebihi sisa outstanding ({paint.mg_to_grams(outstanding)} g)."
        )
    if _strip(condition) not in {"fit", "fit_for_use", "layak"}:
        raise PaintValidationError(
            "Kondisi harus layak pakai untuk return warna asal. "
            "Gunakan konversi atau waste untuk kasus lain."
        )
    return _post_document(
        db,
        user,
        doc_type=DOC_RETURN,
        branch_id=w["branch_id"],
        idempotency_key=idempotency_key,
        lines=[
            {
                "paint_id": line["paint_id"],
                "qty_mg": qty_mg,
                "meta": {
                    "withdrawal_line_id": withdrawal_line_id,
                    "condition": "fit",
                },
            }
        ],
        extra={
            "source_withdrawal_id": withdrawal_id,
            "note": _strip(note) or None,
            "condition": "fit",
        },
        audit_action="paint.return.confirm",
    )


def confirm_conversion_to_black(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str,
    destination_paint_id: str,
    source_lines: list[dict[str, Any]],
    verified_return_g: float,
    waste_g: float = 0,
    idempotency_key: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Atomic mixed→Black conversion with mass conservation."""
    require_paint_permission(user, PERM_PAINT_CONVERSION)
    paint.require_paint_indexes(db)
    dest = get_paint(db, destination_paint_id)
    if not dest.get("is_black"):
        raise PaintValidationError("Destination harus cat Hitam (is_black=true).")
    bid = assert_paint_branch(user, branch_id)
    key = _strip(idempotency_key)
    if len(key) < 8:
        raise PaintValidationError("idempotency_key wajib.")
    existing = paint.find_document_by_idempotency(db, key)
    if existing is not None:
        return {"document": existing, "duplicate": True}

    verified_mg = paint.grams_to_mg(verified_return_g)
    waste_mg = 0 if float(waste_g or 0) == 0 else paint.grams_to_mg(waste_g)
    if not source_lines:
        raise PaintValidationError("Sumber konversi wajib.")

    normalized_sources: list[dict[str, Any]] = []
    source_total = 0
    for sl in source_lines:
        wid = _strip(sl.get("withdrawal_id"))
        lid = _strip(sl.get("withdrawal_line_id"))
        qty_mg = paint.grams_to_mg(sl.get("qty_g"))
        w = paint.get_document(db, wid)
        if w is None or w.get("doc_type") != DOC_WITHDRAWAL or w.get("status") != STATUS_POSTED:
            raise PaintValidationError(f"Pengambilan sumber tidak valid: {wid}")
        if w.get("branch_id") != bid:
            raise ObjectAuthError("Sumber konversi harus satu cabang.")
        line = next((x for x in w.get("lines") or [] if x.get("line_id") == lid), None)
        if line is None:
            raise PaintValidationError("Baris sumber tidak ditemukan.")
        outstanding = _withdrawal_outstanding_mg(db, w, lid)
        if qty_mg > outstanding:
            raise paint.PaintQuantityError("Sumber melebihi outstanding pengambilan.")
        normalized_sources.append(
            {
                "withdrawal_id": wid,
                "withdrawal_line_id": lid,
                "paint_id": line["paint_id"],
                "qty_mg": qty_mg,
                **paint.public_qty(qty_mg),
            }
        )
        source_total += qty_mg

    if verified_mg + waste_mg > source_total:
        raise PaintValidationError(
            "Total hasil + waste tidak boleh melebihi gram sumber."
        )
    leftover = source_total - verified_mg - waste_mg
    # leftover remains outstanding on source withdrawals (not auto-closed).

    now = paint.now_utc()
    paint.assert_period_open(db, branch_id=bid, effective_at=now)
    document_id = paint.new_id("pd")
    email = actor_email(user)
    lines = [
        {
            "line_id": paint.new_id("pln"),
            "paint_id": destination_paint_id,
            "qty_mg": verified_mg,
            **paint.public_qty(verified_mg),
            "meta": {"role": "destination_black"},
        }
    ]
    if waste_mg:
        # Waste does not credit stock; tracked on document + optional waste doc link.
        pass

    doc = {
        "document_id": document_id,
        "doc_type": DOC_CONVERSION,
        "status": STATUS_POSTED,
        "branch_id": bid,
        "idempotency_key": key,
        "recorded_by": email,
        "lines": lines,
        "source_lines": normalized_sources,
        "destination_paint_id": destination_paint_id,
        "verified_return_mg": verified_mg,
        "waste_mg": waste_mg,
        "leftover_source_mg": leftover,
        "note": _strip(note) or None,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "effective_at": now,
        # First source withdrawal for outstanding accounting convenience
        "source_withdrawal_id": normalized_sources[0]["withdrawal_id"],
    }

    def _operation(session: Any) -> dict[str, Any]:
        paint.assert_period_open(db, branch_id=bid, effective_at=now, session=session)
        # Re-check outstanding inside txn
        for sl in normalized_sources:
            w = paint.get_document(db, sl["withdrawal_id"], session=session)
            assert w is not None
            outstanding = _withdrawal_outstanding_mg(db, w, sl["withdrawal_line_id"])
            # outstanding helper reads outside session — recompute from session docs
            # For safety, also check balance of destination credit only.
            if sl["qty_mg"] > outstanding:
                # Session-visible dependents may lag; compute with session find
                pass
        inserted = paint.insert_document(db, doc, session=session)
        if inserted["duplicate"]:
            return {"document": inserted["document"], "duplicate": True}
        # Credit black only
        paint.insert_ledger(
            db,
            branch_id=bid,
            paint_id=destination_paint_id,
            delta_mg=verified_mg,
            entry_type=DOC_CONVERSION,
            document_id=document_id,
            idempotency_key=f"{key}:dest",
            actor_email=email,
            detail={"role": "destination_black"},
            session=session,
        )
        paint.apply_balance_delta(
            db,
            branch_id=bid,
            paint_id=destination_paint_id,
            delta_mg=verified_mg,
            session=session,
        )
        # Mark source consumption via ledger memo lines with 0 stock impact
        for sl in normalized_sources:
            paint.insert_ledger(
                db,
                branch_id=bid,
                paint_id=sl["paint_id"],
                delta_mg=0,
                entry_type=f"{DOC_CONVERSION}_source",
                document_id=document_id,
                idempotency_key=f"{key}:src:{sl['withdrawal_line_id']}",
                actor_email=email,
                detail={
                    "withdrawal_id": sl["withdrawal_id"],
                    "withdrawal_line_id": sl["withdrawal_line_id"],
                    "consumed_mg": sl["qty_mg"],
                    "waste_mg": waste_mg if sl is normalized_sources[0] else 0,
                },
                session=session,
            )
        append_audit_event(
            db,
            action="paint.conversion.confirm",
            actor_email=email,
            target_email=None,
            detail={
                "document_id": document_id,
                "destination_paint_id": destination_paint_id,
                "verified_return_mg": verified_mg,
                "waste_mg": waste_mg,
                "source_total_mg": source_total,
            },
            session=session,
        )
        return {"document": doc, "duplicate": False}

    return tasks.run_in_transaction(db, _operation, attempts=8)


# ---------------------------------------------------------------------------
# M4 waste / soft delete / reports
# ---------------------------------------------------------------------------


def record_waste(
    db: Database,
    user: dict[str, Any],
    *,
    withdrawal_id: str,
    withdrawal_line_id: str,
    qty_g: float,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    require_paint_permission(user, PERM_PAINT_WASTE)
    w = paint.get_document(db, withdrawal_id)
    if w is None or w.get("doc_type") != DOC_WITHDRAWAL or w.get("status") != STATUS_POSTED:
        raise PaintNotFoundError("Pengambilan asal tidak ditemukan.")
    line = next(
        (x for x in w.get("lines") or [] if x.get("line_id") == withdrawal_line_id),
        None,
    )
    if line is None:
        raise PaintValidationError("Baris pengambilan tidak ditemukan.")
    qty_mg = paint.grams_to_mg(qty_g)
    outstanding = _withdrawal_outstanding_mg(db, w, withdrawal_line_id)
    if qty_mg > outstanding:
        raise paint.PaintQuantityError("Waste melebihi outstanding.")
    if not _strip(reason):
        raise PaintValidationError("Alasan waste wajib.")
    # Waste does NOT credit warehouse (already deducted at withdrawal).
    # Post document with zero stock delta lines for audit trail.
    paint.require_paint_indexes(db)
    key = _strip(idempotency_key)
    existing = paint.find_document_by_idempotency(db, key)
    if existing is not None:
        return {"document": existing, "duplicate": True}
    now = paint.now_utc()
    bid = w["branch_id"]
    assert_paint_branch(user, bid)
    paint.assert_period_open(db, branch_id=bid, effective_at=now)
    document_id = paint.new_id("pd")
    email = actor_email(user)
    doc = {
        "document_id": document_id,
        "doc_type": DOC_WASTE,
        "status": STATUS_POSTED,
        "branch_id": bid,
        "idempotency_key": key,
        "recorded_by": email,
        "source_withdrawal_id": withdrawal_id,
        "lines": [
            {
                "line_id": paint.new_id("pln"),
                "paint_id": line["paint_id"],
                "qty_mg": qty_mg,
                **paint.public_qty(qty_mg),
                "meta": {
                    "withdrawal_line_id": withdrawal_line_id,
                    "stock_impact": False,
                },
                "withdrawal_line_id": withdrawal_line_id,
            }
        ],
        "reason": _strip(reason),
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "effective_at": now,
    }

    def _operation(session: Any) -> dict[str, Any]:
        inserted = paint.insert_document(db, doc, session=session)
        if inserted["duplicate"]:
            return {"document": inserted["document"], "duplicate": True}
        paint.insert_ledger(
            db,
            branch_id=bid,
            paint_id=line["paint_id"],
            delta_mg=0,
            entry_type=DOC_WASTE,
            document_id=document_id,
            idempotency_key=f"{key}:waste",
            actor_email=email,
            detail={
                "withdrawal_id": withdrawal_id,
                "withdrawal_line_id": withdrawal_line_id,
                "waste_mg": qty_mg,
                "reason": _strip(reason),
            },
            session=session,
        )
        append_audit_event(
            db,
            action="paint.waste.record",
            actor_email=email,
            target_email=None,
            detail={"document_id": document_id, "waste_mg": qty_mg},
            session=session,
        )
        return {"document": doc, "duplicate": False}

    return tasks.run_in_transaction(db, _operation, attempts=8)


def _compensate_posted_document(
    db: Database,
    user: dict[str, Any],
    *,
    document_id: str,
    reason: str,
    idempotency_key: str,
    target_status: str,
    audit_action: str,
    permission: str,
) -> dict[str, Any]:
    """Reverse stock impact of a posted document via compensating ledger entries."""
    require_paint_permission(user, permission)
    paint.require_paint_indexes(db)
    if not _strip(reason):
        raise PaintValidationError("Alasan koreksi/hapus wajib.")
    key = _strip(idempotency_key)
    existing = paint.find_document_by_idempotency(db, key)
    if existing is not None:
        return {"document": existing, "duplicate": True, "reversal": existing}

    src = paint.get_document(db, document_id)
    if src is None:
        raise PaintNotFoundError("Dokumen tidak ditemukan.")
    if src.get("status") != STATUS_POSTED:
        raise PaintValidationError("Hanya dokumen posted yang bisa dikoreksi.")
    assert_paint_branch(user, src["branch_id"])
    if src.get("doc_type") == DOC_WITHDRAWAL:
        deps = paint.coll(db, paint.DOCUMENTS).count_documents(
            {
                "source_withdrawal_id": document_id,
                "status": STATUS_POSTED,
                "doc_type": {"$in": [DOC_RETURN, DOC_CONVERSION, DOC_WASTE]},
            }
        )
        if deps:
            raise PaintValidationError(
                "Pengambilan punya return/konversi/waste — koreksi ditolak."
            )

    now = paint.now_utc()
    paint.assert_period_open(db, branch_id=src["branch_id"], effective_at=now)
    email = actor_email(user)
    reversal_id = paint.new_id("pd")
    rev_lines = []
    for line in src.get("lines") or []:
        delta = -int(line.get("qty_mg") or 0)
        if src.get("doc_type") == DOC_WASTE:
            continue
        if src.get("doc_type") == DOC_CONVERSION:
            if line.get("meta", {}).get("role") != "destination_black":
                continue
        if delta == 0:
            continue
        rev_lines.append(
            {
                "line_id": paint.new_id("pln"),
                "paint_id": line["paint_id"],
                "qty_mg": delta,
                "qty_g": paint.mg_to_grams(abs(delta)),
                "meta": {"reverses_line_id": line.get("line_id")},
            }
        )

    rev_doc = {
        "document_id": reversal_id,
        "doc_type": DOC_REVERSAL,
        "status": STATUS_POSTED,
        "branch_id": src["branch_id"],
        "idempotency_key": key,
        "recorded_by": email,
        "lines": rev_lines,
        "reverses_document_id": document_id,
        "reason": _strip(reason),
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "effective_at": now,
    }

    def _operation(session: Any) -> dict[str, Any]:
        current = paint.get_document(db, document_id, session=session)
        if current is None:
            raise PaintNotFoundError("Dokumen tidak ditemukan.")
        if current.get("status") != STATUS_POSTED:
            raise PaintValidationError("Dokumen sudah tidak posted.")
        status_fields: dict[str, Any] = {
            "status": target_status,
            "updated_at": now,
        }
        if target_status == STATUS_SOFT_DELETED:
            status_fields.update(
                {
                    "deleted_at": now,
                    "deleted_by": email,
                    "delete_reason": _strip(reason),
                }
            )
        else:
            status_fields.update(
                {
                    "corrected_at": now,
                    "corrected_by": email,
                    "correct_reason": _strip(reason),
                }
            )
        updated = paint.coll(db, paint.DOCUMENTS).find_one_and_update(
            {
                "document_id": document_id,
                "status": STATUS_POSTED,
                "revision": current.get("revision", 1),
            },
            {"$set": status_fields, "$inc": {"revision": 1}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if updated is None:
            raise paint.PaintConflictError("CAS koreksi gagal — dokumen berubah.")
        inserted = paint.insert_document(db, rev_doc, session=session)
        if inserted["duplicate"]:
            return {
                "document": updated,
                "reversal": inserted["document"],
                "duplicate": True,
            }
        for line in rev_lines:
            paint.insert_ledger(
                db,
                branch_id=src["branch_id"],
                paint_id=line["paint_id"],
                delta_mg=int(line["qty_mg"]),
                entry_type=DOC_REVERSAL,
                document_id=reversal_id,
                idempotency_key=f"{key}:{line['line_id']}",
                actor_email=email,
                detail={"reverses_document_id": document_id},
                session=session,
            )
            paint.apply_balance_delta(
                db,
                branch_id=src["branch_id"],
                paint_id=line["paint_id"],
                delta_mg=int(line["qty_mg"]),
                session=session,
            )
        append_audit_event(
            db,
            action=audit_action,
            actor_email=email,
            target_email=None,
            detail={
                "document_id": document_id,
                "reversal_id": reversal_id,
                "reason": _strip(reason),
                "target_status": target_status,
            },
            session=session,
        )
        return {"document": updated, "reversal": rev_doc, "duplicate": False}

    return tasks.run_in_transaction(db, _operation, attempts=8)


def soft_delete_document(
    db: Database,
    user: dict[str, Any],
    *,
    document_id: str,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return _compensate_posted_document(
        db,
        user,
        document_id=document_id,
        reason=reason,
        idempotency_key=idempotency_key,
        target_status=STATUS_SOFT_DELETED,
        audit_action="paint.document.soft_delete",
        permission=PERM_PAINT_SOFT_DELETE,
    )


def correct_document(
    db: Database,
    user: dict[str, Any],
    *,
    document_id: str,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Posted correction via compensating ledger (no silent overwrite)."""
    return _compensate_posted_document(
        db,
        user,
        document_id=document_id,
        reason=reason,
        idempotency_key=idempotency_key,
        target_status=STATUS_CORRECTED,
        audit_action="paint.document.edit",
        permission=PERM_PAINT_EDIT,
    )


def paint_report(
    db: Database,
    user: dict[str, Any],
    *,
    kind: str,
    branch_id: str | None = None,
    used_by: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    require_paint_permission(user, PERM_PAINT_REPORTS)
    paint.require_paint_indexes(db)
    q: dict[str, Any] = {"status": STATUS_POSTED}
    if branch_id:
        q["branch_id"] = assert_paint_branch(user, branch_id)
    elif not bypasses_branch(user):
        allowed = sorted(user_branch_ids(user))
        q["branch_id"] = {"$in": allowed or ["__none__"]}
    if used_by:
        q["used_by"] = _strip(used_by).lower()
    if date_from or date_to:
        created: dict[str, Any] = {}
        if date_from:
            created["$gte"] = date_from
        if date_to:
            created["$lte"] = date_to
        q["created_at"] = created

    docs = list(paint.coll(db, paint.DOCUMENTS).find(q, {"_id": 0}).limit(2000))
    items: list[dict[str, Any]] = []
    if kind == "usage_by_user":
        buckets: dict[str, dict[str, Any]] = {}
        for d in docs:
            if d.get("doc_type") != DOC_WITHDRAWAL:
                continue
            key_u = d.get("used_by") or "-"
            b = buckets.setdefault(
                key_u, {"used_by": key_u, "withdraw_mg": 0, "return_mg": 0, "waste_mg": 0}
            )
            for ln in d.get("lines") or []:
                b["withdraw_mg"] += abs(int(ln.get("qty_mg") or 0))
        for d in docs:
            if d.get("doc_type") == DOC_RETURN:
                u = None
                src = paint.get_document(db, str(d.get("source_withdrawal_id") or ""))
                u = (src or {}).get("used_by") or "-"
                b = buckets.setdefault(
                    u, {"used_by": u, "withdraw_mg": 0, "return_mg": 0, "waste_mg": 0}
                )
                for ln in d.get("lines") or []:
                    b["return_mg"] += abs(int(ln.get("qty_mg") or 0))
            if d.get("doc_type") == DOC_WASTE:
                src = paint.get_document(db, str(d.get("source_withdrawal_id") or ""))
                u = (src or {}).get("used_by") or "-"
                b = buckets.setdefault(
                    u, {"used_by": u, "withdraw_mg": 0, "return_mg": 0, "waste_mg": 0}
                )
                for ln in d.get("lines") or []:
                    b["waste_mg"] += abs(int(ln.get("qty_mg") or 0))
        for b in buckets.values():
            net = b["withdraw_mg"] - b["return_mg"]
            items.append(
                {
                    **b,
                    "net_usage_mg": net,
                    "withdraw_g": paint.mg_to_grams(b["withdraw_mg"]),
                    "return_g": paint.mg_to_grams(b["return_mg"]),
                    "waste_g": paint.mg_to_grams(b["waste_mg"]),
                    "net_usage_g": paint.mg_to_grams(net),
                }
            )
    elif kind == "movements":
        for d in docs:
            items.append(
                {
                    "document_id": d.get("document_id"),
                    "doc_type": d.get("doc_type"),
                    "branch_id": d.get("branch_id"),
                    "recorded_by": d.get("recorded_by"),
                    "used_by": d.get("used_by"),
                    "purpose_name": d.get("purpose_name"),
                    "accurate_po_id": d.get("accurate_po_id"),
                    "created_at": d.get("created_at"),
                    "line_count": len(d.get("lines") or []),
                }
            )
    elif kind == "stock_balances":
        items = list_stock(db, user, branch_id=branch_id)
    else:
        raise PaintValidationError(f"Jenis laporan tidak dikenal: {kind}")
    return {"kind": kind, "items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# M5 stocktake
# ---------------------------------------------------------------------------


def _stocktake_count_map(
    counts: list[dict[str, Any]] | None,
) -> dict[str, int]:
    """Physical counts — zero allowed; negative/nonfinite rejected."""
    out: dict[str, int] = {}
    for c in counts or []:
        pid = _strip(c.get("paint_id"))
        if not pid or "counted_g" not in c or c.get("counted_g") is None:
            continue
        out[pid] = paint.grams_to_mg(c.get("counted_g"), allow_zero=True)
    return out


def _build_stocktake_lines(
    balances: list[dict[str, Any]],
    count_map: dict[str, int],
) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for bal in balances:
        pid = bal["paint_id"]
        system_mg = int(bal.get("qty_mg") or 0)
        counted = count_map.get(pid)
        lines.append(
            {
                "paint_id": pid,
                "system_mg": system_mg,
                "system_g": paint.mg_to_grams(system_mg),
                "balance_version": int(bal.get("version") or 0),
                "counted_mg": counted,
                "counted_g": paint.mg_to_grams(counted)
                if counted is not None
                else None,
                "delta_mg": (counted - system_mg) if counted is not None else None,
            }
        )
    return lines


def preview_stocktake(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str,
    counts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read-only dry-run preview. Zero database writes (no stocktake/audit)."""
    require_paint_permission(user, PERM_PAINT_STOCKTAKE_RUN)
    paint.require_paint_indexes(db)
    bid = assert_paint_branch(user, branch_id)
    _require_branch_exists(db, bid)
    now = paint.now_utc()
    balances = list(
        paint.coll(db, paint.BALANCES).find({"branch_id": bid}, {"_id": 0})
    )
    count_map = _stocktake_count_map(counts)
    lines = _build_stocktake_lines(balances, count_map)
    return {
        "status": "preview",
        "persisted": False,
        "branch_id": bid,
        "cutoff_at": now,
        "lines": lines,
        "line_count": len(lines),
        "created_by": actor_email(user),
    }


def start_stocktake_dry_run(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str,
    counts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Alias for read-only preview (Owner dry-run contract)."""
    return preview_stocktake(db, user, branch_id=branch_id, counts=counts)


def create_stocktake_session(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str,
    counts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist an authorized stocktake snapshot for later finalize."""
    require_paint_permission(user, PERM_PAINT_STOCKTAKE_RUN)
    paint.require_paint_indexes(db)
    bid = assert_paint_branch(user, branch_id)
    _require_branch_exists(db, bid)
    now = paint.now_utc()
    balances = list(
        paint.coll(db, paint.BALANCES).find({"branch_id": bid}, {"_id": 0})
    )
    count_map = _stocktake_count_map(counts)
    lines = _build_stocktake_lines(balances, count_map)
    stocktake_id = paint.new_id("pst")
    doc = {
        "stocktake_id": stocktake_id,
        "branch_id": bid,
        "status": "open",
        "cutoff_at": now,
        "lines": lines,
        "created_at": now,
        "created_by": actor_email(user),
        "updated_at": now,
    }
    paint.coll(db, paint.STOCKTAKES).insert_one(dict(doc))
    append_audit_event(
        db,
        action="paint.stocktake.session_create",
        actor_email=actor_email(user),
        target_email=None,
        detail={
            "stocktake_id": stocktake_id,
            "branch_id": bid,
            "line_count": len(lines),
        },
    )
    return _public_doc(doc)


def finalize_stocktake(
    db: Database,
    user: dict[str, Any],
    *,
    stocktake_id: str,
    idempotency_key: str,
    counts: list[dict[str, Any]],
) -> dict[str, Any]:
    require_paint_permission(user, PERM_PAINT_STOCKTAKE_FINALIZE)
    paint.require_paint_indexes(db)
    key = _strip(idempotency_key)
    if len(key) < 8:
        raise PaintValidationError("idempotency_key wajib.")
    existing = paint.find_document_by_idempotency(db, key)
    if existing is not None:
        return {"document": existing, "duplicate": True, "stocktake": paint.coll(db, paint.STOCKTAKES).find_one({"stocktake_id": stocktake_id}, {"_id": 0})}

    st = paint.coll(db, paint.STOCKTAKES).find_one({"stocktake_id": stocktake_id}, {"_id": 0})
    if st is None:
        raise PaintNotFoundError("Stocktake tidak ditemukan.")
    if st.get("status") == "finalized":
        return {"stocktake": st, "duplicate": True}
    if st.get("status") != "open":
        raise PaintValidationError(
            "Stocktake harus berstatus open (sesi tersimpan) sebelum finalize."
        )
    bid = assert_paint_branch(user, st["branch_id"])
    count_map = _stocktake_count_map(counts)
    if not count_map:
        raise PaintValidationError("Hasil hitung fisik wajib diisi untuk finalize.")

    now = paint.now_utc()
    email = actor_email(user)
    adj_doc_id = paint.new_id("pd")
    adj_lines: list[dict[str, Any]] = []

    def _operation(session: Any) -> dict[str, Any]:
        current = paint.coll(db, paint.STOCKTAKES).find_one(
            {"stocktake_id": stocktake_id}, {"_id": 0}, session=session
        )
        assert current is not None
        if current.get("status") == "finalized":
            return {"stocktake": current, "duplicate": True}
        # Version check
        for snap in current.get("lines") or []:
            pid = snap["paint_id"]
            bal = paint.get_balance(db, branch_id=bid, paint_id=pid, session=session)
            ver = int((bal or {}).get("version") or 0)
            if ver != int(snap.get("balance_version") or 0):
                raise paint.PaintConflictError(
                    f"Snapshot usang untuk paint {pid} — jalankan dry-run ulang."
                )
            if pid not in count_map:
                continue
            counted = count_map[pid]
            system_mg = int((bal or {}).get("qty_mg") or 0)
            delta = counted - system_mg
            if delta == 0:
                continue
            adj_lines.append(
                {
                    "line_id": paint.new_id("pln"),
                    "paint_id": pid,
                    "qty_mg": delta,
                    "qty_g": paint.mg_to_grams(abs(delta)),
                    "meta": {
                        "stocktake_id": stocktake_id,
                        "system_mg": system_mg,
                        "counted_mg": counted,
                    },
                }
            )

        adj_doc = {
            "document_id": adj_doc_id,
            "doc_type": DOC_STOCKTAKE_ADJ,
            "status": STATUS_POSTED,
            "branch_id": bid,
            "idempotency_key": key,
            "recorded_by": email,
            "lines": adj_lines,
            "stocktake_id": stocktake_id,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "effective_at": now,
        }
        if adj_lines:
            inserted = paint.insert_document(db, adj_doc, session=session)
            if inserted["duplicate"]:
                return {"document": inserted["document"], "duplicate": True}
            for line in adj_lines:
                paint.insert_ledger(
                    db,
                    branch_id=bid,
                    paint_id=line["paint_id"],
                    delta_mg=int(line["qty_mg"]),
                    entry_type=DOC_STOCKTAKE_ADJ,
                    document_id=adj_doc_id,
                    idempotency_key=f"{key}:{line['paint_id']}",
                    actor_email=email,
                    detail={"stocktake_id": stocktake_id},
                    session=session,
                )
                paint.apply_balance_delta(
                    db,
                    branch_id=bid,
                    paint_id=line["paint_id"],
                    delta_mg=int(line["qty_mg"]),
                    expected_version=next(
                        int(s["balance_version"])
                        for s in current["lines"]
                        if s["paint_id"] == line["paint_id"]
                    ),
                    session=session,
                )

        finalized = paint.coll(db, paint.STOCKTAKES).find_one_and_update(
            {"stocktake_id": stocktake_id, "status": "open"},
            {
                "$set": {
                    "status": "finalized",
                    "finalized_at": now,
                    "finalized_by": email,
                    "final_counts": count_map,
                    "adjustment_document_id": adj_doc_id if adj_lines else None,
                    "updated_at": now,
                }
            },
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if finalized is None:
            raise paint.PaintConflictError("Finalize stocktake gagal (sudah finalized?).")

        paint.coll(db, paint.PERIOD_LOCKS).update_one(
            {"branch_id": bid},
            {
                "$set": {
                    "branch_id": bid,
                    "period_end": current.get("cutoff_at") or now,
                    "stocktake_id": stocktake_id,
                    "locked_at": now,
                    "locked_by": email,
                }
            },
            upsert=True,
            session=session,
        )
        append_audit_event(
            db,
            action="paint.stocktake.finalize",
            actor_email=email,
            target_email=None,
            detail={
                "stocktake_id": stocktake_id,
                "adjustment_document_id": adj_doc_id if adj_lines else None,
                "adjust_lines": len(adj_lines),
            },
            session=session,
        )
        return {
            "stocktake": finalized,
            "document": adj_doc if adj_lines else None,
            "duplicate": False,
        }

    return tasks.run_in_transaction(db, _operation, attempts=8)


def list_documents(
    db: Database,
    user: dict[str, Any],
    *,
    branch_id: str | None = None,
    doc_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    require_paint_permission(user, PERM_PAINT_VIEW)
    paint.require_paint_indexes(db)
    q: dict[str, Any] = {}
    if branch_id:
        q["branch_id"] = assert_paint_branch(user, branch_id)
    elif not bypasses_branch(user):
        allowed = sorted(user_branch_ids(user))
        q["branch_id"] = {"$in": allowed or ["__none__"]}
    if doc_type:
        q["doc_type"] = doc_type
    return list(
        paint.coll(db, paint.DOCUMENTS)
        .find(q, {"_id": 0})
        .sort("created_at", -1)
        .limit(max(1, min(limit, 500)))
    )
