"""Global Accurate auto-sync date floor (transaksi, Asia/Jakarta).

Kontrak live GET 2026-11-20 (VPS read-only):
- ``filter.transDate.op=BETWEEN``
- ``filter.transDate.val[0]=dd/MM/yyyy`` (inklusif)
- ``filter.transDate.val[1]=31/12/2099`` (tanpa end-date tetap)
- ``GREATER_EQUAL`` + ``val`` tunggal → Accurate ``s:false`` (UNSUPPORTED)
- Kompatibel ``sp.page`` / ``sp.pageSize`` / ``id|asc`` / ``lastUpdate|desc`` / ``fields``

Filter list mengurangi histori; backend tetap memvalidasi ``transDate`` detail.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from app.integrations.accurate.fields import as_text

JAKARTA = ZoneInfo("Asia/Jakarta")

# Upper bound open-ended — Accurate BETWEEN membutuhkan val[1].
ACCURATE_DATE_FLOOR_UPPER = "31/12/2099"

DEFAULT_ACCURATE_SYNC_START_DATE = date(2026, 11, 1)

# Tepat YYYY-MM-DD — tolak suffix, waktu, slash, dan format lain.
_ISO_DATE_STRICT = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_iso_date_floor(raw: str | date | None) -> date:
    """Parse ketat ``YYYY-MM-DD`` (atau ``date``). Default Owner: 2026-11-01.

    Menolak karakter tambahan, datetime ISO, format dd/MM/yyyy, dan tanggal
    kalender tidak valid (mis. 2026-02-29).
    """
    if isinstance(raw, datetime):
        raise ValueError(
            "ACCURATE_SYNC_START_DATE menolak datetime; gunakan date atau YYYY-MM-DD."
        )
    if isinstance(raw, date):
        return raw
    text = (str(raw).strip() if raw is not None else "") or ""
    if not text:
        return DEFAULT_ACCURATE_SYNC_START_DATE
    if _ISO_DATE_STRICT.match(text) is None:
        raise ValueError(
            f"ACCURATE_SYNC_START_DATE tidak valid ({text!r}); "
            "gunakan tepat YYYY-MM-DD."
        )
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"ACCURATE_SYNC_START_DATE bukan tanggal kalender valid ({text!r})."
        ) from exc


def format_accurate_date(d: date) -> str:
    """Format tanggal transaksi Accurate: dd/MM/yyyy."""
    return d.strftime("%d/%m/%Y")


def parse_accurate_trans_date(value: Any) -> date | None:
    """Parse transDate Accurate (umumnya dd/MM/yyyy). None jika kosong/invalid."""
    text = as_text(value).strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def accurate_trans_date_filter_params(start: date) -> dict[str, str]:
    """Parameter list Accurate yang terbukti hidup (BETWEEN)."""
    return {
        "filter.transDate.op": "BETWEEN",
        "filter.transDate.val[0]": format_accurate_date(start),
        "filter.transDate.val[1]": ACCURATE_DATE_FLOOR_UPPER,
    }


def date_floor_scope_id(start: date) -> str:
    """Identitas scope checkpoint — berubah jika Owner mengubah floor."""
    return f"transDate>={start.isoformat()}@Asia/Jakarta"


def classify_trans_date(
    value: Any, *, start: date
) -> tuple[str, date | None]:
    """Klasifikasi tanggal transaksi vs date floor.

    Returns ``(status, parsed)``:
    - ``in_scope`` — tanggal >= start
    - ``out_of_scope`` — tanggal < start
    - ``invalid`` — tidak dapat diparse / kosong
    """
    parsed = parse_accurate_trans_date(value)
    if parsed is None:
        return "invalid", None
    if parsed < start:
        return "out_of_scope", parsed
    return "in_scope", parsed


@lru_cache(maxsize=1)
def configured_date_floor() -> date:
    """Baca floor dari settings (cache dibersihkan bersama settings cache)."""
    from app.config import get_settings

    return parse_iso_date_floor(get_settings().accurate_sync_start_date)


def clear_date_floor_cache() -> None:
    configured_date_floor.cache_clear()


def payload_trans_date(payload: dict[str, Any]) -> Any:
    """Field tanggal transaksi dokumen Accurate (bukan lastUpdate)."""
    if payload.get("transDate") not in (None, ""):
        return payload.get("transDate")
    # Kontrak list memakai transDate; jangan mengganti dengan lastUpdate/ID.
    return payload.get("transDate")


def detail_scope_outcome(
    payload: dict[str, Any],
    *,
    accurate_id: str,
    start: date | None = None,
) -> dict[str, Any] | None:
    """Validasi detail vs date floor. None = lanjut proses; dict = early outcome.

    ``excluded`` — sebelum floor (zero write).
    ``failed`` — transDate invalid/missing (fail-closed, bukan menebak).
    """
    floor = start or configured_date_floor()
    status, parsed = classify_trans_date(payload_trans_date(payload), start=floor)
    if status == "in_scope":
        return None
    if status == "out_of_scope":
        return {
            "outcome": "excluded",
            "reason": "before_date_floor",
            "accurate_id": str(accurate_id),
            "trans_date": parsed.isoformat() if parsed else None,
            "date_floor": floor.isoformat(),
            "writes": 0,
        }
    return {
        "outcome": "failed",
        "reason": "invalid_trans_date",
        "accurate_id": str(accurate_id),
        "date_floor": floor.isoformat(),
        "writes": 0,
        "code": "invalid_trans_date",
    }
