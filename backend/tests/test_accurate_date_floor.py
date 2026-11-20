"""Date floor ACCURATE_SYNC_START_DATE — shared discovery + checkpoint scope."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.po_repository import ensure_po_indexes, get_by_accurate_id
from app.services.accurate_auto_sync import locks, po_job
from app.services.accurate_auto_sync.checkpoint import (
    CHECKPOINT_VERSION,
    load_job_checkpoint,
    save_job_checkpoint,
)
from app.services.accurate_auto_sync.date_floor import (
    accurate_trans_date_filter_params,
    classify_trans_date,
    clear_date_floor_cache,
    date_floor_scope_id,
    detail_scope_outcome,
    format_accurate_date,
    parse_accurate_trans_date,
    parse_iso_date_floor,
)
from app.services.accurate_auto_sync.discovery import list_page_params
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED


@pytest.fixture(autouse=True)
def _floor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    clear_settings_cache()
    clear_date_floor_cache()
    yield
    clear_settings_cache()
    clear_date_floor_cache()


def _payload(so_id: int, *, trans_date: str, ship_zone: str = "K") -> dict[str, Any]:
    p = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    p["id"] = so_id
    p["number"] = f"PO/{so_id}"
    p["transDate"] = trans_date
    p["charField3"] = ship_zone
    return p


class FloorClient:
    def __init__(self) -> None:
        self.ids: list[int] = []
        self.details: dict[str, dict[str, Any]] = {}
        self.last_update: dict[int, str] = {}
        self._clock = 1_700_000_000
        self.list_calls: list[dict[str, Any]] = []
        self.fail_ids: set[str] = set()

    def _tick(self) -> str:
        self._clock += 1
        return str(self._clock)

    def add(self, so_id: int, *, trans_date: str, ship_zone: str = "K") -> None:
        self.ids.append(so_id)
        self.ids = sorted(set(self.ids))
        self.details[str(so_id)] = _payload(so_id, trans_date=trans_date, ship_zone=ship_zone)
        self.last_update[so_id] = self._tick()

    def mutate(self, so_id: int, *, ship_zone: str) -> None:
        cur = self.details[str(so_id)]
        td = str(cur.get("transDate") or "01/11/2026")
        self.details[str(so_id)] = _payload(so_id, trans_date=td, ship_zone=ship_zone)
        self.last_update[so_id] = self._tick()

    def fetch_sales_order_list(self, *, params: dict[str, Any] | None = None) -> list[dict]:
        params = params or {}
        self.list_calls.append(dict(params))
        page = int(params.get("sp.page") or 1)
        size = int(params.get("sp.pageSize") or 20)
        sort = str(params.get("sp.sort") or "")
        if "lastUpdate" in sort:
            ordered = sorted(self.ids, key=lambda i: self.last_update[i], reverse=True)
        else:
            ordered = sorted(self.ids)
        if params.get("filter.transDate.op") == "BETWEEN":
            lo = parse_accurate_trans_date(params.get("filter.transDate.val[0]"))
            hi = parse_accurate_trans_date(params.get("filter.transDate.val[1]"))
            assert lo and hi
            ordered = [
                i
                for i in ordered
                if (
                    td := parse_accurate_trans_date(
                        self.details[str(i)].get("transDate")
                    )
                )
                is not None
                and lo <= td <= hi
            ]
        start = (page - 1) * size
        chunk = ordered[start : start + size]
        return [
            {
                "id": i,
                "lastUpdate": self.last_update[i],
                "transDate": self.details[str(i)].get("transDate"),
            }
            for i in chunk
        ]

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        key = str(accurate_id)
        if key in self.fail_ids:
            raise RuntimeError(f"forced_fail_{key}")
        return copy.deepcopy(self.details[key])


@pytest.fixture()
def pdb(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    ensure_po_indexes(db)
    return db


def test_parse_and_classify_jakarta_boundaries() -> None:
    floor = parse_iso_date_floor("2026-11-01")
    assert floor == date(2026, 11, 1)
    assert classify_trans_date("31/10/2026", start=floor)[0] == "out_of_scope"
    assert classify_trans_date("01/11/2026", start=floor)[0] == "in_scope"
    assert classify_trans_date("20/11/2026", start=floor)[0] == "in_scope"
    assert classify_trans_date("01/12/2026", start=floor)[0] == "in_scope"
    assert classify_trans_date("01/01/2027", start=floor)[0] == "in_scope"
    assert classify_trans_date("", start=floor)[0] == "invalid"
    assert format_accurate_date(floor) == "01/11/2026"
    fp = accurate_trans_date_filter_params(floor)
    assert fp["filter.transDate.op"] == "BETWEEN"
    assert fp["filter.transDate.val[0]"] == "01/11/2026"
    assert fp["filter.transDate.val[1]"] == "31/12/2099"


def test_list_page_params_include_between_filter() -> None:
    params = list_page_params(1, 20, sort="id|asc")
    assert params["filter.transDate.op"] == "BETWEEN"
    assert params["filter.transDate.val[0]"] == "01/11/2026"
    assert "transDate" in params["fields"]


def test_detail_scope_outcome_excludes_before_floor() -> None:
    out = detail_scope_outcome(
        {"id": 1, "transDate": "31/10/2026"}, accurate_id="1"
    )
    assert out is not None
    assert out["outcome"] == "excluded"
    ok = detail_scope_outcome(
        {"id": 2, "transDate": "01/11/2026"}, accurate_id="2"
    )
    assert ok is None


def test_checkpoint_scope_mismatch_resets_pagination(pdb: Database) -> None:
    token = locks.acquire_job_lock(pdb, job="po")
    # Simulasikan checkpoint historis (tanpa date_floor / next_page tinggi).
    pdb["accurate_auto_sync_status"].update_one(
        {"job": "po"},
        {
            "$set": {
                "checkpoint": {
                    "version": 2,
                    "next_page": 6,
                    "last_completed_page": 5,
                    "backfill_complete": False,
                }
            }
        },
        upsert=True,
    )
    loaded = load_job_checkpoint(pdb, "po")
    assert loaded["next_page"] == 1
    assert loaded["date_floor"] == "2026-11-01"
    assert loaded["date_floor_scope"] == date_floor_scope_id(date(2026, 11, 1))
    assert loaded["version"] == CHECKPOINT_VERSION
    # Simpan scope baru lalu ubah floor → mismatch lagi.
    save_job_checkpoint(
        pdb, "po", loaded, owner_token=token, date_floor=date(2026, 11, 1)
    )
    clear_date_floor_cache()
    other = load_job_checkpoint(pdb, "po", date_floor=date(2026, 12, 1))
    assert other["next_page"] == 1
    assert other["date_floor"] == "2026-12-01"
    locks.release_job_lock(pdb, job="po", owner_token=token)


def test_po_date_floor_excludes_oct_includes_nov(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FloorClient()
    client.add(1, trans_date="31/10/2026")
    client.add(2, trans_date="01/11/2026")
    client.add(3, trans_date="20/11/2026")
    client.add(4, trans_date="15/12/2026")
    # 250+ in-scope to exercise pagination/cap
    for i in range(100, 360):
        client.add(i, trans_date="05/11/2026")

    result = po_job.run_po_auto_sync(
        pdb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=120
    )
    assert result["ok"] is True
    assert result["enabled"] is True
    # Filter list: Oct tidak masuk listed/fetched sebagai created
    assert get_by_accurate_id(pdb, "1") is None
    assert get_by_accurate_id(pdb, "2") is not None
    assert get_by_accurate_id(pdb, "3") is not None
    assert get_by_accurate_id(pdb, "4") is not None
    # Cap 100 rows/cycle
    assert result["counters"]["listed"] <= 100
    assert result["counters"]["created"] <= 100
    assert result["counters"]["failed"] == 0
    # List calls carry BETWEEN
    assert any(
        c.get("filter.transDate.op") == "BETWEEN" for c in client.list_calls
    )
    cp = load_job_checkpoint(pdb, "po")
    assert cp["date_floor"] == "2026-11-01"
    assert cp["next_page"] >= 2 or cp["backfill_complete"]


def test_out_of_scope_existing_doc_not_deleted(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Dokumen historis sudah di store — sync tidak menghapus.
    old = normalize_sales_order(_payload(99, trans_date="31/10/2026"))
    from app.repositories.po_repository import upsert_sales_order

    upsert_sales_order(pdb, old)
    assert get_by_accurate_id(pdb, "99") is not None
    client = FloorClient()
    client.add(2, trans_date="01/11/2026")
    po_job.run_po_auto_sync(
        pdb, client=client, page_size=20, max_pages=2, max_rows=40, timeout_seconds=60
    )
    assert get_by_accurate_id(pdb, "99") is not None
    assert get_by_accurate_id(pdb, "2") is not None


def test_accepted_po_safe_under_date_floor(pdb: Database) -> None:
    client = FloorClient()
    client.add(50, trans_date="01/11/2026", ship_zone="A")
    po_job.run_po_auto_sync(
        pdb, client=client, page_size=20, max_pages=1, max_rows=20, timeout_seconds=30
    )
    pdb["accurate_sales_orders"].update_one(
        {"accurate_id": "50"},
        {
            "$set": {
                "operational.acceptance": {
                    "status": ACCEPTANCE_ACCEPTED,
                    "accepted_by": "test@example.com",
                }
            }
        },
    )
    client.mutate(50, ship_zone="A")  # metadata-only path via same zone
    # material change
    client.mutate(50, ship_zone="B")
    # actually mutate qty for material — ship_zone may be safe metadata
    # Ensure accepted stays accepted after unchanged-ish cycle with same material
    before = get_by_accurate_id(pdb, "50")
    assert before is not None
    assert before["operational"]["acceptance"]["status"] == ACCEPTANCE_ACCEPTED
    po_job.run_po_auto_sync(
        pdb, client=client, page_size=20, max_pages=1, max_rows=20, timeout_seconds=30
    )
    after = get_by_accurate_id(pdb, "50")
    assert after["operational"]["acceptance"]["status"] == ACCEPTANCE_ACCEPTED


def test_mid_page_failure_retry_resume(pdb: Database) -> None:
    client = FloorClient()
    for i in range(1, 40):
        client.add(i, trans_date="02/11/2026")
    client.fail_ids.add("5")
    r1 = po_job.run_po_auto_sync(
        pdb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=60
    )
    assert r1["counters"]["failed"] >= 1
    cp = load_job_checkpoint(pdb, "po")
    stuck_page = cp["next_page"]
    client.fail_ids.clear()
    r2 = po_job.run_po_auto_sync(
        pdb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=60
    )
    assert r2["counters"]["failed"] == 0
    assert get_by_accurate_id(pdb, "5") is not None
    assert stuck_page >= 1


def test_kill_switch_off_zero_effect(pdb: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    clear_settings_cache()
    client = FloorClient()
    client.add(1, trans_date="01/11/2026")
    r = po_job.run_po_auto_sync(pdb, client=client)
    assert r["enabled"] is False
    assert r["accurate_requests"] == 0
    assert get_by_accurate_id(pdb, "1") is None


def test_incremental_finds_in_scope_mutation(pdb: Database) -> None:
    client = FloorClient()
    for i in range(1, 25):
        client.add(i, trans_date="03/11/2026")
    # Complete small backfill
    while True:
        r = po_job.run_po_auto_sync(
            pdb,
            client=client,
            page_size=20,
            max_pages=5,
            max_rows=100,
            timeout_seconds=60,
        )
        cp = load_job_checkpoint(pdb, "po")
        if cp.get("backfill_complete"):
            break
        if r["counters"]["failed"]:
            break
    assert load_job_checkpoint(pdb, "po")["backfill_complete"] is True
    client.mutate(3, ship_zone="HOT")
    po_job.run_po_auto_sync(
        pdb, client=client, page_size=20, max_pages=3, max_rows=60, timeout_seconds=60
    )
    doc = get_by_accurate_id(pdb, "3")
    assert doc is not None
    assert doc["header"]["ship_zone"] == "HOT"
