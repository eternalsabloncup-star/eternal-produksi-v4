"""Read-only discovery relasi Surat Jalan ↔ Sales Order (Accurate GET saja).

Tidak menulis Accurate. Tidak menebak relasi: hanya dari detail SJ yang
lolos ``normalize_delivery_note``. Multi-PO dihitung terpisah (bukan seed single-SO);
dipilih sebagai kandidat uji).
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.fields import as_text
from app.repositories import delivery_repository as deliveries
from app.services.delivery_sync import _list_page_params


def discover_sj_so_links(
    *,
    client: AccurateReadClient,
    page_size: int = 20,
    max_pages: int = 5,
    max_rows: int = 100,
    timeout_seconds: float = 120.0,
    prefer_po_id: str | None = None,
) -> dict[str, Any]:
    """Pindai list+detail DO; kembalikan kandidat uji tanpa PII pelanggan.

    Output aman: id numerik Accurate, counters, kode error — tanpa nama/alamat.
    """
    page_size = max(1, min(int(page_size), 100))
    max_pages = max(1, min(int(max_pages), 200))
    max_rows = max(1, min(int(max_rows), 2000))
    timeout_seconds = max(5.0, float(timeout_seconds))
    prefer = as_text(prefer_po_id) if prefer_po_id is not None else ""

    by_po: dict[str, list[str]] = defaultdict(list)
    listed = 0
    pages = 0
    multi_so = 0
    identity_failed = 0
    detail_failed = 0
    single_so = 0
    started = time.monotonic()
    stopped_reason: str | None = None

    for page in range(1, max_pages + 1):
        if time.monotonic() - started > timeout_seconds:
            stopped_reason = "timeout"
            break
        if listed >= max_rows:
            stopped_reason = "max_rows"
            break

        rows = client.fetch_delivery_order_list(
            params=_list_page_params(page, page_size)
        )
        pages += 1
        if not rows:
            break

        for row in rows:
            if time.monotonic() - started > timeout_seconds:
                stopped_reason = "timeout"
                break
            if listed >= max_rows:
                stopped_reason = "max_rows"
                break

            listed += 1
            sj_id = as_text(row.get("id"))
            if not sj_id:
                detail_failed += 1
                continue
            try:
                detail = client.fetch_delivery_order_detail(sj_id)
                note = deliveries.normalize_delivery_note(detail)
            except deliveries.DeliveryNoteIdentityError:
                identity_failed += 1
                continue
            except Exception:  # noqa: BLE001 — discovery partial OK
                detail_failed += 1
                continue

            parents = deliveries.note_parent_po_ids(note)
            if not parents:
                identity_failed += 1
                continue
            if deliveries.note_is_multi_po(note):
                # Discovery seed tetap single-SO; multi-PO dicatat, tidak dipilih.
                multi_so += 1
                continue
            po_id = parents[0]
            single_so += 1
            if sj_id not in by_po[po_id]:
                by_po[po_id].append(sj_id)

        if stopped_reason:
            break
        if len(rows) < page_size:
            break
    else:
        stopped_reason = "max_pages"

    selected_po_id: str | None = None
    selected_sj_ids: list[str] = []
    selection_reason = "none"

    if prefer and by_po.get(prefer):
        selected_po_id = prefer
        selected_sj_ids = list(by_po[prefer])
        selection_reason = "prefer_po_match"
    elif by_po:
        # Pilih PO dengan SJ single-SO terbanyak (deterministik: sort by count, po_id).
        ranked = sorted(
            by_po.items(),
            key=lambda kv: (-len(kv[1]), kv[0]),
        )
        selected_po_id, selected_sj_ids = ranked[0][0], list(ranked[0][1])
        selection_reason = "first_eligible_po"
        if prefer:
            selection_reason = "prefer_po_miss_fallback_eligible"

    return {
        "ok": True,
        "writes_accurate": False,
        "listed": listed,
        "pages": pages,
        "stopped_reason": stopped_reason,
        "single_so_sj": single_so,
        "multi_so_rejected": multi_so,
        "identity_failed": identity_failed,
        "detail_failed": detail_failed,
        "distinct_po_count": len(by_po),
        "prefer_po_id": prefer or None,
        "prefer_po_sj_count": len(by_po.get(prefer, [])),
        "selected_po_id": selected_po_id,
        "selected_sj_ids": selected_sj_ids,
        "selected_sj_count": len(selected_sj_ids),
        "selection_reason": selection_reason,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
