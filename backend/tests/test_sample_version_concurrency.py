"""P1-C: draft/version/confirm concurrency + idempotency."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from pymongo.database import Database

from app.domain.operational import TaskRef, build_task_key
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import sample_repository as samples
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import production_service as prod
from app.services import scheduling_service

PO_ID = "95106"
LINE_A = "106318"
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
}
OP = {
    "email_normalized": "op@eternal.local",
    "role": "user",
    "operational_role": "operator",
}
PNG = b"\x89PNG\r\n\x1a\nsample"


def _seed(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted

    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji"
        )
    master.seed_master_defaults(db)
    return get_by_accurate_id(db, PO_ID)


def _alloc_ready(db: Database, po: dict) -> str:
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op@eternal.local", "qty": 400}],
        idempotency_key=f"sv-{uuid.uuid4().hex}",
    )
    alloc = created["allocations"][0]["allocation_id"]
    ref_line = TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A)
    tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role="helper",
        ref=ref_line,
        qty_target=1000,
        label="Persiapan",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("persiapan-cup", "po_line", ref_line),
        qty=400,
        actor_email="helper@eternal.local",
        actor_role="helper",
        idempotency_key=f"prep-{uuid.uuid4().hex}",
    )
    ref = TaskRef(
        accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc
    )
    tasks.upsert_task(
        db,
        task_type="screen",
        scope="po_line_allocation",
        assigned_role="screener",
        ref=ref,
        qty_target=10,
        label="Screen",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("screen", "po_line_allocation", ref),
        qty=10,
        actor_email="sc@eternal.local",
        actor_role="screener",
        idempotency_key=f"sc-{uuid.uuid4().hex}",
    )
    return alloc


def test_draft_a_b_confirm_b_keeps_history(db: Database) -> None:
    po = _seed(db)
    alloc = _alloc_ready(db, po)
    a = prod.upload_sample_draft(
        db, OP, allocation_id=alloc, filename="a.png", content_type="image/png", content=PNG
    )
    b = prod.upload_sample_draft(
        db, OP, allocation_id=alloc, filename="b.png", content_type="image/png", content=PNG + b"b"
    )
    confirmed = prod.confirm_sample_submission(db, OP, allocation_id=alloc)
    assert confirmed["sample"]["version"] == b["sample"]["version"]
    assert confirmed["sample"]["status"] == samples.STATUS_PENDING
    history = samples.list_samples(db, alloc)
    assert {int(x["version"]) for x in history} >= {
        int(a["sample"]["version"]),
        int(b["sample"]["version"]),
    }
    assert sum(1 for x in history if x["status"] == samples.STATUS_PENDING) == 1


def test_concurrent_uploads_unique_versions(db: Database) -> None:
    po = _seed(db)
    alloc = _alloc_ready(db, po)

    def upload(tag: bytes) -> int:
        out = prod.upload_sample_draft(
            db,
            OP,
            allocation_id=alloc,
            filename=f"{tag.decode()}.png",
            content_type="image/png",
            content=PNG + tag,
        )
        return int(out["sample"]["version"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = [
            f.result()
            for f in as_completed([pool.submit(upload, b"A"), pool.submit(upload, b"B")])
        ]
    assert len(set(versions)) == 2
    assert len(samples.list_samples(db, alloc)) == 2


def test_stale_confirm_rejected_after_newer_draft(db: Database) -> None:
    po = _seed(db)
    alloc = _alloc_ready(db, po)
    first = prod.upload_sample_draft(
        db, OP, allocation_id=alloc, filename="a.png", content_type="image/png", content=PNG
    )
    prod.upload_sample_draft(
        db, OP, allocation_id=alloc, filename="b.png", content_type="image/png", content=PNG + b"x"
    )
    stale = samples.confirm_draft_sample(
        db,
        allocation_id=alloc,
        version=int(first["sample"]["version"]),
        actor_email="op@eternal.local",
    )
    assert stale is None
    ok = prod.confirm_sample_submission(db, OP, allocation_id=alloc)
    assert ok["sample"]["status"] == samples.STATUS_PENDING


def test_double_confirm_idempotent(db: Database) -> None:
    po = _seed(db)
    alloc = _alloc_ready(db, po)
    prod.upload_sample_draft(
        db, OP, allocation_id=alloc, filename="a.png", content_type="image/png", content=PNG
    )
    first = prod.confirm_sample_submission(db, OP, allocation_id=alloc)
    second = prod.confirm_sample_submission(db, OP, allocation_id=alloc)
    assert first.get("duplicate") is not True
    assert second.get("duplicate") is True
    pending = [
        s for s in samples.list_samples(db, alloc) if s["status"] == samples.STATUS_PENDING
    ]
    assert len(pending) == 1


def test_reject_then_new_draft_confirm(db: Database) -> None:
    po = _seed(db)
    alloc = _alloc_ready(db, po)
    prod.upload_sample_draft(
        db, OP, allocation_id=alloc, filename="b.png", content_type="image/png", content=PNG
    )
    submitted = prod.confirm_sample_submission(db, OP, allocation_id=alloc)["sample"]
    prod.decide_sample(
        db,
        HEAD,
        allocation_id=alloc,
        version=int(submitted["version"]),
        approved=False,
        reason="ulang",
    )
    c = prod.upload_sample_draft(
        db, OP, allocation_id=alloc, filename="c.png", content_type="image/png", content=PNG + b"c"
    )
    confirmed = prod.confirm_sample_submission(db, OP, allocation_id=alloc)
    assert confirmed["sample"]["version"] == c["sample"]["version"]
    assert confirmed["sample"]["status"] == samples.STATUS_PENDING


def test_approve_stale_version_rejected(db: Database) -> None:
    po = _seed(db)
    alloc = _alloc_ready(db, po)
    prod.upload_sample_draft(
        db, OP, allocation_id=alloc, filename="a.png", content_type="image/png", content=PNG
    )
    submitted = prod.confirm_sample_submission(db, OP, allocation_id=alloc)["sample"]
    prod.decide_sample(
        db,
        HEAD,
        allocation_id=alloc,
        version=int(submitted["version"]),
        approved=False,
        reason="stale-path",
    )
    with pytest.raises(LookupError):
        prod.decide_sample(
            db,
            HEAD,
            allocation_id=alloc,
            version=int(submitted["version"]),
            approved=True,
        )
