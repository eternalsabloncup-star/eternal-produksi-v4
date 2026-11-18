"""Kontrak Accurate API Token 1.0.3 — HTTP mock + test vector (tanpa secret nyata)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.integrations.accurate.auth import (
    AccurateAuthError,
    api_signature,
    api_timestamp,
    build_auth_headers,
    normalize_host,
    request_headers,
)
from app.integrations.accurate.client import (
    PATH_API_TOKEN,
    PATH_SO_DETAIL,
    PATH_SO_LIST,
    AccurateReadClient,
    AccurateReadStatus,
)
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE

FAKE_TOKEN = "test-api-token-not-real"
FAKE_SECRET = "test-signature-secret-not-real"
ACCOUNT = "https://account.accurate.test"
DB_HOST = "https://public.accurate.test"


def test_timestamp_format_wib() -> None:
    dt = datetime(2026, 3, 20, 10, 5, 9, tzinfo=ZoneInfo("Asia/Jakarta"))
    assert api_timestamp(dt) == "20/03/2026 10:05:09"


def test_signature_hmac_sha256_base64_vector() -> None:
    ts = "20/03/2026 10:05:09"
    expected = base64.b64encode(
        hmac.new(FAKE_SECRET.encode(), ts.encode(), hashlib.sha256).digest()
    ).decode()
    assert api_signature(FAKE_SECRET, ts) == expected


def test_auth_headers_bearer_not_x_api_token() -> None:
    headers = build_auth_headers(
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        now=datetime(2026, 3, 20, 10, 5, 9, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert headers["X-Api-Timestamp"] == "20/03/2026 10:05:09"
    assert headers["X-Api-Signature"] == api_signature(
        FAKE_SECRET, "20/03/2026 10:05:09"
    )
    assert "X-Api-Token" not in headers
    assert FAKE_SECRET not in headers["X-Api-Signature"]
    assert FAKE_SECRET not in headers["Authorization"]


def test_request_headers_omits_session_when_empty() -> None:
    headers = request_headers(
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        session="",
        now=datetime(2026, 3, 20, 10, 5, 9, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert "X-Session-ID" not in headers
    assert headers["Authorization"].startswith("Bearer ")


def test_request_headers_adds_session_when_present() -> None:
    headers = request_headers(
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        session="sess-abc",
        now=datetime(2026, 3, 20, 10, 5, 9, tzinfo=ZoneInfo("Asia/Jakarta")),
    )
    assert headers["X-Session-ID"] == "sess-abc"


def test_normalize_host() -> None:
    assert normalize_host("https://public.accurate.id/accurate/") == (
        "https://public.accurate.id"
    )
    assert normalize_host("zeus.accurate.id") == "https://zeus.accurate.id"


def _token_payload(
    *,
    host: str = DB_HOST,
    db_id: int | None = 99,
    session: str | None = None,
) -> dict:
    database: dict = {"host": host}
    if db_id is not None:
        database["id"] = db_id
    body: dict = {"s": True, "d": {"database": database}}
    if session is not None:
        body["session"] = session
    return body


def _assert_signed(request: httpx.Request) -> None:
    assert request.headers.get("authorization") == f"Bearer {FAKE_TOKEN}"
    assert request.headers.get("x-api-timestamp")
    assert request.headers.get("x-api-signature")
    assert request.headers.get("x-api-token") is None


def test_open_db_host_with_empty_session() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "account.accurate.test" and request.url.path.endswith(
            PATH_API_TOKEN
        ):
            _assert_signed(request)
            assert request.headers.get("x-session-id") is None
            return httpx.Response(200, json=_token_payload(session=None))
        return httpx.Response(404, json={"s": False})

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    opened = client.open_db()
    assert opened.host == DB_HOST
    assert opened.session == ""
    assert opened.database_id == "99"


def test_open_db_host_with_nonempty_session() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(
                200, json=_token_payload(session="sess-from-token")
            )
        return httpx.Response(404)

    client = AccurateReadClient(
        base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    opened = client.open_db()
    assert opened.session == "sess-from-token"


def test_database_host_used_for_so_not_account_base() -> None:
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        if request.url.path.endswith(PATH_API_TOKEN):
            _assert_signed(request)
            return httpx.Response(200, json=_token_payload())
        if request.url.host == "public.accurate.test" and PATH_SO_DETAIL in str(
            request.url.path
        ):
            _assert_signed(request)
            assert request.headers.get("x-session-id") is None
            assert request.url.params.get("id") == "95106"
            return httpx.Response(
                200, json={"s": True, "d": SAMPLE_SALES_ORDER_STRUCTURE}
            )
        if PATH_SO_LIST in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": [{"id": 95106, "number": "PO/18/11/2026/000010"}]},
            )
        return httpx.Response(404, json={"s": False})

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    detail = client.fetch_sales_order_detail(95106)
    assert detail["id"] == 95106
    assert detail["detailItem"][0]["charField1"] == "Cetak"
    assert detail["detailItem"][1]["charField1"] == "Tidak Cetak"
    rows = client.fetch_sales_order_list()
    assert rows[0]["id"] == 95106
    assert "account.accurate.test" in seen_hosts
    assert "public.accurate.test" in seen_hosts


def test_host_switch_on_308_resigns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        _assert_signed(request)
        if host == "account.accurate.test":
            return httpx.Response(
                200,
                json=_token_payload(host="https://old.accurate.test", db_id=1),
            )
        if host == "old.accurate.test" and "sales-order/detail.do" in path:
            return httpx.Response(
                308,
                headers={
                    "Location": (
                        "https://new.accurate.test/accurate/api/sales-order/detail.do"
                    )
                },
            )
        if host == "new.accurate.test" and "sales-order/detail.do" in path:
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {
                        "id": 77,
                        "number": "SO/X",
                        "detailItem": [],
                    },
                },
            )
        return httpx.Response(500, json={"s": False})

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    detail = client.fetch_sales_order_detail(77)
    assert detail["number"] == "SO/X"
    assert client._cached_host == "https://new.accurate.test"


def test_so_detail_uses_session_header_when_cached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(
                200, json=_token_payload(session="live-sess")
            )
        if "sales-order/detail.do" in request.url.path:
            assert request.headers.get("x-session-id") == "live-sess"
            return httpx.Response(
                200, json={"s": True, "d": {"id": 1, "number": "A", "detailItem": []}}
            )
        return httpx.Response(404)

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(handler),
    )
    client.fetch_sales_order_detail(1)


def test_http_server_error() -> None:
    def err_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        return httpx.Response(500, json={"s": False})

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(err_handler),
    )
    with pytest.raises(ConnectionError, match="error server"):
        client.fetch_sales_order_detail(95106)


def test_http_timeout_on_api_token() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(timeout_handler),
    )
    with pytest.raises(TimeoutError, match="Timeout Accurate"):
        client.fetch_sales_order_detail(1)


def test_session_expired_401_retries_open_db() -> None:
    calls = {"token": 0, "detail": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            calls["token"] += 1
            return httpx.Response(200, json=_token_payload())
        if "sales-order/detail.do" in request.url.path:
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
    detail = client.fetch_sales_order_detail(5)
    assert detail["number"] == "OK"
    assert calls["token"] >= 2
    assert calls["detail"] == 2


def test_auth_rejects_false_token_response() -> None:
    def bad_auth(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"s": False, "message": "denied"})

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        transport=httpx.MockTransport(bad_auth),
    )
    with pytest.raises(AccurateAuthError, match="menolak"):
        client.open_db()


def test_database_pin_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_token_payload(db_id=111)
        )

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
    # Diagnostik aman: tanpa nilai ID mentah
    diag_text = str(exc.value.diagnostics)
    assert "823960" not in diag_text
    assert "111" not in diag_text
    assert any(d.get("field") == "d.database.id" for d in exc.value.diagnostics)


def test_database_pin_numeric_match_int_vs_str() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_token_payload(db_id=823960))

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        db_id="823960",
        transport=httpx.MockTransport(handler),
    )
    opened = client.open_db()
    assert opened.database_id == "823960"


def test_database_pin_uuid_vs_numeric_is_kind_mismatch() -> None:
    """Akar live VPS: ACCURATE_DB_ID 36-char UUID vs d.database.id numerik."""
    uuid_pin = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert len(uuid_pin) == 36

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_token_payload(db_id=95106))

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        db_id=uuid_pin,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AccurateAuthError) as exc:
        client.open_db()
    assert exc.value.code == "accurate_database_id_kind_mismatch"
    assert uuid_pin not in str(exc.value.diagnostics)
    pin_row = next(d for d in exc.value.diagnostics if d["field"] == "pin")
    assert pin_row["kind"] == "uuid"
    assert pin_row["length"] == 36
    assert pin_row["match"] is False
    id_row = next(d for d in exc.value.diagnostics if d["field"] == "d.database.id")
    assert id_row["kind"] == "numeric"
    assert id_row["type"] == "int"
    assert id_row["match"] is False


def test_database_pin_uuid_matches_uuid_field_alternate_shape() -> None:
    uuid_val = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "s": True,
                "d": {
                    "database": {
                        "id": 99,
                        "guid": uuid_val,
                        "host": DB_HOST,
                    }
                },
            },
        )

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        db_id=uuid_val.upper(),  # normalisasi case
        transport=httpx.MockTransport(handler),
    )
    opened = client.open_db()
    assert opened.database_id == uuid_val.lower()


def test_database_pin_missing_resolved_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"s": True, "d": {"database": {"host": DB_HOST}}},
        )

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        db_id="823960",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AccurateAuthError) as exc:
        client.open_db()
    assert exc.value.code == "accurate_database_id_missing"


def test_database_id_from_databaseId_string_format() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "s": True,
                "d": {"databaseId": "42", "host": DB_HOST},
            },
        )

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        db_id="42",
        transport=httpx.MockTransport(handler),
    )
    opened = client.open_db()
    assert opened.host == DB_HOST
    assert opened.database_id == "42"


def test_list_path_is_proven_not_rest_id_style() -> None:
    assert PATH_SO_DETAIL == "/accurate/api/sales-order/detail.do"
    assert PATH_SO_LIST == "/accurate/api/sales-order/list.do"
    assert "{id}" not in PATH_SO_DETAIL


def test_client_ready_without_db_pin() -> None:
    ready = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
    )
    assert ready.status == AccurateReadStatus.READY
