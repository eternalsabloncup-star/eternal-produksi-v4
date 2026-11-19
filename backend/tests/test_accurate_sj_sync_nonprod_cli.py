"""Smoke CLI nonprod helpers + discover/selected wiring (no live Accurate)."""

from __future__ import annotations

import importlib
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def test_parse_sj_ids_dedupes() -> None:
    cli = importlib.import_module("accurate_sj_sync_nonprod")
    assert cli._parse_sj_ids("1, 2,1,3") == ["1", "2", "3"]
    assert cli._parse_sj_ids("") == []
    assert cli._parse_sj_ids(None) == []


def test_cli_discover_empty_returns_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = importlib.import_module("accurate_sj_sync_nonprod")

    class _Settings:
        accurate_base_url = "https://public.accurate.id"
        sj_sync_enabled = True
        mongodb_db = "eternal_v4_test_cli"

    monkeypatch.setattr(cli, "_host_of", lambda url: "public.accurate.id")

    class _FakeClient:
        @classmethod
        def from_settings(cls):
            return cls()

    monkeypatch.setattr(
        "app.integrations.accurate.client.AccurateReadClient",
        _FakeClient,
    )

    def _fake_discover(**kwargs):
        return {
            "ok": True,
            "listed": 2,
            "pages": 1,
            "single_so_sj": 0,
            "multi_so_rejected": 1,
            "identity_failed": 0,
            "detail_failed": 0,
            "distinct_po_count": 0,
            "prefer_po_id": "95106",
            "prefer_po_sj_count": 0,
            "selected_po_id": None,
            "selected_sj_ids": [],
            "selected_sj_count": 0,
            "selection_reason": "none",
            "stopped_reason": "max_pages",
            "elapsed_seconds": 0.1,
            "writes_accurate": False,
        }

    monkeypatch.setattr(
        "app.services.delivery_sj_discovery.discover_sj_so_links",
        _fake_discover,
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _Settings(),
    )
    monkeypatch.setattr("app.config.clear_settings_cache", lambda: None)
    monkeypatch.setattr("app.db.close_client", lambda: None)

    # Re-import path: main imports inside function — patch modules before call.
    import app.config as config_mod
    import app.db as db_mod
    import app.services.delivery_sj_discovery as disc_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: _Settings())
    monkeypatch.setattr(config_mod, "clear_settings_cache", lambda: None)
    monkeypatch.setattr(db_mod, "close_client", lambda: None)
    monkeypatch.setattr(disc_mod, "discover_sj_so_links", _fake_discover)

    from app.integrations import accurate as accurate_pkg
    from app.integrations.accurate import client as client_mod

    monkeypatch.setattr(client_mod.AccurateReadClient, "from_settings", classmethod(lambda cls: _FakeClient()))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["--discover", "--prefer-po-id", "95106"])
    out = json.loads(buf.getvalue().strip())
    assert rc == 11
    assert out["ok"] is False
    assert out["stage"] == "discover"
    assert out["selected_sj_count"] == 0
    assert out["multi_so_rejected"] == 1
    assert out["writes_accurate"] is False


def test_cli_selected_mode_calls_targeted_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = importlib.import_module("accurate_sj_sync_nonprod")

    class _Settings:
        accurate_base_url = "https://public.accurate.id"
        sj_sync_enabled = True
        mongodb_db = "eternal_v4_test_cli"

    class _FakeClient:
        @classmethod
        def from_settings(cls):
            return "client"

    called: dict = {}

    def _fake_selected(db, user, sj_ids, **kwargs):
        called["sj_ids"] = list(sj_ids)
        called["client"] = kwargs.get("client")
        return {
            "ok": True,
            "created": 1,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "failed": 0,
            "listed": 1,
            "pages": 0,
            "skip_reasons": {},
            "failures": [],
            "stopped_reason": None,
            "selected": True,
        }

    import app.config as config_mod
    import app.db as db_mod
    import app.services.delivery_sync as sync_mod
    from app.integrations.accurate import client as client_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: _Settings())
    monkeypatch.setattr(config_mod, "clear_settings_cache", lambda: None)
    monkeypatch.setattr(db_mod, "close_client", lambda: None)
    monkeypatch.setattr(db_mod, "get_db", lambda settings=None: "db")
    monkeypatch.setattr(db_mod, "ensure_indexes", lambda db: None)
    monkeypatch.setattr(
        client_mod.AccurateReadClient, "from_settings", classmethod(lambda cls: "client")
    )
    monkeypatch.setattr(
        sync_mod, "sync_selected_delivery_orders_from_accurate", _fake_selected
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["--sj-ids", "10,20", "--actor-email", "a@eternal.local"])
    out = json.loads(buf.getvalue().strip())
    assert rc == 0
    assert out["mode"] == "selected"
    assert out["created"] == 1
    assert called["sj_ids"] == ["10", "20"]
    assert called["client"] == "client"
