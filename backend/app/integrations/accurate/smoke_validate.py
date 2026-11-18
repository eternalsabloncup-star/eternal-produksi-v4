"""Validasi hasil smoke read Accurate — tanpa Mongo, tanpa cetak data sensitif."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SmokeExpectations:
    accurate_id: str = "95106"
    po_number: str = "PO/18/11/2026/000010"
    detail_count: int = 2
    process_raws: tuple[str, ...] = ("Cetak", "Tidak Cetak")


@dataclass
class SmokeCheckResult:
    ok: bool
    auth_ok: bool
    accurate_id_match: bool
    po_number_match: bool
    detail_count_match: bool
    process_mapping_ok: bool
    observed_accurate_id: str = ""
    observed_po_number: str = ""
    observed_detail_count: int = 0
    observed_process_raws: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_smoke_fields(payload: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Ambil id, number, dan charField1 per baris — tanpa field pelanggan."""
    accurate_id = _as_str(payload.get("id", payload.get("accurate_id")))
    po_number = _as_str(
        payload.get("number") or payload.get("poNo") or payload.get("po_no")
    )
    details = payload.get("detailItem") or payload.get("detailItems") or []
    processes: list[str] = []
    if isinstance(details, list):
        for row in details:
            if isinstance(row, dict):
                processes.append(_as_str(row.get("charField1")))
    return accurate_id, po_number, processes


def validate_sales_order_smoke(
    payload: dict[str, Any] | None,
    *,
    expectations: SmokeExpectations | None = None,
    auth_ok: bool = True,
) -> SmokeCheckResult:
    """Bandingkan payload detail SO dengan ekspektasi smoke (read-only)."""
    exp = expectations or SmokeExpectations()
    errors: list[str] = []

    if not auth_ok:
        return SmokeCheckResult(
            ok=False,
            auth_ok=False,
            accurate_id_match=False,
            po_number_match=False,
            detail_count_match=False,
            process_mapping_ok=False,
            errors=["autentikasi gagal"],
        )

    if not isinstance(payload, dict):
        return SmokeCheckResult(
            ok=False,
            auth_ok=True,
            accurate_id_match=False,
            po_number_match=False,
            detail_count_match=False,
            process_mapping_ok=False,
            errors=["payload detail kosong atau bukan objek"],
        )

    accurate_id, po_number, processes = extract_smoke_fields(payload)
    id_match = accurate_id == exp.accurate_id
    po_match = po_number == exp.po_number
    count_match = len(processes) == exp.detail_count
    # Urutan proses mengikuti detailItem; harus memuat mapping Cetak / Tidak Cetak.
    process_ok = list(processes) == list(exp.process_raws)

    if not id_match:
        errors.append(
            f"accurate_id tidak cocok (expected={exp.accurate_id}, got={accurate_id or '-'})"
        )
    if not po_match:
        errors.append(
            f"po_number tidak cocok (expected={exp.po_number}, got={po_number or '-'})"
        )
    if not count_match:
        errors.append(
            f"detail_count tidak cocok (expected={exp.detail_count}, got={len(processes)})"
        )
    if not process_ok:
        errors.append(
            "mapping proses charField1 tidak cocok "
            f"(expected={list(exp.process_raws)}, got={processes})"
        )

    ok = id_match and po_match and count_match and process_ok
    return SmokeCheckResult(
        ok=ok,
        auth_ok=True,
        accurate_id_match=id_match,
        po_number_match=po_match,
        detail_count_match=count_match,
        process_mapping_ok=process_ok,
        observed_accurate_id=accurate_id,
        observed_po_number=po_number,
        observed_detail_count=len(processes),
        observed_process_raws=processes,
        errors=errors,
    )


def format_smoke_report(result: SmokeCheckResult) -> str:
    """Laporan aman: tanpa token, secret, session, host, atau data pelanggan."""
    lines = [
        f"auth: {'ok' if result.auth_ok else 'FAIL'}",
        f"accurate_id_match: {'ok' if result.accurate_id_match else 'FAIL'}"
        + (f" ({result.observed_accurate_id})" if result.observed_accurate_id else ""),
        f"po_number_match: {'ok' if result.po_number_match else 'FAIL'}"
        + (f" ({result.observed_po_number})" if result.observed_po_number else ""),
        f"detail_count: {result.observed_detail_count} "
        f"({'ok' if result.detail_count_match else 'FAIL'})",
        f"process_mapping: {'ok' if result.process_mapping_ok else 'FAIL'}"
        + (
            f" {result.observed_process_raws}"
            if result.observed_process_raws
            else ""
        ),
        f"overall: {'PASS' if result.ok else 'FAIL'}",
    ]
    if result.errors:
        lines.append("errors:")
        for err in result.errors:
            lines.append(f"  - {err}")
    return "\n".join(lines)
