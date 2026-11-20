"""Regresi ingest PO — cabang kanonis + date floor + kill-switch."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.org_master_repository import (
    ensure_org_master_indexes,
    upsert_branch,
)
from app.repositories.po_repository import get_by_accurate_id, set_po_branch_id
from app.services.accurate_auto_sync import locks, po_job, status
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED
from app.services.po_sync import sync_sales_order_payload


class FakeAccurateClient:
    def __init__(
        self,
        *,
        list_rows: list[dict[str, Any]],
        details: dict[str, dict[str, Any]],
    ):
        self.list_rows = list_rows
        self.details = details
        self.list_calls = 0
        self.detail_calls = 0

    def fetch_sales_order_list(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.list_calls += 1
        params = params or {}
        page = int(params.get("sp.page") or 1)
        size = int(params.get("sp.pageSize") or 20)
        rows = sorted(
            [dict(r) for r in self.list_rows], key=lambda r: int(r.get("id") or 0)
        )
        start = (page - 1) * size
        return rows[start : start + size]

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.detail_calls += 1
        key = str(accurate_id)
        if key not in self.details:
            raise LookupError(key)
        return copy.deepcopy(self.details[key])


@pytest.fixture()
def ingest_db(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    ensure_org_master_indexes(db)
    upsert_branch(db, code="50", name="Yogyakarta", is_active=True)
    upsert_branch(db, code="150", name="Semarang", is_active=True)
    return db


def _detail(
    accurate_id: str,
    *,
    trans_date: str,
    branch_id: str | None,
    number: str | None = None,
) -> dict[str, Any]:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id) if accurate_id.isdigit() else accurate_id
    payload["transDate"] = trans_date
    payload["number"] = number or f"PO/{trans_date.replace('/', '/')}/X"
    if branch_id is None:
        payload.pop("branchId", None)
    else:
        payload["branchId"] = int(branch_id) if str(branch_id).isdigit() else branch_id
    return payload


def test_auto_sync_sets_branch_50_and_150(ingest_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    details = {
        "88001": _detail("88001", trans_date="01/11/2026", branch_id="50"),
        "88002": _detail("88002", trans_date="02/11/2026", branch_id="150"),
    }
    client = FakeAccurateClient(
        list_rows=[{"id": 88001}, {"id": 88002}],
        details=details,
    )
    result = po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=10
    )
    assert result["ok"] is True
    assert result["counters"]["created"] == 2
    assert get_by_accurate_id(ingest_db, "88001")["branch_id"] == "50"
    assert get_by_accurate_id(ingest_db, "88002")["branch_id"] == "150"


def test_missing_or_unknown_branch_fail_closed(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    details = {
        "88010": _detail("88010", trans_date="01/11/2026", branch_id=None),
        "88011": _detail("88011", trans_date="01/11/2026", branch_id="999"),
    }
    client = FakeAccurateClient(
        list_rows=[{"id": 88010}, {"id": 88011}],
        details=details,
    )
    result = po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=10
    )
    assert get_by_accurate_id(ingest_db, "88010") is None
    assert get_by_accurate_id(ingest_db, "88011") is None
    assert result["counters"]["created"] == 0
    assert result["counters"]["failed"] >= 1


def test_reingest_preserves_manual_branch(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    # Seed PO dengan cabang manual 150.
    token = locks.acquire_job_lock(ingest_db, job="po")
    payload = _detail("88020", trans_date="05/11/2026", branch_id="50")
    po = normalize_sales_order(payload)
    po_job._upsert_unaccepted(
        ingest_db,
        po,
        line_diff={
            "added_line_ids": [],
            "removed_line_ids": [],
            "changed_line_ids": [],
            "previous_line_count": 0,
            "current_line_count": 2,
        },
        owner_token=token,
        branch_id="150",
        set_branch_on_insert=True,
    )
    locks.release_job_lock(ingest_db, job="po", owner_token=token)
    assert get_by_accurate_id(ingest_db, "88020")["branch_id"] == "150"

    client = FakeAccurateClient(
        list_rows=[{"id": 88020}],
        details={"88020": _detail("88020", trans_date="05/11/2026", branch_id="50")},
    )
    po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=5
    )
    doc = get_by_accurate_id(ingest_db, "88020")
    assert doc["branch_id"] == "150"
    assert (doc.get("operational") or {}).get("branch_review", {}).get("status") == "conflict"


def test_accepted_po_keeps_operational(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    token = locks.acquire_job_lock(ingest_db, job="po")
    payload = _detail("88030", trans_date="05/11/2026", branch_id="50")
    po = normalize_sales_order(payload)
    po_job._upsert_unaccepted(
        ingest_db,
        po,
        line_diff={
            "added_line_ids": [],
            "removed_line_ids": [],
            "changed_line_ids": [],
            "previous_line_count": 0,
            "current_line_count": 2,
        },
        owner_token=token,
        branch_id="50",
        set_branch_on_insert=True,
    )
    locks.release_job_lock(ingest_db, job="po", owner_token=token)
    ingest_db["accurate_sales_orders"].update_one(
        {"accurate_id": "88030"},
        {
            "$set": {
                "operational": {
                    "acceptance": {
                        "status": ACCEPTANCE_ACCEPTED,
                        "accepted_by": "tester@example.com",
                    },
                    "inbox_status": "PO Diterima",
                    "custom_marker": "keep-me",
                }
            }
        },
    )
    client = FakeAccurateClient(
        list_rows=[{"id": 88030}],
        details={
            "88030": _detail(
                "88030",
                trans_date="05/11/2026",
                branch_id="50",
                number="PO/05/11/2026/KEEP",
            )
        },
    )
    po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=5
    )
    doc = get_by_accurate_id(ingest_db, "88030")
    assert doc["operational"]["custom_marker"] == "keep-me"
    assert doc["operational"]["acceptance"]["status"] == ACCEPTANCE_ACCEPTED
    assert doc["branch_id"] == "50"


def test_date_floor_excludes_before_allows_on_and_after(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    details = {
        "88040": _detail("88040", trans_date="31/10/2026", branch_id="50"),
        "88041": _detail("88041", trans_date="01/11/2026", branch_id="50"),
        "88042": _detail(
            "88042",
            trans_date="06/11/2026",
            branch_id="50",
            number="PO/28/10/2026/000029",
        ),
    }
    client = FakeAccurateClient(
        list_rows=[{"id": 88040}, {"id": 88041}, {"id": 88042}],
        details=details,
    )
    result = po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=10
    )
    assert get_by_accurate_id(ingest_db, "88040") is None
    assert get_by_accurate_id(ingest_db, "88041") is not None
    assert get_by_accurate_id(ingest_db, "88042") is not None
    assert result["counters"]["excluded"] >= 1
    assert result["counters"]["created"] == 2


def test_invalid_trans_date_zero_write(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    bad = _detail("88050", trans_date="01/11/2026", branch_id="50")
    bad["transDate"] = "bukan-tanggal"
    client = FakeAccurateClient(list_rows=[{"id": 88050}], details={"88050": bad})
    result = po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=5
    )
    assert get_by_accurate_id(ingest_db, "88050") is None
    assert result["counters"]["failed"] >= 1


def test_manual_sync_respects_date_floor_and_branch(ingest_db: Database) -> None:
    rejected = sync_sales_order_payload(
        ingest_db,
        _detail("88060", trans_date="31/10/2026", branch_id="50"),
        mongo_guard="test",
    )
    assert rejected.get("writes") == 0
    assert rejected.get("outcome") == "excluded"
    assert get_by_accurate_id(ingest_db, "88060") is None

    ok = sync_sales_order_payload(
        ingest_db,
        _detail("88061", trans_date="01/11/2026", branch_id="50"),
        mongo_guard="test",
    )
    assert ok.get("writes") == 1
    assert get_by_accurate_id(ingest_db, "88061")["branch_id"] == "50"


def test_kill_switch_off_zero_http_zero_write(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    client = FakeAccurateClient(
        list_rows=[{"id": 88070}],
        details={"88070": _detail("88070", trans_date="01/11/2026", branch_id="50")},
    )
    before = list(ingest_db["accurate_sales_orders"].find())
    result = po_job.run_po_auto_sync(ingest_db, client=client)
    assert result["enabled"] is False
    assert result.get("accurate_requests", 0) == 0
    assert result.get("writes", 0) == 0
    assert client.list_calls == 0
    assert client.detail_calls == 0
    assert list(ingest_db["accurate_sales_orders"].find()) == before


def test_existing_empty_branch_no_auto_fill_review(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PO existing tanpa branch_id → tidak auto-fill; review_required (bukan unchanged)."""
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    payload = _detail("88100", trans_date="05/11/2026", branch_id="50")
    token = locks.acquire_job_lock(ingest_db, job="po")
    po = normalize_sales_order(payload)
    po_job._upsert_unaccepted(
        ingest_db,
        po,
        line_diff={
            "added_line_ids": [],
            "removed_line_ids": [],
            "changed_line_ids": [],
            "previous_line_count": 0,
            "current_line_count": 2,
        },
        owner_token=token,
        branch_id=None,
        set_branch_on_insert=False,
    )
    ingest_db["accurate_sales_orders"].update_one(
        {"accurate_id": "88100"},
        {"$unset": {"branch_id": ""}},
    )
    locks.release_job_lock(ingest_db, job="po", owner_token=token)
    assert get_by_accurate_id(ingest_db, "88100").get("branch_id") in (None, "")

    client = FakeAccurateClient(
        list_rows=[{"id": 88100}],
        details={"88100": payload},
    )
    result = po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=5
    )
    doc = get_by_accurate_id(ingest_db, "88100")
    assert doc.get("branch_id") in (None, "")
    assert result["counters"]["review_required"] >= 1
    assert result["counters"].get("unchanged", 0) == 0
    review = (doc.get("operational") or {}).get("branch_review") or {}
    assert review.get("status") == "review_required"
    assert review.get("reason") == "existing_missing_branch_id"


def test_accepted_empty_branch_no_auto_fill(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepted + branch kosong → tidak auto-fill; workflow tetap; review."""
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    token = locks.acquire_job_lock(ingest_db, job="po")
    payload = _detail("88110", trans_date="05/11/2026", branch_id="150")
    po = normalize_sales_order(payload)
    po_job._upsert_unaccepted(
        ingest_db,
        po,
        line_diff={
            "added_line_ids": [],
            "removed_line_ids": [],
            "changed_line_ids": [],
            "previous_line_count": 0,
            "current_line_count": 2,
        },
        owner_token=token,
        branch_id=None,
        set_branch_on_insert=False,
    )
    ingest_db["accurate_sales_orders"].update_one(
        {"accurate_id": "88110"},
        {
            "$unset": {"branch_id": ""},
            "$set": {
                "operational": {
                    "acceptance": {
                        "status": ACCEPTANCE_ACCEPTED,
                        "accepted_by": "tester@example.com",
                    },
                    "inbox_status": "PO Diterima",
                    "custom_marker": "accepted-keep",
                }
            },
        },
    )
    locks.release_job_lock(ingest_db, job="po", owner_token=token)

    client = FakeAccurateClient(
        list_rows=[{"id": 88110}],
        details={"88110": payload},
    )
    result = po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=5
    )
    doc = get_by_accurate_id(ingest_db, "88110")
    assert doc.get("branch_id") in (None, "")
    assert doc["operational"]["custom_marker"] == "accepted-keep"
    assert doc["operational"]["acceptance"]["status"] == ACCEPTANCE_ACCEPTED
    review = (doc.get("operational") or {}).get("branch_review") or {}
    assert review.get("status") == "review_required"
    assert result["counters"]["review_required"] >= 1


def test_dependency_empty_branch_no_auto_fill(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task/SJ/alokasi + branch kosong → tidak auto-fill; turunan tidak diubah."""
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    token = locks.acquire_job_lock(ingest_db, job="po")
    payload = _detail("88120", trans_date="05/11/2026", branch_id="50")
    po = normalize_sales_order(payload)
    po_job._upsert_unaccepted(
        ingest_db,
        po,
        line_diff={
            "added_line_ids": [],
            "removed_line_ids": [],
            "changed_line_ids": [],
            "previous_line_count": 0,
            "current_line_count": 2,
        },
        owner_token=token,
        branch_id=None,
        set_branch_on_insert=False,
    )
    ingest_db["accurate_sales_orders"].update_one(
        {"accurate_id": "88120"}, {"$unset": {"branch_id": ""}}
    )
    locks.release_job_lock(ingest_db, job="po", owner_token=token)
    ingest_db["operational_tasks"].insert_one(
        {
            "accurate_po_id": "88120",
            "task_key": "88120-print",
            "task_type": "print",
            "status": "open",
            "branch_id": "150",
        }
    )
    ingest_db["production_allocations"].insert_one(
        {"accurate_po_id": "88120", "allocation_id": "A-88120", "branch_id": "150"}
    )
    ingest_db["accurate_delivery_notes"].insert_one(
        {"accurate_po_id": "88120", "accurate_sj_id": "SJ-88120", "branch_id": "150"}
    )
    client = FakeAccurateClient(
        list_rows=[{"id": 88120}],
        details={"88120": payload},
    )
    po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=5
    )
    doc = get_by_accurate_id(ingest_db, "88120")
    assert doc.get("branch_id") in (None, "")
    review = (doc.get("operational") or {}).get("branch_review") or {}
    assert review.get("status") == "review_required"
    assert ingest_db["operational_tasks"].find_one({"accurate_po_id": "88120"})[
        "branch_id"
    ] == "150"
    assert ingest_db["production_allocations"].find_one({"accurate_po_id": "88120"})[
        "branch_id"
    ] == "150"
    assert ingest_db["accurate_delivery_notes"].find_one({"accurate_po_id": "88120"})[
        "branch_id"
    ] == "150"


def test_manual_override_with_deps_not_overwritten(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cabang manual + dependency: konflik source tidak menimpa."""
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    token = locks.acquire_job_lock(ingest_db, job="po")
    payload = _detail("88121", trans_date="05/11/2026", branch_id="50")
    po = normalize_sales_order(payload)
    po_job._upsert_unaccepted(
        ingest_db,
        po,
        line_diff={
            "added_line_ids": [],
            "removed_line_ids": [],
            "changed_line_ids": [],
            "previous_line_count": 0,
            "current_line_count": 2,
        },
        owner_token=token,
        branch_id="150",
        set_branch_on_insert=True,
    )
    locks.release_job_lock(ingest_db, job="po", owner_token=token)
    ingest_db["operational_tasks"].insert_one(
        {"accurate_po_id": "88121", "task_type": "print", "status": "open", "branch_id": "150"}
    )
    client = FakeAccurateClient(
        list_rows=[{"id": 88121}],
        details={"88121": _detail("88121", trans_date="05/11/2026", branch_id="50")},
    )
    po_job.run_po_auto_sync(
        ingest_db, client=client, page_size=20, max_pages=1, max_rows=5
    )
    doc = get_by_accurate_id(ingest_db, "88121")
    assert doc["branch_id"] == "150"
    assert (doc.get("operational") or {}).get("branch_review", {}).get("status") == "conflict"


def test_manual_sync_one_date_floor_contract(ingest_db: Database) -> None:
    """P0: jalur manual sync_one — excluded/failed tanpa exception; Nov sukses."""
    from app.services.po_sync import sync_one_sales_order_from_accurate

    class _Client:
        def __init__(self, details: dict[str, dict[str, Any]]):
            self.details = details

        def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
            return copy.deepcopy(self.details[str(accurate_id)])

    oct_client = _Client(
        {"88200": _detail("88200", trans_date="31/10/2026", branch_id="50")}
    )
    excluded = sync_one_sales_order_from_accurate(
        ingest_db, 88200, client=oct_client, mongo_guard="test"
    )
    assert excluded["ok"] is False
    assert excluded["outcome"] == "excluded"
    assert excluded["writes"] == 0
    assert excluded["process_kinds"] == []
    assert get_by_accurate_id(ingest_db, "88200") is None

    bad = _detail("88201", trans_date="01/11/2026", branch_id="50")
    bad["transDate"] = "bukan-tanggal"
    failed = sync_one_sales_order_from_accurate(
        ingest_db, 88201, client=_Client({"88201": bad}), mongo_guard="test"
    )
    assert failed["ok"] is False
    assert failed["outcome"] == "failed"
    assert failed["writes"] == 0
    assert get_by_accurate_id(ingest_db, "88201") is None

    nov = sync_one_sales_order_from_accurate(
        ingest_db,
        88202,
        client=_Client(
            {"88202": _detail("88202", trans_date="01/11/2026", branch_id="50")}
        ),
        mongo_guard="test",
    )
    assert nov["ok"] is True
    assert nov["writes"] >= 1
    assert get_by_accurate_id(ingest_db, "88202")["branch_id"] == "50"

    mixed = sync_one_sales_order_from_accurate(
        ingest_db,
        88203,
        client=_Client(
            {
                "88203": _detail(
                    "88203",
                    trans_date="06/11/2026",
                    branch_id="150",
                    number="PO/28/10/2026/000099",
                )
            }
        ),
        mongo_guard="test",
    )
    assert mixed["ok"] is True
    assert get_by_accurate_id(ingest_db, "88203")["branch_id"] == "150"


def test_race_accept_and_task_no_auto_rollback(
    ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Race accept/task saat re-ingest: tidak auto-fill dan tidak rollback branch."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from itertools import count

    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    payload = _detail("88310", trans_date="05/11/2026", branch_id="50")
    token = locks.acquire_job_lock(ingest_db, job="po")
    po = normalize_sales_order(payload)
    po_job._upsert_unaccepted(
        ingest_db,
        po,
        line_diff={
            "added_line_ids": [],
            "removed_line_ids": [],
            "changed_line_ids": [],
            "previous_line_count": 0,
            "current_line_count": 2,
        },
        owner_token=token,
        branch_id=None,
        set_branch_on_insert=False,
    )
    ingest_db["accurate_sales_orders"].update_one(
        {"accurate_id": "88310"}, {"$unset": {"branch_id": ""}}
    )
    locks.release_job_lock(ingest_db, job="po", owner_token=token)

    seq = count(1)

    def _accept() -> None:
        ingest_db["accurate_sales_orders"].update_one(
            {"accurate_id": "88310"},
            {
                "$set": {
                    "operational.acceptance": {
                        "status": ACCEPTANCE_ACCEPTED,
                        "accepted_by": "race@example.com",
                    }
                }
            },
        )

    def _task() -> None:
        n = next(seq)
        ingest_db["operational_tasks"].insert_one(
            {
                "accurate_po_id": "88310",
                "task_key": f"88310-print-{n}",
                "branch_id": "150",
            }
        )

    client = FakeAccurateClient(
        list_rows=[{"id": 88310}],
        details={"88310": payload},
    )

    def _sync() -> None:
        po_job.run_po_auto_sync(
            ingest_db, client=client, page_size=20, max_pages=1, max_rows=5
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = []
        for i in range(12):
            if i % 3 == 0:
                futs.append(pool.submit(_accept))
            elif i % 3 == 1:
                futs.append(pool.submit(_task))
            else:
                futs.append(pool.submit(_sync))
        for f in as_completed(futs):
            f.result()

    doc = get_by_accurate_id(ingest_db, "88310")
    # Sync rutin tidak auto-fill → branch tetap kosong (tidak ada rollback karena tidak diisi).
    assert doc.get("branch_id") in (None, "")
    for t in ingest_db["operational_tasks"].find({"accurate_po_id": "88310"}):
        assert t.get("branch_id") == "150"


def test_dry_run_verification_levels_no_false_ready() -> None:
    import importlib.util
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "po_branch_repair_dry_run.py"
    spec = importlib.util.spec_from_file_location("po_branch_repair_dry_run", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    snap = {
        "tasks": 0,
        "alloc": 0,
        "sj": 0,
        "branch_id": None,
        "meta_branch": "50",
        "stored_po_date": "2026-11-05",
        "detail_verified": False,
    }
    assert mod._classify(snap) == "CANDIDATE_FROM_MONGO_SNAPSHOT"
    snap["detail_verified"] = True
    assert mod._classify(snap) == "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"
