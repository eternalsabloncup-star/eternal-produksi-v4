"""Accurate Delivery Order read client — GET only, mock HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import (
    PATH_API_TOKEN,
    PATH_DO_DETAIL,
    PATH_DO_LIST,
    AccurateReadClient,
)

FAKE_TOKEN = "test-api-token-not-real"
FAKE_SECRET = "test-signature-secret-not-real"
ACCOUNT = "https://account.accurate.test"
DB_HOST = "https://public.accurate.test"
LIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"
)


def _token_payload(*, host: str = DB_HOST, db_id: int = 99) -> dict:
    return {"s": True, "d": {"database": {"id": db_id, "host": host}}}


def _assert_signed(request: httpx.Request) -> None:
    assert request.headers.get("authorization") == f"Bearer {FAKE_TOKEN}"
    assert request.headers.get("x-api-timestamp")
    assert request.headers.get("x-api-signature")
    assert request.method == "GET" or request.url.path.endswith(PATH_API_TOKEN)


def test_do_paths_are_get_style_not_rest_write() -> None:
    assert PATH_DO_LIST == "/accurate/api/delivery-order/list.do"
    assert PATH_DO_DETAIL == "/accurate/api/delivery-order/detail.do"
    assert "save.do" not in PATH_DO_LIST
    assert "delete.do" not in PATH_DO_DETAIL
    assert "{id}" not in PATH_DO_DETAIL


def test_fetch_delivery_order_list_and_detail() -> None:
    detail = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path.endswith(PATH_API_TOKEN):
            assert request.method == "POST"
            return httpx.Response(200, json=_token_payload())
        _assert_signed(request)
        assert request.url.host == "public.accurate.test"
        if PATH_DO_LIST in request.url.path:
            assert request.method == "GET"
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": [
                        {"id": 880101, "number": "TEST-SJ/19/11/2026/000099"},
                        {"id": 880102, "number": "TEST-SJ/19/11/2026/000100"},
                    ],
                },
            )
        if PATH_DO_DETAIL in request.url.path:
            assert request.method == "GET"
            assert request.url.params.get("id") == "880101"
            return httpx.Response(200, json={"s": True, "d": detail})
        return httpx.Response(404, json={"s": False})

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    rows = client.fetch_delivery_order_list(params={"sp.page": 1, "sp.pageSize": 20})
    assert len(rows) == 2
    assert rows[0]["id"] == 880101
    body = client.fetch_delivery_order_detail(880101)
    assert body["id"] == 880101
    assert len(body["detailItem"]) == 2
    assert "POST" in methods
    assert methods.count("GET") >= 2
    assert "PUT" not in methods
    assert "DELETE" not in methods


def test_do_detail_unwraps_delivery_order_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_DO_DETAIL in request.url.path:
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {
                        "deliveryOrder": {
                            "id": 1,
                            "number": "SJ/X",
                            "detailItem": [{"id": 9}],
                        }
                    },
                },
            )
        return httpx.Response(404)

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    detail = client.fetch_delivery_order_detail(1)
    assert detail["number"] == "SJ/X"


def test_do_http_401_retries_session() -> None:
    calls = {"token": 0, "detail": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            calls["token"] += 1
            return httpx.Response(200, json=_token_payload())
        if PATH_DO_DETAIL in request.url.path:
            calls["detail"] += 1
            if calls["detail"] == 1:
                return httpx.Response(401, json={"s": False})
            return httpx.Response(
                200, json={"s": True, "d": {"id": 5, "number": "OK", "detailItem": []}}
            )
        return httpx.Response(404)

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    assert client.fetch_delivery_order_detail(5)["number"] == "OK"
    assert calls["token"] >= 2


def test_do_http_429_and_500_raise() -> None:
    def handler_429(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        return httpx.Response(429, json={"s": False})

    def handler_500(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        return httpx.Response(500, json={"s": False})

    client_429 = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler_429),
    )
    # 429: setelah retry → AccurateAuthError (4xx), bukan write retry ke Accurate.
    with pytest.raises(AccurateAuthError):
        client_429.fetch_delivery_order_list()

    client_500 = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler_500),
    )
    with pytest.raises(ConnectionError):
        client_500.fetch_delivery_order_list()


def test_do_timeout() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(timeout_handler),
    )
    with pytest.raises(TimeoutError):
        client.fetch_delivery_order_detail(1)


def test_do_database_pin_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_token_payload(db_id=111))

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        db_id="823960",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AccurateAuthError) as exc:
        client.open_db()
    assert exc.value.code == "accurate_database_mismatch"


def test_do_host_308_resigns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "account.accurate.test":
            return httpx.Response(
                200, json=_token_payload(host="https://old.accurate.test")
            )
        if host == "old.accurate.test" and PATH_DO_DETAIL in path:
            return httpx.Response(
                308,
                headers={
                    "Location": (
                        "https://new.accurate.test/accurate/api/delivery-order/detail.do"
                    )
                },
            )
        if host == "new.accurate.test" and PATH_DO_DETAIL in path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"id": 77, "number": "SJ/X", "detailItem": []}},
            )
        return httpx.Response(500, json={"s": False})

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    assert client.fetch_delivery_order_detail(77)["number"] == "SJ/X"
    assert client._cached_host == "https://new.accurate.test"
