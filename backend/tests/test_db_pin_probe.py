"""Tes probe pin numerik — tanpa mencetak/menyimpan nilai ke assert stdout."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import httpx
import pytest

from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.db_pin_probe import (
    run_db_pin_probe,
    select_primary_numeric_candidate,
    write_pin_file,
)
from app.integrations.accurate.auth import collect_database_id_candidates
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE

ACCOUNT = "https://account.accurate.test"
DB_HOST = "https://public.accurate.test"
FAKE_TOKEN = "tok"
FAKE_SECRET = "sec"
NUMERIC_ID = 823960


def _handler_factory(db_id: int | None = NUMERIC_ID):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/api-token.do"):
            database: dict = {"host": DB_HOST}
            if db_id is not None:
                database["id"] = db_id
            return httpx.Response(200, json={"s": True, "d": {"database": database}})
        if "sales-order/detail.do" in request.url.path:
            assert request.url.params.get("id") == "95106"
            return httpx.Response(
                200, json={"s": True, "d": SAMPLE_SALES_ORDER_STRUCTURE}
            )
        return httpx.Response(404)

    return handler


def test_select_primary_prefers_d_database_id() -> None:
    cands = collect_database_id_candidates(
        {
            "s": True,
            "d": {"database": {"id": 99, "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}},
        }
    )
    primary = select_primary_numeric_candidate(cands)
    assert primary is not None
    assert primary.field == "d.database.id"
    assert primary.kind == "numeric"


def test_write_pin_file_mode_600(tmp_path: Path) -> None:
    priv = tmp_path / "private"
    priv.mkdir(mode=0o700)
    os.chmod(priv, 0o700)
    path = priv / "v4_db_pin"
    write_pin_file(path, str(NUMERIC_ID))
    assert path.read_text(encoding="utf-8").strip() == str(NUMERIC_ID)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_write_pin_file_refuses_overwrite(tmp_path: Path) -> None:
    priv = tmp_path / "private2"
    priv.mkdir(mode=0o700)
    os.chmod(priv, 0o700)
    path = priv / "pin.txt"
    write_pin_file(path, "111")
    with pytest.raises(FileExistsError):
        write_pin_file(path, "222")
    assert path.read_text(encoding="utf-8").strip() == "111"


def test_write_pin_file_refuses_symlink_target(tmp_path: Path) -> None:
    priv = tmp_path / "private3"
    priv.mkdir(mode=0o700)
    os.chmod(priv, 0o700)
    real = priv / "real"
    real.write_text("x", encoding="utf-8")
    link = priv / "pin_link"
    link.symlink_to(real)
    with pytest.raises(FileExistsError):
        write_pin_file(link, "999")


def test_probe_writes_pin_verifies_so_without_leaking_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    priv = tmp_path / "probe_priv"
    priv.mkdir(mode=0o700)
    os.chmod(priv, 0o700)
    pin_path = priv / "pin.txt"
    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(_handler_factory()),
    )
    result = run_db_pin_probe(client, pin_out_path=pin_path, verify_sales_order=True)
    assert result.ok is True
    assert result.primary_kind == "numeric"
    assert result.primary_field == "d.database.id"
    assert result.so_verify_ok is True
    assert pin_path.read_text(encoding="utf-8").strip() == str(NUMERIC_ID)
    report = result.format_report()
    assert str(NUMERIC_ID) not in report
    assert "tok" not in report
    assert "sec" not in report
    print(report)
    captured = capsys.readouterr().out
    assert str(NUMERIC_ID) not in captured


def test_probe_missing_numeric_id() -> None:
    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(_handler_factory(db_id=None)),
    )
    result = run_db_pin_probe(client, verify_sales_order=False)
    assert result.ok is False
    assert result.code == "accurate_database_id_missing"


def test_script_probe_exit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "accurate_db_pin_probe.py"
    spec = importlib.util.spec_from_file_location("accurate_db_pin_probe", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    priv = tmp_path / "script_priv"
    priv.mkdir(mode=0o700)
    os.chmod(priv, 0o700)
    pin_path = priv / "out.pin"
    monkeypatch.setenv("V4_ACCURATE_ACCOUNT_BASE", ACCOUNT)
    monkeypatch.setenv("V4_ACCURATE_API_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("V4_ACCURATE_API_SIGNATURE_SECRET", FAKE_SECRET)
    monkeypatch.setenv("V4_ACCURATE_DB_PIN_OUT", str(pin_path))
    monkeypatch.delenv("ACCURATE_DB_ID", raising=False)
    monkeypatch.delenv("V4_ACCURATE_DB_ID", raising=False)

    code = mod.run(transport=httpx.MockTransport(_handler_factory()))
    assert code == 0
    assert pin_path.exists()
