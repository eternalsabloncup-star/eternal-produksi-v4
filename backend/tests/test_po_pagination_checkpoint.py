"""PO discovery checkpoint — backfill id|asc + incremental lastUpdate|desc."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.po_repository import (
    ensure_po_indexes,
    get_by_accurate_id,
    upsert_sales_order,
)
from app.services.accurate_auto_sync import locks, po_job
from app.services.accurate_auto_sync.po_checkpoint import load_po_checkpoint
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED


def _payload(so_id: int, *, ship_zone: str = "K", qty: float = 10) -> dict[str, Any]:
    p = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    p["id"] = so_id
    p["number"] = f"PO/{so_id}"
    p["charField3"] = ship_zone
    p["charField5"] = "Non - Buffer"
    p["detailItem"][0]["quantity"] = qty
    p["detailItem"][1]["quantity"] = qty
    return p


class UniverseClient:
    """Fake Accurate: id|asc contiguous; lastUpdate|desc by real timestamps."""

    def __init__(self) -> None:
        self.ids: list[int] = []
        self.details: dict[str, dict[str, Any]] = {}
        self.last_update: dict[int, str] = {}
        self._clock = 1_700_000_000
        self.fail_ids: set[str] = set()
        self.list_calls: list[dict[str, Any]] = []
        self.detail_calls = 0

    def _tick(self) -> str:
        self._clock += 1
        return str(self._clock)

    def seed(self, n: int, *, start: int = 1) -> None:
        for i in range(start, start + n):
            if i not in self.ids:
                self.ids.append(i)
            self.details[str(i)] = _payload(i)
            self.last_update[i] = self._tick()
        self.ids = sorted(set(self.ids))

    def add_new(self, so_id: int, *, ship_zone: str = "Z") -> None:
        self.ids.append(so_id)
        self.ids = sorted(set(self.ids))
        self.details[str(so_id)] = _payload(so_id, ship_zone=ship_zone)
        self.last_update[so_id] = self._tick()

    def mutate(self, so_id: int, *, ship_zone: str) -> None:
        self.details[str(so_id)] = _payload(so_id, ship_zone=ship_zone)
        self.last_update[so_id] = self._tick()

    def fetch_sales_order_list(self, *, params: dict[str, Any] | None = None) -> list[dict]:
        params = params or {}
        page = int(params.get("sp.page") or 1)
        size = int(params.get("sp.pageSize") or 20)
        sort = str(params.get("sp.sort") or "")
        self.list_calls.append({"page": page, "size": size, "sort": sort})
        if sort.startswith("id|asc") or sort == "id":
            ordered = sorted(self.ids)
        elif "lastUpdate" in sort:
            ordered = sorted(
                self.ids, key=lambda i: self.last_update.get(i, ""), reverse=True
            )
        elif sort.startswith("id|desc"):
            ordered = sorted(self.ids, reverse=True)
        else:
            ordered = list(self.ids)
        start = (page - 1) * size
        chunk = ordered[start : start + size]
        return [{"id": i, "lastUpdate": self.last_update.get(i)} for i in chunk]

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.detail_calls += 1
        key = str(accurate_id)
        if key in self.fail_ids:
            raise RuntimeError(f"forced_fail_{key}")
        return copy.deepcopy(self.details[key])


@pytest.fixture()
def pdb(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    ensure_po_indexes(db)
    return db


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()


def test_250_po_discovered_across_cycles_cap_100(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(250)
    seen: set[str] = set()
    for _ in range(5):
        r = po_job.run_po_auto_sync(
            pdb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=50
        )
        assert r["ok"] is True
        for doc in pdb["accurate_sales_orders"].find({}, {"accurate_id": 1}):
            seen.add(str(doc["accurate_id"]))
        cp = load_po_checkpoint(pdb)
        if cp.get("backfill_complete"):
            break
        assert r["partial"] is True
        assert r["complete"] is False
    assert len(seen) == 250
    assert load_po_checkpoint(pdb)["backfill_complete"] is True
    final = None
    for _ in range(8):
        final = po_job.run_po_auto_sync(
            pdb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=50
        )
        if final.get("complete"):
            break
    assert final is not None
    assert final["complete"] is True
    assert final["partial"] is False
    assert final["status"]["health"] == "HEALTHY"
    assert final.get("checkpoint", {}).get("incremental_crawl_complete") is True


def test_new_po_during_pagination_and_old_mutation(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(30)
    # First cycle: cap small so backfill incomplete
    r1 = po_job.run_po_auto_sync(
        pdb, client=client, page_size=10, max_pages=1, max_rows=100, timeout_seconds=50
    )
    assert r1["partial"] is True
    assert pdb["accurate_sales_orders"].count_documents({}) == 10
    # New high id + mutate already-synced id=1
    client.add_new(9999, ship_zone="NEW")
    client.mutate(1, ship_zone="UPD")
    # Finish backfill
    for _ in range(5):
        r = po_job.run_po_auto_sync(
            pdb, client=client, page_size=10, max_pages=2, max_rows=100, timeout_seconds=50
        )
        if r.get("complete"):
            break
    assert get_by_accurate_id(pdb, "9999") is not None
    # Incremental should pick mutation on id=1 (accepted path = metadata)
    assert get_by_accurate_id(pdb, "9999")["header"]["ship_zone"] == "NEW"
    # Force another incremental cycle after mutate already-synced unaccepted PO
    client.mutate(5, ship_zone="M5")
    po_job.run_po_auto_sync(
        pdb, client=client, page_size=10, max_pages=3, max_rows=100, timeout_seconds=50
    )
    assert get_by_accurate_id(pdb, "5")["header"]["ship_zone"] == "M5"


def test_mid_page_failure_does_not_skip_checkpoint(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(20)
    # Fail second id on first page (page_size=5 → ids 1..5, fail id 2)
    client.fail_ids.add("2")
    r1 = po_job.run_po_auto_sync(
        pdb, client=client, page_size=5, max_pages=2, max_rows=100, timeout_seconds=50
    )
    assert r1["ok"] is False
    assert r1["counters"]["failed"] >= 1
    cp = load_po_checkpoint(pdb)
    assert cp["next_page"] == 1  # tidak maju
    # Clear failure and retry — should resume page 1 without losing id 2
    client.fail_ids.clear()
    po_job.run_po_auto_sync(
        pdb, client=client, page_size=5, max_pages=5, max_rows=100, timeout_seconds=50
    )
    assert get_by_accurate_id(pdb, "2") is not None
    # Eventually all 20
    for _ in range(4):
        r = po_job.run_po_auto_sync(
            pdb, client=client, page_size=5, max_pages=2, max_rows=100, timeout_seconds=50
        )
        if r.get("complete"):
            break
    assert pdb["accurate_sales_orders"].count_documents({}) == 20


def test_accepted_po_protected_and_no_schedule_mutation(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(5)
    upsert_sales_order(pdb, normalize_sales_order(_payload(1)))
    pdb["accurate_sales_orders"].update_one(
        {"accurate_id": "1"},
        {
            "$set": {
                "operational.acceptance": {
                    "status": ACCEPTANCE_ACCEPTED,
                    "accepted_by": "owner@test",
                },
                "operational.gate_marker": "KEEP",
            }
        },
    )
    before_cmds = pdb["schedule_commands"].count_documents({})
    client.mutate(1, ship_zone="ZZ")
    po_job.run_po_auto_sync(
        pdb, client=client, page_size=5, max_pages=2, max_rows=100, timeout_seconds=50
    )
    doc = get_by_accurate_id(pdb, "1")
    assert doc["operational"]["acceptance"]["status"] == ACCEPTANCE_ACCEPTED
    assert doc["operational"]["acceptance"]["accepted_by"] == "owner@test"
    assert doc["operational"]["gate_marker"] == "KEEP"
    assert doc["header"]["ship_zone"] == "ZZ"  # safe metadata
    assert pdb["schedule_commands"].count_documents({}) == before_cmds
    assert pdb["production_allocations"].count_documents({}) == 0


def test_lock_blocks_concurrent_worker(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    token = locks.acquire_job_lock(pdb, job="po")
    client = UniverseClient()
    client.seed(3)
    result = po_job.run_po_auto_sync(
        pdb, client=client, page_size=5, max_pages=1, max_rows=100, timeout_seconds=50
    )
    assert result["ok"] is False
    assert result.get("error") == "SYNC_IN_PROGRESS"
    locks.release_job_lock(pdb, job="po", owner_token=token)


def test_empty_universe_completes(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    r = po_job.run_po_auto_sync(
        pdb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=50
    )
    assert r["complete"] is True
    assert load_po_checkpoint(pdb)["backfill_complete"] is True


def test_backfill_done_incremental_pending_not_complete(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """COMPLETE tidak diklaim saat crawl lastUpdate masih pending."""
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(40)
    for _ in range(5):
        r = po_job.run_po_auto_sync(
            pdb, client=client, page_size=5, max_pages=4, max_rows=100, timeout_seconds=50
        )
        if load_po_checkpoint(pdb).get("backfill_complete"):
            break
    assert load_po_checkpoint(pdb)["backfill_complete"] is True
    # Batasi halaman agar crawl incremental tidak selesai dalam 1 siklus.
    mid = po_job.run_po_auto_sync(
        pdb, client=client, page_size=5, max_pages=2, max_rows=100, timeout_seconds=50
    )
    cp = load_po_checkpoint(pdb)
    if mid.get("complete"):
        # Dataset kecil: incremental boleh selesai cepat — masih valid.
        assert cp.get("incremental_crawl_complete") is True
    else:
        assert mid["partial"] is True
        assert mid["complete"] is False
        assert (
            int(cp.get("incremental_next_page") or 1) > 1
            or not cp.get("incremental_crawl_complete")
        )


def test_timeout_keeps_checkpoint(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(50)
    r = po_job.run_po_auto_sync(
        pdb,
        client=client,
        page_size=10,
        max_pages=5,
        max_rows=100,
        timeout_seconds=0.0001,
    )
    assert r["stopped_reason"] == "timeout"
    assert r["complete"] is False
    assert load_po_checkpoint(pdb)["next_page"] >= 1


def test_old_mutation_found_beyond_three_page_window(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutasi PO lama harus ketemu meski >3 halaman lastUpdate lebih baru."""
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(15)
    for _ in range(3):
        r = po_job.run_po_auto_sync(
            pdb, client=client, page_size=5, max_pages=4, max_rows=100, timeout_seconds=50
        )
        if r.get("complete"):
            break
    assert load_po_checkpoint(pdb)["backfill_complete"] is True
    # 20 PO baru → >3 halaman (page_size=5) lebih baru dari id=3
    for i in range(1000, 1020):
        client.add_new(i, ship_zone="NEW")
    client.mutate(3, ship_zone="DEEP")
    found_old = False
    found_new = False
    for _ in range(12):
        po_job.run_po_auto_sync(
            pdb, client=client, page_size=5, max_pages=4, max_rows=100, timeout_seconds=50
        )
        doc = get_by_accurate_id(pdb, "3")
        if doc and doc["header"]["ship_zone"] == "DEEP":
            found_old = True
        if get_by_accurate_id(pdb, "1000") is not None:
            found_new = True
        if found_old and found_new:
            break
    assert found_old is True
    assert found_new is True
