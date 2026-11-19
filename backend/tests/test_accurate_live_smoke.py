"""Tes mock validasi + script smoke Accurate (tanpa live, tanpa Mongo)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest

from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.integrations.accurate.smoke_validate import (
    SmokeExpectations,
    format_smoke_report,
    validate_sales_order_smoke,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "accurate_live_smoke.py"


def _load_smoke_script():
    spec = importlib.util.spec_from_file_location("accurate_live_smoke", SCRIPTS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validate_sample_payload_passes() -> None:
    result = validate_sales_order_smoke(SAMPLE_SALES_ORDER_STRUCTURE)
    assert result.ok is True
    assert result.auth_ok is True
    assert result.accurate_id_match is True
    assert result.po_number_match is True
    assert result.detail_count_match is True
    assert result.process_mapping_ok is True
    assert result.observed_process_raws == ["Cetak", "Tidak Cetak"]
    report = format_smoke_report(result)
    assert "PASS" in report
    assert "Pelanggan" not in report
    assert "Bearer" not in report


def test_validate_fails_on_wrong_po_and_process() -> None:
    bad = {
        "id": 95106,
        "number": "PO/WRONG",
        "detailItem": [
            {"id": 1, "charField1": "Polos"},
            {"id": 2, "charField1": "Cetak"},
        ],
    }
    result = validate_sales_order_smoke(bad)
    assert result.ok is False
    assert result.po_number_match is False
    assert result.process_mapping_ok is False
    assert result.detail_count_match is True


def test_validate_auth_failure() -> None:
    result = validate_sales_order_smoke(None, auth_ok=False)
    assert result.ok is False
    assert result.auth_ok is False


def test_script_prefers_v4_db_id_over_legacy_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_smoke_script()
    monkeypatch.setenv("ACCURATE_ACCOUNT_BASE", "https://account.accurate.test")
    monkeypatch.setenv("ACCURATE_API_TOKEN", "tok-test")
    monkeypatch.setenv("ACCURATE_API_SIGNATURE_SECRET", "sec-test")
    monkeypatch.setenv(
        "ACCURATE_DB_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )  # UUID ERP lama
    monkeypatch.setenv("V4_ACCURATE_DB_ID", "823960")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/api-token.do"):
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {
                        "database": {
                            "id": 823960,
                            "host": "https://public.accurate.test",
                        }
                    },
                },
            )
        if "sales-order/detail.do" in request.url.path:
            return httpx.Response(
                200, json={"s": True, "d": SAMPLE_SALES_ORDER_STRUCTURE}
            )
        return httpx.Response(404)

    code = mod.run_smoke(transport=httpx.MockTransport(handler))
    assert code == 0


def test_script_mock_transport_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_smoke_script()
    monkeypatch.setenv("ACCURATE_ACCOUNT_BASE", "https://account.accurate.test")
    monkeypatch.setenv("ACCURATE_API_TOKEN", "tok-test")
    monkeypatch.setenv("ACCURATE_API_SIGNATURE_SECRET", "sec-test")
    monkeypatch.delenv("ACCURATE_DB_ID", raising=False)
    monkeypatch.delenv("V4_ACCURATE_DB_ID", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/api-token.do"):
            assert request.headers.get("authorization", "").startswith("Bearer ")
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {
                        "database": {
                            "id": 1,
                            "host": "https://public.accurate.test",
                        }
                    },
                },
            )
        if "sales-order/detail.do" in request.url.path:
            assert request.url.params.get("id") == "95106"
            return httpx.Response(
                200, json={"s": True, "d": SAMPLE_SALES_ORDER_STRUCTURE}
            )
        return httpx.Response(404, json={"s": False})

    code = mod.run_smoke(transport=httpx.MockTransport(handler))
    assert code == 0


def test_script_exits_nonzero_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_smoke_script()
    for key in (
        "ACCURATE_ACCOUNT_BASE",
        "ACCURATE_BASE_URL",
        "V4_ACCURATE_ACCOUNT_BASE",
        "V4_ACCURATE_BASE_URL",
        "ACCURATE_API_TOKEN",
        "V4_ACCURATE_API_TOKEN",
        "ACCURATE_API_SIGNATURE_SECRET",
        "ACCURATE_SIGNATURE_SECRET",
        "V4_ACCURATE_API_SIGNATURE_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    code = mod.run_smoke(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert code == 2


def test_script_exits_nonzero_on_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_smoke_script()
    monkeypatch.setenv("ACCURATE_BASE_URL", "https://account.accurate.test")
    monkeypatch.setenv("ACCURATE_API_TOKEN", "tok")
    monkeypatch.setenv("ACCURATE_SIGNATURE_SECRET", "sec")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/api-token.do"):
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {"database": {"id": 1, "host": "https://db.accurate.test"}},
                },
            )
        return httpx.Response(
            200,
            json={
                "s": True,
                "d": {
                    "id": 999,
                    "number": "OTHER",
                    "detailItem": [{"id": 1, "charField1": "Cetak"}],
                },
            },
        )

    code = mod.run_smoke(transport=httpx.MockTransport(handler))
    assert code == 1


def test_build_client_does_not_need_mongo(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_smoke_script()
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    # V4_* diprioritaskan skrip — bersihkan agar uji hermetik di shell ber-env live.
    for key in (
        "V4_ACCURATE_ACCOUNT_BASE",
        "V4_ACCURATE_BASE_URL",
        "V4_ACCURATE_API_TOKEN",
        "V4_ACCURATE_API_SIGNATURE_SECRET",
        "V4_ACCURATE_DB_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ACCURATE_ACCOUNT_BASE", "https://account.example")
    monkeypatch.setenv("ACCURATE_API_TOKEN", "t")
    monkeypatch.setenv("ACCURATE_API_SIGNATURE_SECRET", "s")
    client = mod.build_client_from_environ()
    assert client.account_base_url == "https://account.example"
    assert client.status.value == "ready"


def test_custom_expectations_detail_count() -> None:
    payload = {
        "id": "95106",
        "number": "PO/18/11/2026/000010",
        "detailItem": [{"id": 1, "charField1": "Cetak"}],
    }
    result = validate_sales_order_smoke(
        payload,
        expectations=SmokeExpectations(detail_count=1, process_raws=("Cetak",)),
    )
    assert result.ok is True
