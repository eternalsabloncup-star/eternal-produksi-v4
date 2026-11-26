"""Accurate read client — API Token 1.0.3 (kontrak accurate-gateway terbukti).

Alur:
1. Account base → POST /api/api-token.do (signed headers)
2. open_db / ensure_db_session → (database host, session) — session boleh ""
3. Sales Order / Delivery Order di host database:
   - /accurate/api/sales-order/list.do | detail.do?id=
   - /accurate/api/delivery-order/list.do | detail.do?id=

HTTP 308: update host + re-sign (jangan ikut redirect dengan signature basi).
401: clear cache, buka ulang sesi sekali.
Tidak menulis ke Accurate (GET saja). Tidak memakai REST /{resource}/{id}.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Settings, get_settings
from app.integrations.accurate.auth import (
    AccurateAuthError,
    DbSession,
    collect_database_id_candidates,
    extract_database,
    extract_optional_session,
    match_database_pin,
    normalize_host,
    request_headers,
)

PATH_API_TOKEN = "/api/api-token.do"
PATH_OPEN_DB = "/api/open-db.do"  # mode OAuth / alternatif — bukan jalur API Token terbukti
PATH_SO_LIST = "/accurate/api/sales-order/list.do"
PATH_SO_DETAIL = "/accurate/api/sales-order/detail.do"
# Delivery Order (Surat Jalan) — GET only; terbukti live di VPS Owner.
PATH_DO_LIST = "/accurate/api/delivery-order/list.do"
PATH_DO_DETAIL = "/accurate/api/delivery-order/detail.do"
# Sales Invoice / Sales Receipt — GET only; path + shape diverifikasi live (read-only).
PATH_SI_LIST = "/accurate/api/sales-invoice/list.do"
PATH_SI_DETAIL = "/accurate/api/sales-invoice/detail.do"
# Sales Invoice PDF — verified LIVE 2026-11-27 (print.do → temp path → PDF bytes).
# Official OpenAPI omits print.do; contract proven by read-only live inspection.
PATH_SI_PRINT = "/accurate/api/sales-invoice/print.do"
PATH_SR_LIST = "/accurate/api/sales-receipt/list.do"
PATH_SR_DETAIL = "/accurate/api/sales-receipt/detail.do"

_MAX_ATTEMPTS = 3
_RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
# Bound Accurate print temp PDF (LIVE sample ~0.6MB; keep headroom).
MAX_SALES_INVOICE_PDF_BYTES = 15 * 1024 * 1024


class _PdfRetryable(Exception):
    """Internal: retry PDF stream attempt (401/5xx soft)."""

    def __init__(self, message: str, *, clear_session: bool = False) -> None:
        super().__init__(message)
        self.clear_session = clear_session


class AccurateReadStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"
    CONFIG_PRESENT = "config_present"
    READY = "ready"


class AccurateReadClient:
    """Client sync read-only Accurate Online."""

    def __init__(
        self,
        account_base_url: str = "",
        api_token: str = "",
        signature_secret: str = "",
        db_id: str = "",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        *,
        require_db_pin: bool = False,
        base_url: str | None = None,
    ) -> None:
        # base_url = alias account base (kompatibilitas pemanggilan lama).
        self.account_base_url = (account_base_url or base_url or "").strip()
        self.api_token = (api_token or "").strip()
        self.signature_secret = (signature_secret or "").strip()
        self.db_id = (db_id or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.transport = transport
        self.require_db_pin = bool(require_db_pin)
        self._cached_host: str | None = None
        self._cached_db_session: str = ""
        self._cached_database_id: str | None = None

    @property
    def base_url(self) -> str:
        """Account base URL (bukan host Sales Order)."""
        return self.account_base_url

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> AccurateReadClient:
        cfg = settings or get_settings()
        return cls(
            account_base_url=(cfg.accurate_base_url or "").strip(),
            api_token=(cfg.accurate_api_token or "").strip(),
            signature_secret=(cfg.accurate_api_signature_secret or "").strip(),
            db_id=(cfg.accurate_db_id or "").strip(),
            timeout_seconds=float(cfg.accurate_http_timeout_seconds),
            require_db_pin=bool(cfg.accurate_db_pin_required),
        )

    @classmethod
    def from_env(cls) -> AccurateReadClient:
        return cls.from_settings()

    @property
    def status(self) -> AccurateReadStatus:
        # Credential wajib: account base + token + signature secret.
        # Pin numerik wajib bila require_db_pin (default produksi).
        creds_ok = bool(
            self.account_base_url and self.api_token and self.signature_secret
        )
        if creds_ok and (self.db_id or not self.require_db_pin):
            return AccurateReadStatus.READY
        if self.account_base_url or self.api_token or self.signature_secret or self.db_id:
            return AccurateReadStatus.CONFIG_PRESENT
        return AccurateReadStatus.NOT_CONFIGURED

    def _http(self, *, follow_redirects: bool = False) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout_seconds,
            "follow_redirects": follow_redirects,
        }
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.Client(**kwargs)

    def _signed_request_headers(self, session: str | None = None) -> dict[str, str]:
        if not self.api_token or not self.signature_secret:
            raise AccurateAuthError(
                "ACCURATE_API_TOKEN dan ACCURATE_API_SIGNATURE_SECRET wajib.",
                code="accurate_credentials_missing",
            )
        sid = self._cached_db_session if session is None else session
        return request_headers(
            api_token=self.api_token,
            signature_secret=self.signature_secret,
            session=sid,
        )

    def clear_session(self) -> None:
        self._cached_host = None
        self._cached_db_session = ""
        self._cached_database_id = None

    def open_db(self, client: httpx.Client | None = None) -> DbSession:
        """Resolve (host, session) via POST account /api/api-token.do.

        Session boleh string kosong (jalur API Token terbukti tidak memakai X-Session-ID).
        """
        if self.require_db_pin and not self.db_id:
            raise AccurateAuthError(
                "V4_ACCURATE_DB_ID (pin numerik d.database.id) wajib "
                "saat ACCURATE_REQUIRE_DB_PIN / APP_ENV=production.",
                code="accurate_database_pin_required",
            )
        if self.status != AccurateReadStatus.READY:
            raise AccurateAuthError(
                "Accurate api_token belum lengkap "
                "(ACCURATE_BASE_URL / ACCURATE_ACCOUNT_BASE, ACCURATE_API_TOKEN, "
                "ACCURATE_API_SIGNATURE_SECRET"
                + (", V4_ACCURATE_DB_ID" if self.require_db_pin else "")
                + ").",
                code="accurate_credentials_missing",
            )
        own = client is None
        http = client or self._http()
        try:
            account = self.account_base_url.rstrip("/")
            url = f"{account}{PATH_API_TOKEN}"
            try:
                resp = http.post(url, headers=self._signed_request_headers(session=""))
            except httpx.TimeoutException as exc:
                raise TimeoutError("Timeout Accurate api-token.do") from exc
            except httpx.HTTPError as exc:
                raise ConnectionError(
                    f"api-token.do unreachable: {exc.__class__.__name__}"
                ) from exc

            # Manual redirect + re-sign
            if resp.status_code in {301, 302, 307, 308}:
                location = resp.headers.get("Location") or resp.headers.get("location")
                if not location:
                    raise AccurateAuthError(
                        f"api-token.do HTTP {resp.status_code} tanpa Location",
                        code="accurate_api_token_redirect_missing",
                    )
                next_url = urljoin(url, location)
                try:
                    resp = http.post(
                        next_url, headers=self._signed_request_headers(session="")
                    )
                except httpx.TimeoutException as exc:
                    raise TimeoutError("Timeout Accurate api-token.do redirect") from exc
                except httpx.HTTPError as exc:
                    raise ConnectionError(
                        f"api-token.do redirect unreachable: {exc.__class__.__name__}"
                    ) from exc
                parsed = urlparse(next_url)
                if parsed.netloc and "account.accurate.id" not in parsed.netloc:
                    self._cached_host = normalize_host(
                        f"{parsed.scheme}://{parsed.netloc}"
                    )

            if resp.status_code == 401:
                raise AccurateAuthError(
                    "api-token.do menolak token/signature",
                    code="accurate_auth_rejected",
                )
            if resp.status_code >= 400:
                raise AccurateAuthError(
                    f"api-token.do HTTP {resp.status_code}",
                    code="accurate_api_token_rejected",
                )
            try:
                payload = resp.json()
            except ValueError as exc:
                raise AccurateAuthError(
                    "api-token.do response bukan JSON",
                    code="accurate_api_token_invalid",
                ) from exc
            if not isinstance(payload, dict):
                raise AccurateAuthError(
                    "api-token.do shape tidak dikenali",
                    code="accurate_api_token_shape",
                )
            if payload.get("s") is False or payload.get("success") is False:
                raise AccurateAuthError(
                    "api-token.do menolak kredensial (s/success=false).",
                    code="accurate_api_token_business_reject",
                )

            host, resolved_id = extract_database(payload)
            if self._cached_host and not host:
                host = self._cached_host
            if not host:
                raise AccurateAuthError(
                    "api-token.do tidak mengembalikan database.host",
                    code="accurate_host_missing",
                )
            session = extract_optional_session(payload)  # biasanya ""

            expected = self.db_id
            if expected:
                candidates = collect_database_id_candidates(payload)
                pin_match = match_database_pin(expected, candidates)
                if not pin_match.ok:
                    messages = {
                        "accurate_database_id_missing": (
                            "api-token.do tidak mengembalikan database.id — pin gagal"
                        ),
                        "accurate_database_id_kind_mismatch": (
                            "Jenis ACCURATE_DB_ID tidak cocok dengan identitas "
                            "database dari api-token.do (pin UUID vs id numerik, "
                            "atau sebaliknya) — pin tetap diwajibkan"
                        ),
                        "accurate_database_mismatch": (
                            "Database id dari api-token.do tidak cocok dengan konfigurasi"
                        ),
                    }
                    raise AccurateAuthError(
                        messages.get(pin_match.code, "Database pin gagal"),
                        code=pin_match.code,
                        diagnostics=pin_match.diagnostics,
                    )
                # Prefer field yang cocok sebagai resolved id (nilai dinormalisasi).
                if pin_match.matched_field:
                    for cand in candidates:
                        if cand.field == pin_match.matched_field:
                            resolved_id = cand.normalized
                            break

            return DbSession(host=host, session=session, database_id=resolved_id)
        finally:
            if own:
                http.close()

    def ensure_db_session(self, client: httpx.Client | None = None) -> DbSession:
        """Cache _cached_host + _cached_db_session (session boleh kosong)."""
        if self._cached_host:
            return DbSession(
                host=self._cached_host,
                session=self._cached_db_session,
                database_id=self._cached_database_id,
            )
        opened = self.open_db(client)
        self._cached_host = opened.host
        self._cached_db_session = opened.session or ""
        self._cached_database_id = opened.database_id
        return opened

    # Alias nama lama
    def ensure_session(self, client: httpx.Client | None = None) -> DbSession:
        return self.ensure_db_session(client)

    def _unwrap_detail(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Buka bungkus Accurate ``d`` / ``deliveryOrder`` / ``salesOrder`` objek.

        Header DO live bisa punya ``salesOrder: false`` (boolean) — hanya
        objek dict yang dipertimbangkan sebagai kandidat detail.
        """
        current: dict[str, Any] = payload
        for _ in range(4):
            if "detailItem" in current or (
                "id" in current and "number" in current and "s" not in current
            ):
                return current
            next_inner: dict[str, Any] | None = None
            for key in (
                "d",
                "data",
                "deliveryOrder",
                "salesOrder",
                "salesInvoice",
                "salesReceipt",
            ):
                inner = current.get(key)
                if isinstance(inner, dict) and (
                    "detailItem" in inner
                    or "detailInvoice" in inner
                    or "id" in inner
                    or "number" in inner
                    or "deliveryOrder" in inner
                    or "d" in inner
                ):
                    next_inner = inner
                    break
            if next_inner is None:
                return current
            current = next_inner
        return current

    @staticmethod
    def _unwrap_list_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = payload.get("d") or payload.get("data") or payload.get("list") or []
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("list") or []
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, dict)]

    def _request_on_db_host(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        with self._http(follow_redirects=False) as http:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    db = self.ensure_db_session(http)
                    url = f"{db.host.rstrip('/')}{path}"
                    resp = http.request(
                        method,
                        url,
                        params=params,
                        headers=self._signed_request_headers(),
                    )
                except AccurateAuthError:
                    raise
                except httpx.TimeoutException as exc:
                    raise TimeoutError(f"Timeout Accurate {path}") from exc
                except httpx.HTTPError as exc:
                    last_err = ConnectionError(
                        f"Koneksi Accurate gagal: {exc.__class__.__name__}"
                    )
                    continue

                if resp.status_code in {301, 302, 307, 308}:
                    location = resp.headers.get("Location") or resp.headers.get(
                        "location"
                    )
                    if not location:
                        raise AccurateAuthError(
                            f"HTTP {resp.status_code} tanpa Location",
                            code="accurate_redirect_missing",
                        )
                    next_url = urljoin(str(resp.request.url), location)
                    parsed = urlparse(next_url)
                    if parsed.scheme and parsed.netloc:
                        self._cached_host = normalize_host(
                            f"{parsed.scheme}://{parsed.netloc}"
                        )
                    retry_url = (
                        next_url
                        if parsed.path and parsed.path != "/"
                        else f"{self._cached_host}{path}"
                    )
                    try:
                        resp = http.request(
                            method,
                            retry_url,
                            params=params,
                            headers=self._signed_request_headers(),
                        )
                    except httpx.TimeoutException as exc:
                        raise TimeoutError(f"Timeout Accurate {path} setelah redirect") from exc
                    except httpx.HTTPError as exc:
                        last_err = ConnectionError(
                            f"Koneksi Accurate gagal: {exc.__class__.__name__}"
                        )
                        continue

                if resp.status_code == 401 and attempt < _MAX_ATTEMPTS:
                    # Sesi/host kedaluwarsa — buka ulang + re-sign
                    self.clear_session()
                    continue

                if resp.status_code in _RETRYABLE and attempt < _MAX_ATTEMPTS:
                    last_err = ConnectionError(
                        f"Accurate {path} error server HTTP {resp.status_code}"
                    )
                    continue

                if resp.status_code >= 500:
                    raise ConnectionError(
                        f"Accurate {path} error server HTTP {resp.status_code}"
                    )
                if resp.status_code >= 400:
                    raise AccurateAuthError(
                        f"Accurate {path} HTTP {resp.status_code}",
                        code="accurate_http_error",
                    )
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise RuntimeError(f"{path} response bukan JSON") from exc
                if not isinstance(data, dict):
                    raise RuntimeError(f"{path} response bukan objek JSON.")
                if data.get("s") is False or data.get("success") is False:
                    raise RuntimeError(f"{path} mengembalikan status gagal.")
                return data

        if last_err:
            raise last_err
        raise ConnectionError(f"Accurate {path} gagal setelah retry")

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        """GET detail.do?id=<accurate_id> pada host database."""
        data = self._request_on_db_host(
            "GET",
            PATH_SO_DETAIL,
            params={"id": str(accurate_id)},
        )
        return self._unwrap_detail(data)

    def fetch_sales_order(self, accurate_id: str | int) -> dict[str, Any]:
        return self.fetch_sales_order_detail(accurate_id)

    def fetch_sales_order_list(
        self,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        data = self._request_on_db_host("GET", PATH_SO_LIST, params=params or {})
        return self._unwrap_list_rows(data)

    def fetch_sales_orders(self) -> list[dict[str, object]]:
        return self.fetch_sales_order_list()  # type: ignore[return-value]

    def fetch_delivery_order_list(
        self,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """GET delivery-order/list.do — read-only Surat Jalan."""
        data = self._request_on_db_host("GET", PATH_DO_LIST, params=params or {})
        return self._unwrap_list_rows(data)

    def fetch_delivery_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        """GET delivery-order/detail.do?id= — read-only Surat Jalan."""
        data = self._request_on_db_host(
            "GET",
            PATH_DO_DETAIL,
            params={"id": str(accurate_id)},
        )
        return self._unwrap_detail(data)

    def fetch_sales_invoice_list(
        self,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """GET sales-invoice/list.do — read-only Faktur Penjualan."""
        data = self._request_on_db_host("GET", PATH_SI_LIST, params=params or {})
        return self._unwrap_list_rows(data)

    def fetch_sales_invoice_detail(self, accurate_id: str | int) -> dict[str, Any]:
        """GET sales-invoice/detail.do?id= — read-only Faktur Penjualan."""
        data = self._request_on_db_host(
            "GET",
            PATH_SI_DETAIL,
            params={"id": str(accurate_id)},
        )
        return self._unwrap_detail(data)

    @staticmethod
    def _hostname_only(normalized_host: str) -> str:
        parsed = urlparse(normalized_host)
        netloc = (parsed.netloc or "").lower()
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[-1]
        return netloc.split(":")[0]

    @classmethod
    def _is_accurate_cloud_host(cls, normalized_host: str) -> bool:
        host = cls._hostname_only(normalized_host)
        return host == "accurate.id" or host.endswith(".accurate.id")

    def _assert_trusted_pdf_destination(
        self,
        *,
        from_host: str,
        destination_url: str,
        required_path_prefix: str = "/accurate/files/",
    ) -> str:
        """Validasi redirect/absolute URL PDF sebelum mengirim header signed.

        Fail-closed: jangan teruskan Authorization/signature ke origin asing.
        Host rotation Accurate (*.accurate.id) diizinkan hanya bila kedua sisi
        adalah host cloud Accurate yang dikenal.
        """
        parsed = urlparse(destination_url)
        if parsed.scheme not in {"https", "http"}:
            raise AccurateAuthError(
                "Redirect PDF Accurate menolak skema tidak aman.",
                code="accurate_pdf_redirect_untrusted",
            )
        if parsed.username or parsed.password:
            raise AccurateAuthError(
                "Redirect PDF Accurate menolak kredensial di URL.",
                code="accurate_pdf_redirect_untrusted",
            )
        if not parsed.netloc:
            raise AccurateAuthError(
                "Redirect PDF Accurate tanpa host.",
                code="accurate_pdf_redirect_untrusted",
            )
        to_host = normalize_host(f"{parsed.scheme}://{parsed.netloc}")
        from_norm = normalize_host(from_host)
        if to_host != from_norm:
            if not (
                self._is_accurate_cloud_host(from_norm)
                and self._is_accurate_cloud_host(to_host)
            ):
                raise AccurateAuthError(
                    "Redirect PDF Accurate ke host tidak terpercaya.",
                    code="accurate_pdf_redirect_untrusted",
                )
        path = parsed.path or ""
        if not path.startswith(required_path_prefix):
            raise AccurateAuthError(
                "Redirect PDF Accurate di luar /accurate/files/.",
                code="accurate_pdf_redirect_untrusted",
            )
        if ".." in path or "\\" in path or parsed.query or parsed.fragment:
            raise AccurateAuthError(
                "Path PDF Accurate tidak valid.",
                code="accurate_pdf_redirect_untrusted",
            )
        return f"{to_host}{path}"

    def fetch_sales_invoice_pdf(
        self,
        accurate_id: str | int,
        *,
        max_bytes: int = MAX_SALES_INVOICE_PDF_BYTES,
    ) -> bytes:
        """Ambil PDF Faktur Penjualan resmi Accurate (dua langkah, GET).

        1) ``GET /accurate/api/sales-invoice/print.do?id=`` → JSON
           ``{s:true,d:{output:"/accurate/files/_temp/….pdf"}}``
        2) ``GET`` path ``output`` dengan header signed → ``application/pdf``.

        Membuat file temp di Accurate (print generation) — bukan mutasi dokumen
        faktur. Tidak menyimpan PDF ke Mongo. Fail-closed bila bukan PDF valid.
        """
        bound = max(1024, min(int(max_bytes), MAX_SALES_INVOICE_PDF_BYTES))
        print_payload = self._request_on_db_host(
            "GET",
            PATH_SI_PRINT,
            params={"id": str(accurate_id)},
        )
        output: str | None = None
        inner = print_payload.get("d")
        if isinstance(inner, dict):
            raw_out = inner.get("output") or inner.get("file") or inner.get("url")
            if isinstance(raw_out, str) and raw_out.strip():
                output = raw_out.strip()
        elif isinstance(inner, str) and inner.strip():
            output = inner.strip()
        if not output:
            raise RuntimeError("sales-invoice/print.do tidak mengembalikan path PDF.")

        db = self.ensure_db_session()
        if output.startswith("http://") or output.startswith("https://"):
            # Absolute URL: jangan percaya path saja — wajib host DB terpercaya.
            file_url = self._assert_trusted_pdf_destination(
                from_host=db.host,
                destination_url=output,
            )
            parsed = urlparse(file_url)
            file_path = parsed.path
        else:
            file_path = output if output.startswith("/") else f"/{output}"
            if not file_path.startswith("/accurate/files/"):
                raise RuntimeError("Path PDF Accurate di luar /accurate/files/.")
            if ".." in file_path or "\\" in file_path or "?" in file_path or "#" in file_path:
                raise RuntimeError("Path PDF Accurate tidak valid.")

        pdf_bytes = self._request_bytes_on_db_host(
            "GET", file_path, max_bytes=bound
        )
        if not pdf_bytes.startswith(b"%PDF"):
            raise RuntimeError("Respons Accurate bukan PDF valid.")
        return pdf_bytes

    def _request_bytes_on_db_host(
        self,
        method: str,
        path: str,
        *,
        max_bytes: int = MAX_SALES_INVOICE_PDF_BYTES,
    ) -> bytes:
        """GET bytes mentah PDF pada host DB — redirect terpercaya + stream bounded."""
        if not path.startswith("/accurate/files/"):
            raise RuntimeError("Path PDF Accurate di luar /accurate/files/.")
        if ".." in path or "\\" in path:
            raise RuntimeError("Path PDF Accurate tidak valid.")
        bound = max(1024, min(int(max_bytes), MAX_SALES_INVOICE_PDF_BYTES))
        last_err: Exception | None = None
        with self._http(follow_redirects=False) as http:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    db = self.ensure_db_session(http)
                    url = f"{db.host.rstrip('/')}{path}"
                    return self._stream_trusted_pdf(
                        http,
                        method=method,
                        url=url,
                        trusted_from_host=db.host,
                        path=path,
                        max_bytes=bound,
                        attempt=attempt,
                    )
                except AccurateAuthError:
                    raise
                except TimeoutError:
                    raise
                except RuntimeError:
                    raise
                except httpx.TimeoutException as exc:
                    raise TimeoutError(f"Timeout Accurate {path}") from exc
                except httpx.HTTPError as exc:
                    last_err = ConnectionError(
                        f"Koneksi Accurate gagal: {exc.__class__.__name__}"
                    )
                    continue
                except _PdfRetryable as exc:
                    last_err = ConnectionError(str(exc))
                    if attempt >= _MAX_ATTEMPTS:
                        break
                    if exc.clear_session:
                        self.clear_session()
                    continue

        if last_err:
            raise last_err
        raise ConnectionError(f"Accurate {path} gagal setelah retry")

    def _stream_trusted_pdf(
        self,
        http: httpx.Client,
        *,
        method: str,
        url: str,
        trusted_from_host: str,
        path: str,
        max_bytes: int,
        attempt: int,
        _redirects: int = 0,
    ) -> bytes:
        """Stream PDF dengan batas ukuran; redirect hanya ke host terpercaya."""
        if _redirects > 3:
            raise AccurateAuthError(
                "Terlalu banyak redirect PDF Accurate.",
                code="accurate_pdf_redirect_untrusted",
            )
        try:
            with http.stream(
                method,
                url,
                headers=self._signed_request_headers(),
            ) as resp:
                if resp.status_code in {301, 302, 307, 308}:
                    location = resp.headers.get("Location") or resp.headers.get(
                        "location"
                    )
                    if not location:
                        raise AccurateAuthError(
                            f"HTTP {resp.status_code} tanpa Location",
                            code="accurate_redirect_missing",
                        )
                    # Baca/abaikan body redirect tanpa menyimpan.
                    try:
                        resp.read()
                    except Exception:
                        pass
                    next_url = urljoin(str(resp.request.url), location)
                    trusted_url = self._assert_trusted_pdf_destination(
                        from_host=trusted_from_host,
                        destination_url=next_url,
                    )
                    # Host rotation Accurate: update cache hanya setelah trust check.
                    parsed = urlparse(trusted_url)
                    self._cached_host = normalize_host(
                        f"{parsed.scheme}://{parsed.netloc}"
                    )
                    return self._stream_trusted_pdf(
                        http,
                        method=method,
                        url=trusted_url,
                        trusted_from_host=self._cached_host,
                        path=path,
                        max_bytes=max_bytes,
                        attempt=attempt,
                        _redirects=_redirects + 1,
                    )

                if resp.status_code == 401 and attempt < _MAX_ATTEMPTS:
                    raise _PdfRetryable(
                        f"Accurate {path} HTTP 401", clear_session=True
                    )
                if resp.status_code in _RETRYABLE and attempt < _MAX_ATTEMPTS:
                    raise _PdfRetryable(
                        f"Accurate {path} error server HTTP {resp.status_code}",
                        clear_session=False,
                    )
                if resp.status_code >= 500:
                    raise ConnectionError(
                        f"Accurate {path} error server HTTP {resp.status_code}"
                    )
                if resp.status_code >= 400:
                    raise AccurateAuthError(
                        f"Accurate {path} HTTP {resp.status_code}",
                        code="accurate_http_error",
                    )

                cl_raw = resp.headers.get("content-length")
                if cl_raw is not None and str(cl_raw).strip() != "":
                    try:
                        content_length = int(str(cl_raw).strip())
                    except ValueError as exc:
                        raise RuntimeError(
                            "Content-Length PDF Accurate tidak valid."
                        ) from exc
                    if content_length < 0 or content_length > max_bytes:
                        raise RuntimeError("PDF Accurate melebihi batas ukuran.")

                ctype = (resp.headers.get("content-type") or "").lower()
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    if len(buf) + len(chunk) > max_bytes:
                        raise RuntimeError("PDF Accurate melebihi batas ukuran.")
                    buf.extend(chunk)

                body = bytes(buf)
                if "application/pdf" not in ctype and not body.startswith(b"%PDF"):
                    raise RuntimeError(
                        f"{path} content-type bukan PDF ({ctype or 'missing'})."
                    )
                return body
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"Timeout Accurate {path}") from exc

    def fetch_sales_receipt_list(
        self,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """GET sales-receipt/list.do — read-only Penerimaan Penjualan."""
        data = self._request_on_db_host("GET", PATH_SR_LIST, params=params or {})
        return self._unwrap_list_rows(data)

    def fetch_sales_receipt_detail(self, accurate_id: str | int) -> dict[str, Any]:
        """GET sales-receipt/detail.do?id= — read-only Penerimaan Penjualan."""
        data = self._request_on_db_host(
            "GET",
            PATH_SR_DETAIL,
            params={"id": str(accurate_id)},
        )
        return self._unwrap_detail(data)
