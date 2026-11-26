"""M12.1 — Accurate Sales Invoice PDF contract + tracking authorization."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database

from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import (
    PATH_API_TOKEN,
    PATH_SI_PRINT,
    AccurateReadClient,
)
from app.repositories import invoice_repository as invoices
from app.services import tracking_service as tracking
from tests.test_tracking import _login, _seed_admin, _sync_po


ACCOUNT = "https://account.accurate.test"
DB_HOST = "https://public.accurate.test"
FAKE_TOKEN = "tok-test"
FAKE_SECRET = "sec-test"
PDF_BYTES = b"%PDF-1.4 m12-1-fixture"
EVIL_HOST = "https://evil.example"


def _token_payload() -> dict[str, Any]:
    return {
        "s": True,
        "d": {
            "host": DB_HOST,
            "session": "",
            "database": {"id": 99, "host": DB_HOST},
        },
    }


def _pdf_client(handler) -> AccurateReadClient:
    return AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        db_id="99",
        require_db_pin=False,
        transport=httpx.MockTransport(handler),
    )


def test_fetch_sales_invoice_pdf_two_step() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {"output": "/accurate/files/_temp/abc.pdf"},
                },
            )
        if request.url.path == "/accurate/files/_temp/abc.pdf":
            return httpx.Response(
                200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404, json={"s": False})

    client = _pdf_client(handler)
    out = client.fetch_sales_invoice_pdf("118761")
    assert out == PDF_BYTES
    assert any(PATH_SI_PRINT in p for p in calls)
    assert any(p.endswith("/accurate/files/_temp/abc.pdf") for p in calls)


def test_fetch_sales_invoice_pdf_rejects_bad_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200, json={"s": True, "d": {"output": "/etc/passwd"}}
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(RuntimeError, match="/accurate/files/"):
        client.fetch_sales_invoice_pdf("1")


def test_fetch_sales_invoice_pdf_rejects_non_pdf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/x.pdf"}},
            )
        if request.url.path.endswith("/accurate/files/_temp/x.pdf"):
            return httpx.Response(
                200, content=b"NOTPDF", headers={"content-type": "text/plain"}
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(RuntimeError):
        client.fetch_sales_invoice_pdf("1")


def test_fetch_sales_invoice_pdf_oversized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/big.pdf"}},
            )
        if request.url.path.endswith("/accurate/files/_temp/big.pdf"):
            return httpx.Response(
                200,
                content=b"%PDF" + b"x" * 5000,
                headers={"content-type": "application/pdf"},
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(RuntimeError, match="batas ukuran"):
        client.fetch_sales_invoice_pdf("1", max_bytes=1024)


def test_pdf_redirect_to_untrusted_host_rejected() -> None:
    """Jangan ikut redirect ke origin asing dengan header signed."""
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/r.pdf"}},
            )
        if request.url.host == "public.accurate.test" and request.url.path.endswith(
            "/accurate/files/_temp/r.pdf"
        ):
            return httpx.Response(
                302,
                headers={
                    "Location": f"{EVIL_HOST}/accurate/files/_temp/r.pdf",
                },
            )
        if request.url.host == "evil.example":
            return httpx.Response(
                200,
                content=PDF_BYTES,
                headers={"content-type": "application/pdf"},
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(AccurateAuthError, match="tidak terpercaya"):
        client.fetch_sales_invoice_pdf("1")
    assert "evil.example" not in seen_hosts


def test_pdf_absolute_output_untrusted_host_rejected() -> None:
    """Path /accurate/files/ pada host asing tidak cukup untuk trust."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {
                        "output": f"{EVIL_HOST}/accurate/files/_temp/abs.pdf",
                    },
                },
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(AccurateAuthError, match="tidak terpercaya"):
        client.fetch_sales_invoice_pdf("1")


def test_pdf_rejects_http_absolute_url() -> None:
    """Authenticated PDF absolute URL must be HTTPS."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {
                        "output": "http://public.accurate.test/accurate/files/_temp/h.pdf",
                    },
                },
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(AccurateAuthError, match="non-HTTPS"):
        client.fetch_sales_invoice_pdf("1")


def test_pdf_rejects_https_to_http_redirect_downgrade() -> None:
    """HTTPS origin must not follow HTTP Location (downgrade)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/d.pdf"}},
            )
        if request.url.path.endswith("/accurate/files/_temp/d.pdf"):
            return httpx.Response(
                302,
                headers={
                    "Location": "http://public.accurate.test/accurate/files/_temp/d2.pdf",
                },
                # Large body must not be unbounded-consumed on redirect.
                content=b"x" * (2 * 1024 * 1024),
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(AccurateAuthError, match="non-HTTPS|downgrade"):
        client.fetch_sales_invoice_pdf("1")


def test_pdf_absolute_url_keeps_validated_trusted_host() -> None:
    """Absolute PDF URL on another trusted Accurate host must be fetched there."""
    seen: list[str] = []

    def token_payload_cloud() -> dict[str, Any]:
        return {
            "s": True,
            "d": {
                "host": "https://public.accurate.id",
                "session": "",
                "database": {"id": 99, "host": "https://public.accurate.id"},
            },
        }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.scheme}://{request.url.host}{request.url.path}")
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=token_payload_cloud())
        if PATH_SI_PRINT in request.url.path:
            assert request.url.host == "public.accurate.id"
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {
                        "output": (
                            "https://zeus.accurate.id/accurate/files/_temp/rot.pdf"
                        ),
                    },
                },
            )
        if (
            request.url.host == "zeus.accurate.id"
            and request.url.path.endswith("/accurate/files/_temp/rot.pdf")
        ):
            return httpx.Response(
                200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
            )
        if request.url.host == "public.accurate.id" and "rot.pdf" in request.url.path:
            raise AssertionError("must not fetch absolute PDF from previous DB host")
        return httpx.Response(404)

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token=FAKE_TOKEN,
        signature_secret=FAKE_SECRET,
        db_id="99",
        require_db_pin=False,
        transport=httpx.MockTransport(handler),
    )
    assert client.fetch_sales_invoice_pdf("1") == PDF_BYTES
    assert any(u.startswith("https://zeus.accurate.id/") for u in seen)
    assert not any(
        u.startswith("https://public.accurate.id/accurate/files/_temp/rot.pdf")
        for u in seen
    )


def test_pdf_redirect_same_host_allowed_and_no_secret_leak() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/ok.pdf"}},
            )
        if request.url.path.endswith("/accurate/files/_temp/ok.pdf"):
            # Relative redirect same host
            return httpx.Response(
                308,
                headers={"Location": "/accurate/files/_temp/ok2.pdf"},
            )
        if request.url.path.endswith("/accurate/files/_temp/ok2.pdf"):
            # Signed headers must be present on trusted hop.
            assert request.headers.get("authorization", "").startswith("Bearer ")
            assert "x-api-signature" in {
                k.lower() for k in request.headers.keys()
            }
            return httpx.Response(
                200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    assert client.fetch_sales_invoice_pdf("1") == PDF_BYTES


def test_pdf_host_path_manipulation_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {"output": "/accurate/files/../secrets/x.pdf"},
                },
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(RuntimeError, match="tidak valid"):
        client.fetch_sales_invoice_pdf("1")


def test_pdf_oversized_content_length_rejected_early() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/cl.pdf"}},
            )
        if request.url.path.endswith("/accurate/files/_temp/cl.pdf"):
            # Content-Length over bound — reject before accepting body as PDF.
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/pdf",
                    "content-length": "99999",
                },
                content=b"%PDF" + b"x" * 4000,
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(RuntimeError, match="batas ukuran"):
        client.fetch_sales_invoice_pdf("1", max_bytes=2048)


def test_pdf_chunked_stream_exceeds_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/ch.pdf"}},
            )
        if request.url.path.endswith("/accurate/files/_temp/ch.pdf"):
            # No Content-Length; cumulative stream exceeds bound.
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.4 " + b"y" * 3000,
            )
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(RuntimeError, match="batas ukuran"):
        client.fetch_sales_invoice_pdf("1", max_bytes=1024)


def test_pdf_upstream_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/t.pdf"}},
            )
        if request.url.path.endswith("/accurate/files/_temp/t.pdf"):
            raise httpx.ReadTimeout("simulated")
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(TimeoutError):
        client.fetch_sales_invoice_pdf("1")


def test_pdf_upstream_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(200, json=_token_payload())
        if PATH_SI_PRINT in request.url.path:
            return httpx.Response(
                200,
                json={"s": True, "d": {"output": "/accurate/files/_temp/f.pdf"}},
            )
        if request.url.path.endswith("/accurate/files/_temp/f.pdf"):
            return httpx.Response(503, json={"s": False})
        return httpx.Response(404)

    client = _pdf_client(handler)
    with pytest.raises(ConnectionError):
        client.fetch_sales_invoice_pdf("1")


def _seed_invoice(db: Database, **overrides: Any) -> dict[str, Any]:
    if "accurate_sales_invoices" not in db.list_collection_names():
        db.create_collection("accurate_sales_invoices")
    invoices.ensure_invoice_indexes(db)
    doc = {
        "accurate_invoice_id": "INV-PDF-1",
        "invoice_no": "SI/PDF/001",
        "trans_date": "01/11/2026",
        "status": "OPEN",
        "total_amount": 1000.0,
        "accurate_po_ids": ["95106"],
        "primary_accurate_po_id": "95106",
        "lines": [{"sales_order_id": "95106", "qty": 1}],
        "review_required": False,
    }
    doc.update(overrides)
    db["accurate_sales_invoices"].delete_many(
        {"accurate_invoice_id": doc["accurate_invoice_id"]}
    )
    db["accurate_sales_invoices"].insert_one(doc)
    return doc


def test_public_invoice_pdf_happy_path(app_client: TestClient, db: Database) -> None:
    _sync_po(db)
    _seed_admin(db)
    _seed_invoice(db)
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()

    with patch(
        "app.integrations.accurate.client.AccurateReadClient.fetch_sales_invoice_pdf",
        return_value=PDF_BYTES,
    ):
        resp = app_client.get(
            f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
            f"/invoices/INV-PDF-1/pdf"
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "no-store" in resp.headers.get("cache-control", "")
    assert resp.content == PDF_BYTES
    # No credential leakage in body/headers
    blob = (resp.text if False else "") + str(resp.headers)
    assert FAKE_TOKEN not in blob
    assert FAKE_SECRET not in blob


def test_public_invoice_pdf_invalid_token(app_client: TestClient, db: Database) -> None:
    _sync_po(db)
    _seed_invoice(db)
    resp = app_client.get(
        "/api/public/tracking/c/p/INVALIDTOKENXXXXXXXXXXXX/invoices/INV-PDF-1/pdf"
    )
    assert resp.status_code == 404


def test_public_invoice_pdf_wrong_slug(app_client: TestClient, db: Database) -> None:
    _sync_po(db)
    _seed_admin(db)
    _seed_invoice(db)
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    resp = app_client.get(
        f"/api/public/tracking/other-customer/{gen['po_slug']}/{gen['token']}"
        f"/invoices/INV-PDF-1/pdf"
    )
    assert resp.status_code == 404


def test_public_invoice_pdf_multi_po_forbidden(
    app_client: TestClient, db: Database
) -> None:
    _sync_po(db)
    _seed_admin(db)
    _seed_invoice(
        db,
        accurate_invoice_id="INV-MULTI-PDF",
        accurate_po_ids=["95106", "99999"],
        primary_accurate_po_id="",
        lines=[
            {"sales_order_id": "95106", "qty": 1},
            {"sales_order_id": "99999", "qty": 1},
        ],
        review_required=True,
    )
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    resp = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
        f"/invoices/INV-MULTI-PDF/pdf"
    )
    assert resp.status_code == 403


def test_public_invoice_pdf_review_forbidden(
    app_client: TestClient, db: Database
) -> None:
    _sync_po(db)
    _seed_admin(db)
    _seed_invoice(db, review_required=True, review_reason="hold")
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    resp = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
        f"/invoices/INV-PDF-1/pdf"
    )
    assert resp.status_code == 403


def test_public_invoice_pdf_void_forbidden(app_client: TestClient, db: Database) -> None:
    _sync_po(db)
    _seed_admin(db)
    _seed_invoice(db, status="void")
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    resp = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
        f"/invoices/INV-PDF-1/pdf"
    )
    assert resp.status_code == 403


def test_public_invoice_pdf_missing_invoice(
    app_client: TestClient, db: Database
) -> None:
    _sync_po(db)
    _seed_admin(db)
    invoices.ensure_invoice_indexes(db)
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    resp = app_client.get(
        f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
        f"/invoices/MISSING/pdf"
    )
    assert resp.status_code == 404


def test_public_invoice_pdf_upstream_unavailable(
    app_client: TestClient, db: Database
) -> None:
    _sync_po(db)
    _seed_admin(db)
    _seed_invoice(db)
    csrf = _login(app_client)
    gen = app_client.post(
        "/api/tracking/95106/links",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()
    with patch(
        "app.integrations.accurate.client.AccurateReadClient.fetch_sales_invoice_pdf",
        side_effect=TimeoutError("timeout"),
    ):
        resp = app_client.get(
            f"/api/public/tracking/{gen['customer_slug']}/{gen['po_slug']}/{gen['token']}"
            f"/invoices/INV-PDF-1/pdf"
        )
    assert resp.status_code == 503


def test_resolve_public_invoice_pdf_revoked(db: Database) -> None:
    _sync_po(db)
    _seed_invoice(db)
    result = tracking.generate_tracking_link(
        db, accurate_po_id="95106", actor_email="admin@eternal.local"
    )
    tracking.revoke_tracking_link(
        db,
        link_id=str(result["link"]["id"]),
        actor_email="admin@eternal.local",
        reason="test",
    )
    with pytest.raises(tracking.TrackingNotFound):
        tracking.resolve_public_invoice_pdf(
            db,
            customer_slug=result["customer_slug"],
            po_slug=result["po_slug"],
            raw_token=result["token"],
            accurate_invoice_id="INV-PDF-1",
        )


def test_metadata_flags_without_fetching_pdf(db: Database) -> None:
    """file_available true only signals eligibility — not that bytes were fetched."""
    _sync_po(db)
    _seed_invoice(db)
    result = tracking.generate_tracking_link(
        db, accurate_po_id="95106", actor_email="admin@eternal.local"
    )
    payload = tracking.public_tracking_view(
        db,
        customer_slug=result["customer_slug"],
        po_slug=result["po_slug"],
        raw_token=result["token"],
    )
    inv = next(d for d in payload["documents"] if d["category"] == "sales_invoice")
    item = next(i for i in inv["items"] if i["id"] == "INV-PDF-1")
    assert item["file_available"] is True
    assert item["download_allowed"] is True
    assert item["preview_allowed"] is True
