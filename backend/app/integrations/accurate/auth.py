"""Accurate Online API Token 1.0.3 — kontrak terbukti (eternal accurate-gateway).

Setiap request:
  Authorization: Bearer <API_TOKEN>
  X-Api-Timestamp: dd/MM/yyyy HH:mm:ss  (Asia/Jakarta)
  X-Api-Signature: Base64(HMAC-SHA256(signature_secret, timestamp))

X-Session-ID hanya ditambahkan bila session non-kosong (jalur kosong = tanpa header).
Secret / token tidak pernah di-log.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_WIB = ZoneInfo("Asia/Jakarta")


class AccurateAuthError(RuntimeError):
    """Gagal autentikasi / resolusi host Accurate."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "accurate_auth_error",
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        # Hanya metadata aman (field/type/length/match) — tanpa nilai ID.
        self.diagnostics = list(diagnostics or [])


def api_timestamp(now: datetime | None = None) -> str:
    """Format wajib Accurate Online API Token 1.0.3."""
    dt = now or datetime.now(_WIB)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_WIB)
    else:
        dt = dt.astimezone(_WIB)
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def api_signature(signature_secret: str, timestamp: str) -> str:
    """HMAC-SHA256(secret as key, timestamp as message) → Base64."""
    digest = hmac.new(
        signature_secret.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def build_api_signature(*, timestamp: str, signature_secret: str) -> str:
    """Alias eksplisit keyword — sama dengan api_signature."""
    return api_signature(signature_secret, timestamp)


def signed_headers(
    *,
    api_token: str,
    signature_secret: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Header tanda tangan segar (_signed_headers legacy / gateway)."""
    ts = api_timestamp(now)
    return {
        "Authorization": f"Bearer {api_token}",
        "X-Api-Timestamp": ts,
        "X-Api-Signature": api_signature(signature_secret, ts),
        "Accept": "application/json",
    }


def build_auth_headers(
    *,
    api_token: str,
    signature_secret: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Alias publik — Authorization Bearer + timestamp WIB + signature."""
    return signed_headers(
        api_token=api_token,
        signature_secret=signature_secret,
        now=now,
    )


def request_headers(
    *,
    api_token: str,
    signature_secret: str,
    session: str = "",
    now: datetime | None = None,
) -> dict[str, str]:
    """_request_headers: signed + X-Session-ID hanya jika session non-kosong."""
    headers = signed_headers(
        api_token=api_token,
        signature_secret=signature_secret,
        now=now,
    )
    sid = (session or "").strip()
    if sid:
        headers["X-Session-ID"] = sid
    return headers


def normalize_host(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    host = host.removesuffix("/accurate").rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        if "." not in host:
            host = f"https://{host}.accurate.id"
        else:
            host = f"https://{host}"
    return host.rstrip("/")


@dataclass
class DbSession:
    """Hasil open_db / ensure_db_session: host database + session (boleh kosong)."""

    host: str
    session: str = ""
    database_id: str | None = None


# UUID 8-4-4-4-12 (36 karakter dengan hyphen) — sesi/token Accurate sering bentuk ini;
# database.id dari api-token.do / open-db.do terbukti numerik (gateway + docs Accurate).
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def classify_database_id(value: Any) -> str:
    """Klasifikasi jenis identifier: numeric | uuid | other."""
    if value is None or isinstance(value, bool):
        return "other"
    if isinstance(value, int):
        return "numeric"
    if isinstance(value, float) and value.is_integer():
        return "numeric"
    text = str(value).strip()
    if not text:
        return "other"
    if text.isdigit() or (
        text.startswith("-") and text[1:].isdigit()
    ):
        return "numeric"
    # Numeric string dengan .0 dari serialisasi long
    if text.replace(".", "", 1).isdigit() and text.count(".") == 1:
        whole, frac = text.split(".", 1)
        if whole.isdigit() and set(frac) <= {"0"}:
            return "numeric"
    if _UUID_RE.match(text):
        return "uuid"
    return "other"


def _python_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def _safe_id_length(value: Any) -> int:
    if value is None:
        return 0
    return len(str(value).strip())


def normalize_id_for_compare(value: Any, *, kind: str | None = None) -> str:
    """Normalisasi perbandingan tanpa mengubah makna identitas."""
    kind = kind or classify_database_id(value)
    text = str(value).strip()
    if kind == "numeric":
        if isinstance(value, bool):
            return text
        if isinstance(value, (int, float)):
            return str(int(value))
        if text.replace(".", "", 1).isdigit() and text.count(".") <= 1:
            return str(int(float(text)))
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return str(int(text))
        return text
    if kind == "uuid":
        return text.lower()
    return text


@dataclass(frozen=True)
class DatabaseIdCandidate:
    """Kandidat identitas database dari respons — nilai disimpan internal saja."""

    field: str
    raw: Any

    @property
    def kind(self) -> str:
        return classify_database_id(self.raw)

    @property
    def python_type(self) -> str:
        return _python_type_name(self.raw)

    @property
    def length(self) -> int:
        return _safe_id_length(self.raw)

    @property
    def normalized(self) -> str:
        return normalize_id_for_compare(self.raw, kind=self.kind)

    def safe_diag(self, *, matched: bool | None = None) -> dict[str, Any]:
        row: dict[str, Any] = {
            "field": self.field,
            "type": self.python_type,
            "kind": self.kind,
            "length": self.length,
        }
        if matched is not None:
            row["match"] = matched
        return row


def collect_database_id_candidates(payload: dict[str, Any]) -> list[DatabaseIdCandidate]:
    """Kumpulkan kandidat ID dari bentuk respons yang dikenal (tanpa menebak DB)."""
    data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
    d_database = data.get("database") if isinstance(data.get("database"), dict) else {}
    root_database = (
        payload.get("database") if isinstance(payload.get("database"), dict) else {}
    )

    ordered: list[tuple[str, Any]] = [
        ("d.database.id", d_database.get("id") if d_database else None),
        ("d.database.databaseId", d_database.get("databaseId") if d_database else None),
        ("d.database.guid", d_database.get("guid") if d_database else None),
        ("d.database.uuid", d_database.get("uuid") if d_database else None),
        ("d.databaseId", data.get("databaseId") if data else None),
        ("databaseId", payload.get("databaseId")),
        ("database.id", root_database.get("id") if root_database else None),
    ]
    out: list[DatabaseIdCandidate] = []
    seen_fields: set[str] = set()
    for field, raw in ordered:
        if raw is None or isinstance(raw, bool):
            continue
        if isinstance(raw, str) and not raw.strip():
            continue
        if field in seen_fields:
            continue
        seen_fields.add(field)
        out.append(DatabaseIdCandidate(field=field, raw=raw))
    return out


def extract_database(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Ambil (host, database_id primer) — primer = d.database.id bila ada (kontrak gateway)."""
    data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
    database = data.get("database") if isinstance(data.get("database"), dict) else {}
    if not database and isinstance(payload.get("database"), dict):
        database = payload["database"]
    host = database.get("host") or data.get("host") or payload.get("host")
    if isinstance(host, bool):
        host = None
    host_n = normalize_host(str(host)) if host else None

    candidates = collect_database_id_candidates(payload)
    # Primer untuk pin API Token: utamakan field numerik d.database.id (bukti gateway).
    primary: str | None = None
    for cand in candidates:
        if cand.field == "d.database.id":
            primary = cand.normalized
            break
    if primary is None and candidates:
        # Fallback urutan collect (databaseId, dll.) — tetap fail-closed saat pin.
        primary = candidates[0].normalized
    return host_n, primary


@dataclass
class DatabasePinMatch:
    ok: bool
    code: str
    matched_field: str | None
    pin_kind: str
    pin_length: int
    diagnostics: list[dict[str, Any]]


def match_database_pin(
    expected: str,
    candidates: list[DatabaseIdCandidate],
) -> DatabasePinMatch:
    """Bandingkan pin env hanya dengan kandidat sejenis (numeric↔numeric, uuid↔uuid).

    Bukti:
    - Gateway / docs: pin vs ``d.database.id`` numerik.
    - ACCURATE_DB_ID 36-char di VPS = bentuk UUID — jenis berbeda dari database.id numerik.
    """
    pin = (expected or "").strip()
    pin_kind = classify_database_id(pin)
    pin_len = len(pin)
    pin_norm = normalize_id_for_compare(pin, kind=pin_kind)

    if not candidates:
        return DatabasePinMatch(
            ok=False,
            code="accurate_database_id_missing",
            matched_field=None,
            pin_kind=pin_kind,
            pin_length=pin_len,
            diagnostics=[
                {
                    "field": "pin",
                    "type": "str",
                    "kind": pin_kind,
                    "length": pin_len,
                    "match": False,
                }
            ],
        )

    same_kind = [c for c in candidates if c.kind == pin_kind]
    diagnostics: list[dict[str, Any]] = [
        {
            "field": "pin",
            "type": "str",
            "kind": pin_kind,
            "length": pin_len,
            "match": False,
        }
    ]

    if not same_kind:
        # Jangan membandingkan UUID pin ke numeric id (akar mismatch live VPS).
        for c in candidates:
            diagnostics.append(c.safe_diag(matched=False))
        return DatabasePinMatch(
            ok=False,
            code="accurate_database_id_kind_mismatch",
            matched_field=None,
            pin_kind=pin_kind,
            pin_length=pin_len,
            diagnostics=diagnostics,
        )

    for c in candidates:
        is_match = c.kind == pin_kind and c.normalized == pin_norm
        diagnostics.append(c.safe_diag(matched=is_match))
        if is_match:
            diagnostics[0]["match"] = True
            return DatabasePinMatch(
                ok=True,
                code="ok",
                matched_field=c.field,
                pin_kind=pin_kind,
                pin_length=pin_len,
                diagnostics=diagnostics,
            )

    return DatabasePinMatch(
        ok=False,
        code="accurate_database_mismatch",
        matched_field=None,
        pin_kind=pin_kind,
        pin_length=pin_len,
        diagnostics=diagnostics,
    )


def extract_optional_session(payload: dict[str, Any]) -> str:
    """Session opsional — string kosong jika tidak ada (jalur API Token terbukti)."""
    for key in ("session", "sessionId", "SID"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    data = payload.get("d")
    if isinstance(data, dict):
        for key in ("session", "sessionId", "SID"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""
