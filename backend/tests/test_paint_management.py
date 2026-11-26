"""Paint management M2–M6 integration tests — isolated Mongo only."""

from __future__ import annotations

import uuid

import pytest
from pymongo.database import Database

from app.repositories.org_master_repository import ensure_org_master_indexes, upsert_branch
from app.repositories.paint_repository import PaintQuantityError, grams_to_mg, mg_to_grams
from app.services import paint_accurate_adapter as accurate_paint
from app.services import paint_service as svc
from app.services.object_auth import ObjectAuthError

KP = {
    "email": "kp@eternal.local",
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "branch_ids": ["50"],
    "effective_module_permissions": [],
}

OTHER_BRANCH_USER = {
    "email": "other@eternal.local",
    "email_normalized": "other@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "branch_ids": ["150"],
    "effective_module_permissions": [
        "paint.module.view",
        "paint.stock.view",
        "paint.inbound.confirm",
    ],
}

OPS = {
    "email": "ops@eternal.local",
    "email_normalized": "ops@eternal.local",
    "role": "admin",
    "operational_role": None,
    "branch_ids": [],
    "effective_module_permissions": ["operations.full", "access.all_branches"],
}


def _key(prefix: str = "k") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


@pytest.fixture()
def paint_env(db: Database) -> Database:
    ensure_org_master_indexes(db)
    upsert_branch(db, code="50", name="Yogyakarta", is_active=True)
    upsert_branch(db, code="150", name="Semarang", is_active=True)
    return db


def _seed_masters(db: Database) -> dict[str, str]:
    white = svc.upsert_paint(
        db, KP, brand="ABC", kind="Plastisol", color="Putih", is_black=False
    )
    black = svc.upsert_paint(
        db, KP, brand="ABC", kind="Plastisol", color="Hitam", is_black=True
    )
    red = svc.upsert_paint(
        db, KP, brand="ABC", kind="Plastisol", color="Merah", is_black=False
    )
    purpose = svc.upsert_purpose(db, KP, name="Produksi Sablon", requires_po=False)
    purpose_po = svc.upsert_purpose(db, KP, name="Sample PO", requires_po=True)
    return {
        "white": white["paint_id"],
        "black": black["paint_id"],
        "red": red["paint_id"],
        "purpose": purpose["purpose_id"],
        "purpose_po": purpose_po["purpose_id"],
    }


def test_grams_mg_roundtrip() -> None:
    assert grams_to_mg(1.5) == 1500
    assert mg_to_grams(1500) == 1.5
    with pytest.raises(PaintQuantityError):
        grams_to_mg(0)
    assert grams_to_mg(0, allow_zero=True) == 0
    with pytest.raises(PaintQuantityError):
        grams_to_mg(-1, allow_zero=True)
    with pytest.raises(PaintQuantityError):
        grams_to_mg(1.2345)
    with pytest.raises(PaintQuantityError):
        grams_to_mg(float("nan"), allow_zero=True)


def test_m2_master_inbound_stock_branch_isolation(paint_env: Database) -> None:
    db = paint_env
    ids = _seed_masters(db)
    # duplicate identity rejected
    with pytest.raises(svc.PaintValidationError):
        svc.upsert_paint(db, KP, brand="ABC", kind="Plastisol", color="Putih")

    with pytest.raises(svc.PaintValidationError):
        svc.confirm_inbound(
            db,
            KP,
            branch_id="50",
            paint_id=ids["white"],
            qty_g=100,
            idempotency_key=_key(),
            physical_confirmed=False,
        )

    k1 = _key("in")
    r1 = svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["white"],
        qty_g=100,
        idempotency_key=k1,
        physical_confirmed=True,
        supplier="Toko Cat",
    )
    assert r1["duplicate"] is False
    r1b = svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["white"],
        qty_g=100,
        idempotency_key=k1,
        physical_confirmed=True,
    )
    assert r1b["duplicate"] is True

    stock = svc.list_stock(db, KP, branch_id="50")
    white_row = next(x for x in stock if x["paint_id"] == ids["white"])
    assert white_row["qty_mg"] == 100_000

    # other branch user cannot see/use branch 50 for mutations
    with pytest.raises(ObjectAuthError):
        svc.confirm_inbound(
            db,
            OTHER_BRANCH_USER,
            branch_id="50",
            paint_id=ids["white"],
            qty_g=10,
            idempotency_key=_key(),
            physical_confirmed=True,
        )


def test_m3_withdrawal_return_conversion_waste(paint_env: Database) -> None:
    db = paint_env
    ids = _seed_masters(db)
    for pid, g in ((ids["white"], 200), (ids["red"], 200), (ids["black"], 50)):
        svc.confirm_inbound(
            db,
            KP,
            branch_id="50",
            paint_id=pid,
            qty_g=g,
            idempotency_key=_key("in"),
            physical_confirmed=True,
        )

    with pytest.raises(svc.PaintValidationError):
        svc.create_withdrawal(
            db,
            KP,
            branch_id="50",
            used_by_email="op@eternal.local",
            purpose_id=ids["purpose_po"],
            items=[{"paint_id": ids["white"], "qty_g": 10}],
            idempotency_key=_key(),
        )

    w = svc.create_withdrawal(
        db,
        KP,
        branch_id="50",
        used_by_email="op@eternal.local",
        purpose_id=ids["purpose"],
        items=[
            {"paint_id": ids["white"], "qty_g": 40},
            {"paint_id": ids["red"], "qty_g": 30},
        ],
        idempotency_key=_key("w"),
        note="serah fisik",
    )
    assert w["duplicate"] is False
    doc = w["document"]
    assert doc["recorded_by"] == "kp@eternal.local"
    assert doc["used_by"] == "op@eternal.local"
    white_line = next(x for x in doc["lines"] if x["paint_id"] == ids["white"])
    red_line = next(x for x in doc["lines"] if x["paint_id"] == ids["red"])

    # over-withdraw rejected
    with pytest.raises(PaintQuantityError):
        svc.create_withdrawal(
            db,
            KP,
            branch_id="50",
            used_by_email="op@eternal.local",
            purpose_id=ids["purpose"],
            items=[{"paint_id": ids["white"], "qty_g": 9999}],
            idempotency_key=_key(),
        )

    # partial return
    ret = svc.confirm_return(
        db,
        KP,
        withdrawal_id=doc["document_id"],
        withdrawal_line_id=white_line["line_id"],
        qty_g=10,
        idempotency_key=_key("ret"),
    )
    assert ret["duplicate"] is False

    # over-return
    with pytest.raises(PaintQuantityError):
        svc.confirm_return(
            db,
            KP,
            withdrawal_id=doc["document_id"],
            withdrawal_line_id=white_line["line_id"],
            qty_g=1000,
            idempotency_key=_key(),
        )

    # conversion mixed → black
    conv = svc.confirm_conversion_to_black(
        db,
        KP,
        branch_id="50",
        destination_paint_id=ids["black"],
        source_lines=[
            {
                "withdrawal_id": doc["document_id"],
                "withdrawal_line_id": white_line["line_id"],
                "qty_g": 20,
            },
            {
                "withdrawal_id": doc["document_id"],
                "withdrawal_line_id": red_line["line_id"],
                "qty_g": 15,
            },
        ],
        verified_return_g=30,
        waste_g=5,
        idempotency_key=_key("cv"),
    )
    assert conv["duplicate"] is False
    assert conv["document"]["verified_return_mg"] == 30_000
    assert conv["document"]["waste_mg"] == 5_000

    # destination cannot exceed sources
    with pytest.raises(svc.PaintValidationError):
        svc.confirm_conversion_to_black(
            db,
            KP,
            branch_id="50",
            destination_paint_id=ids["black"],
            source_lines=[
                {
                    "withdrawal_id": doc["document_id"],
                    "withdrawal_line_id": red_line["line_id"],
                    "qty_g": 5,
                }
            ],
            verified_return_g=10,
            waste_g=0,
            idempotency_key=_key(),
        )

    # waste remaining white outstanding (40-10-20=10)
    waste = svc.record_waste(
        db,
        KP,
        withdrawal_id=doc["document_id"],
        withdrawal_line_id=white_line["line_id"],
        qty_g=5,
        reason="tumpah",
        idempotency_key=_key("ws"),
    )
    assert waste["duplicate"] is False

    # stock white: inbound 200 - withdraw 40 + return 10 = 170
    white_bal = next(x for x in svc.list_stock(db, KP, branch_id="50") if x["paint_id"] == ids["white"])
    assert white_bal["qty_mg"] == 170_000
    black_bal = next(x for x in svc.list_stock(db, KP, branch_id="50") if x["paint_id"] == ids["black"])
    # inbound 50 + conversion 30 = 80
    assert black_bal["qty_mg"] == 80_000


def test_m4_correct_document_compensating(paint_env: Database) -> None:
    db = paint_env
    ids = _seed_masters(db)
    inbound = svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["white"],
        qty_g=25,
        idempotency_key=_key(),
        physical_confirmed=True,
    )
    corrected = svc.correct_document(
        db,
        KP,
        document_id=inbound["document"]["document_id"],
        reason="salah qty",
        idempotency_key=_key("corr"),
    )
    assert corrected["document"]["status"] == "corrected"
    bal = next(
        (x for x in svc.list_stock(db, KP, branch_id="50") if x["paint_id"] == ids["white"]),
        None,
    )
    assert bal is None or bal["qty_mg"] == 0


def test_m4_soft_delete_and_reports(paint_env: Database) -> None:
    db = paint_env
    ids = _seed_masters(db)
    svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["white"],
        qty_g=50,
        idempotency_key=_key(),
        physical_confirmed=True,
    )
    w = svc.create_withdrawal(
        db,
        KP,
        branch_id="50",
        used_by_email="op@eternal.local",
        purpose_id=ids["purpose"],
        items=[{"paint_id": ids["white"], "qty_g": 10}],
        idempotency_key=_key(),
    )
    # soft delete blocked when dependent return exists
    line_id = w["document"]["lines"][0]["line_id"]
    svc.confirm_return(
        db,
        KP,
        withdrawal_id=w["document"]["document_id"],
        withdrawal_line_id=line_id,
        qty_g=2,
        idempotency_key=_key(),
    )
    with pytest.raises(svc.PaintValidationError):
        svc.soft_delete_document(
            db,
            KP,
            document_id=w["document"]["document_id"],
            reason="batal",
            idempotency_key=_key(),
        )

    # soft delete inbound without deps
    inbound = svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["red"],
        qty_g=20,
        idempotency_key=_key(),
        physical_confirmed=True,
    )
    deleted = svc.soft_delete_document(
        db,
        KP,
        document_id=inbound["document"]["document_id"],
        reason="salah input",
        idempotency_key=_key("sd"),
    )
    assert deleted["document"]["status"] == "soft_deleted"
    red_bal = next(
        (x for x in svc.list_stock(db, KP, branch_id="50") if x["paint_id"] == ids["red"]),
        None,
    )
    assert red_bal is None or red_bal["qty_mg"] == 0

    report = svc.paint_report(db, KP, kind="usage_by_user", branch_id="50")
    assert report["total"] >= 1
    assert any(i.get("used_by") == "op@eternal.local" for i in report["items"])


def _paint_collection_counts(db: Database) -> dict[str, int]:
    from app.repositories import paint_repository as paint

    return {
        paint.STOCKTAKES: db[paint.STOCKTAKES].count_documents({}),
        paint.PERIOD_LOCKS: db[paint.PERIOD_LOCKS].count_documents({}),
        paint.DOCUMENTS: db[paint.DOCUMENTS].count_documents({}),
        paint.LEDGER: db[paint.LEDGER].count_documents({}),
        paint.BALANCES: db[paint.BALANCES].count_documents({}),
        "audit_logs": db["audit_logs"].count_documents({}),
    }


def test_m5_stocktake_dry_run_and_finalize(paint_env: Database) -> None:
    db = paint_env
    ids = _seed_masters(db)
    svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["white"],
        qty_g=100,
        idempotency_key=_key(),
        physical_confirmed=True,
    )
    before = _paint_collection_counts(db)
    dry = svc.preview_stocktake(db, KP, branch_id="50")
    assert dry["status"] == "preview"
    assert dry["persisted"] is False
    assert "stocktake_id" not in dry
    after_preview = _paint_collection_counts(db)
    assert after_preview == before
    # repeated preview still zero writes
    svc.start_stocktake_dry_run(db, KP, branch_id="50")
    assert _paint_collection_counts(db) == before

    bal = next(x for x in svc.list_stock(db, KP, branch_id="50") if x["paint_id"] == ids["white"])
    assert bal["qty_mg"] == 100_000

    # explicit session persists snapshot
    session = svc.create_stocktake_session(db, KP, branch_id="50")
    assert session["status"] == "open"
    assert session["stocktake_id"]
    assert db["paint_stocktakes"].count_documents({"stocktake_id": session["stocktake_id"]}) == 1

    # concurrent inbound after snapshot → finalize rejects stale version
    svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["white"],
        qty_g=5,
        idempotency_key=_key(),
        physical_confirmed=True,
    )
    with pytest.raises(Exception):
        svc.finalize_stocktake(
            db,
            KP,
            stocktake_id=session["stocktake_id"],
            idempotency_key=_key(),
            counts=[{"paint_id": ids["white"], "counted_g": 100}],
        )

    session2 = svc.create_stocktake_session(db, KP, branch_id="50")
    fin = svc.finalize_stocktake(
        db,
        KP,
        stocktake_id=session2["stocktake_id"],
        idempotency_key=_key("fin"),
        counts=[{"paint_id": ids["white"], "counted_g": 90}],
    )
    assert fin["stocktake"]["status"] == "finalized"
    bal2 = next(x for x in svc.list_stock(db, KP, branch_id="50") if x["paint_id"] == ids["white"])
    assert bal2["qty_mg"] == 90_000

    # idempotent finalize
    fin2 = svc.finalize_stocktake(
        db,
        KP,
        stocktake_id=session2["stocktake_id"],
        idempotency_key=_key("fin2"),
        counts=[{"paint_id": ids["white"], "counted_g": 90}],
    )
    assert fin2.get("duplicate") is True

    # period lock: new inbound after lock (effective_at now > period_end) still OK
    svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["white"],
        qty_g=1,
        idempotency_key=_key(),
        physical_confirmed=True,
    )


def test_m5_stocktake_zero_physical_count(paint_env: Database) -> None:
    db = paint_env
    ids = _seed_masters(db)
    svc.confirm_inbound(
        db,
        KP,
        branch_id="50",
        paint_id=ids["white"],
        qty_g=1000,
        idempotency_key=_key(),
        physical_confirmed=True,
    )
    preview = svc.preview_stocktake(
        db,
        KP,
        branch_id="50",
        counts=[{"paint_id": ids["white"], "counted_g": 0}],
    )
    white_line = next(x for x in preview["lines"] if x["paint_id"] == ids["white"])
    assert white_line["counted_mg"] == 0
    assert white_line["delta_mg"] == -1_000_000

    with pytest.raises(PaintQuantityError):
        svc.preview_stocktake(
            db,
            KP,
            branch_id="50",
            counts=[{"paint_id": ids["white"], "counted_g": -1}],
        )

    session = svc.create_stocktake_session(db, KP, branch_id="50")
    fin = svc.finalize_stocktake(
        db,
        KP,
        stocktake_id=session["stocktake_id"],
        idempotency_key=_key("fin0"),
        counts=[{"paint_id": ids["white"], "counted_g": 0}],
    )
    assert fin["stocktake"]["status"] == "finalized"
    bal = next(x for x in svc.list_stock(db, KP, branch_id="50") if x["paint_id"] == ids["white"])
    assert bal["qty_mg"] == 0
    # no double ledger on idempotent finalize
    ledger_n = db["paint_ledger"].count_documents(
        {"entry_type": "stocktake_adjustment", "paint_id": ids["white"]}
    )
    fin2 = svc.finalize_stocktake(
        db,
        KP,
        stocktake_id=session["stocktake_id"],
        idempotency_key=_key("fin0b"),
        counts=[{"paint_id": ids["white"], "counted_g": 0}],
    )
    assert fin2.get("duplicate") is True
    assert (
        db["paint_ledger"].count_documents(
            {"entry_type": "stocktake_adjustment", "paint_id": ids["white"]}
        )
        == ledger_n
    )


def test_m6_accurate_dark_adapter(paint_env: Database) -> None:
    db = paint_env
    ids = _seed_masters(db)
    ready = accurate_paint.readiness()
    assert ready.status in {"READY_DARK", "NOT_READY"}
    assert ready.adapter_enabled is False

    rejected = accurate_paint.reject_unverified_goods_receipt(
        external_document_id="GR-1", physical_confirmed=True
    )
    assert rejected["accepted"] is False

    # mapping requires accurate.map permission — KP default excludes it
    with pytest.raises(svc.PaintPermissionError):
        accurate_paint.upsert_mapping(
            db, KP, accurate_item_code="ITEM-1", paint_id=ids["white"]
        )

    mapper = {
        **OPS,
        "effective_module_permissions": [
            "operations.full",
            "access.all_branches",
            "paint.accurate.map",
        ],
    }
    mapped = accurate_paint.upsert_mapping(
        db, mapper, accurate_item_code="ITEM-1", paint_id=ids["white"]
    )
    assert mapped["paint_id"] == ids["white"]
    assert len(accurate_paint.list_mappings(db)) == 1
