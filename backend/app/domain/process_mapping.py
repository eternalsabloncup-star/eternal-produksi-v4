"""Mapping proses sumber → Cetak / Non-Cetak.

Sumber Owner: detailItem[].charField1.
Jangan menebak dari nama barang.
"""

from __future__ import annotations

from typing import Literal

ProcessKind = Literal["Cetak", "Non-Cetak"]

# "Tidak Cetak" = nilai Accurate charField1 (Owner).
# "Non-Cetak" = alias kompatibilitas kontrak internal.
VERIFIED_PROCESS_SOURCE_MAP: dict[str, ProcessKind] = {
    "Cetak": "Cetak",
    "Tidak Cetak": "Non-Cetak",
    "Non-Cetak": "Non-Cetak",
}


def map_process_from_source(process_raw: str | None) -> dict[str, object]:
    raw = (process_raw or "").strip()
    if not raw:
        return {
            "ok": False,
            "process_kind": None,
            "process_raw": "",
            "reason": "Nilai proses sumber kosong (charField1).",
        }
    hit = VERIFIED_PROCESS_SOURCE_MAP.get(raw)
    if hit:
        return {
            "ok": True,
            "process_kind": hit,
            "process_raw": raw,
            "reason": None,
        }
    return {
        "ok": False,
        "process_kind": None,
        "process_raw": raw,
        "reason": (
            f'Nilai proses "{raw}" belum ada di allowlist terverifikasi '
            "(bukan asumsi nama barang)."
        ),
    }
