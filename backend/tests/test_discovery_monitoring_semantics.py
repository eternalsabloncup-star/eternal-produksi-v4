"""Monitoring semantics: COMPLETE vs incremental_pending."""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.database import Database

from app.services.accurate_auto_sync import po_job
from app.services.accurate_auto_sync.checkpoint import save_job_checkpoint
from app.services.accurate_auto_sync.locks import acquire_job_lock, release_job_lock
from app.services.accurate_auto_sync.po_checkpoint import load_po_checkpoint
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from tests.test_po_pagination_checkpoint import UniverseClient, _enable


@pytest.fixture()
def pdb(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    return db


def test_complete_requires_incremental_crawl_after_backfill(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(12)
    # Force backfill done, incremental never started.
    token = acquire_job_lock(pdb, job="po")
    try:
        save_job_checkpoint(
            pdb,
            "po",
            {
                **load_po_checkpoint(pdb),
                "backfill_complete": True,
                "next_page": 3,
                "incremental_next_page": 1,
                "incremental_cycles_completed": 0,
                "incremental_crawl_complete": False,
                "phase": "incremental",
            },
            owner_token=token,
        )
    finally:
        release_job_lock(pdb, job="po", owner_token=token)

    # Cap pages so one cycle cannot finish 12/5 crawl.
    r = po_job.run_po_auto_sync(
        pdb, client=client, page_size=5, max_pages=2, max_rows=100, timeout_seconds=50
    )
    detail = (r.get("status") or {}).get("detail") or {}
    if r.get("complete"):
        assert detail.get("incremental_pending") is False
        assert load_po_checkpoint(pdb).get("incremental_crawl_complete") is True
    else:
        assert r["complete"] is False
        assert r["partial"] is True
        assert detail.get("incremental_pending") is True or int(
            load_po_checkpoint(pdb).get("incremental_next_page") or 1
        ) > 1


def test_full_incremental_pass_marks_complete(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(8)
    final: dict[str, Any] | None = None
    for _ in range(6):
        final = po_job.run_po_auto_sync(
            pdb, client=client, page_size=5, max_pages=5, max_rows=100, timeout_seconds=50
        )
        if final.get("complete"):
            break
    assert final is not None
    assert final["complete"] is True
    assert final["partial"] is False
    assert load_po_checkpoint(pdb)["backfill_complete"] is True
    assert load_po_checkpoint(pdb)["incremental_crawl_complete"] is True
    assert (final.get("status") or {}).get("detail", {}).get("incremental_pending") is False


def test_mid_page_failure_keeps_checkpoint_incomplete(
    pdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    client = UniverseClient()
    client.seed(10)
    client.fail_ids.add("2")
    r = po_job.run_po_auto_sync(
        pdb, client=client, page_size=5, max_pages=2, max_rows=100, timeout_seconds=50
    )
    assert r["ok"] is False
    assert r["complete"] is False
    assert load_po_checkpoint(pdb)["next_page"] == 1
