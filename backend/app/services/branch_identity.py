"""Identitas cabang kanonis = Accurate ``branchId`` (string).

Kontrak Owner PR #20:
- PO / user / object scope memakai identitas yang sama.
- Nama cabang hanya dari Master yang diverifikasi — jangan mengarang dari ID.
- Mapping manual tidak dipakai untuk otorisasi.
"""

from __future__ import annotations

from typing import Any


def normalize_branch_identity(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    # Accurate branchId numerik — pertahankan digit apa adanya.
    if text.isdigit():
        return text
    # Legacy Master code (HQ, SMG, …) — normalisasi huruf besar.
    return text.upper()


def extract_accurate_branch_id(doc: dict[str, Any] | None) -> str | None:
    """Ambil Accurate branchId dari dokumen PO / meta."""
    if not isinstance(doc, dict):
        return None
    header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    meta = header.get("raw_sync_meta") if isinstance(header.get("raw_sync_meta"), dict) else {}
    for candidate in (
        doc.get("branch_id"),
        doc.get("accurate_branch_id"),
        meta.get("accurate_branch_id"),
        header.get("branchId"),
        doc.get("branchId"),
    ):
        nid = normalize_branch_identity(candidate)
        if nid:
            return nid
    return None


def branch_display_name(
    master_doc: dict[str, Any] | None, *, branch_id: str | None
) -> dict[str, Any]:
    """Nama tampilan — tidak mengarang dari ID mentah."""
    if master_doc and str(master_doc.get("name") or "").strip():
        verified = bool(master_doc.get("accurate_mapping_verified") or master_doc.get("name_verified"))
        return {
            "branch_id": branch_id,
            "branch_name": str(master_doc.get("name")).strip(),
            "branch_name_verified": verified,
        }
    return {
        "branch_id": branch_id,
        "branch_name": None,
        "branch_name_verified": False,
        "branch_name_status": "unverified" if branch_id else "missing",
    }
