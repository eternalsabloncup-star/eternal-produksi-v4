"""15 customer-facing Live Tracking milestones — mapping ke event backend V4.

Bukan sumber status baru. Status diturunkan dari bukti event yang sah.
"""

from __future__ import annotations

from typing import Final, Literal

MilestoneState = Literal[
    "completed",
    "in_progress",
    "pending",
    "not_applicable",
    "attention_required",
    "unknown",
]

MilestoneId = Literal[
    "order_received",
    "order_verified",
    "design_prep",
    "design_approved",
    "material_prep",
    "production_scheduled",
    "print_prep",
    "printing",
    "quality_check",
    "production_complete",
    "shipping_prep",
    "ready_to_ship",
    "in_transit",
    "delivered",
    "order_complete",
]


class MilestoneSpec:
    __slots__ = ("id", "seq", "label", "print_only", "backend_sources", "gap_note")

    def __init__(
        self,
        *,
        id: MilestoneId,
        seq: int,
        label: str,
        print_only: bool = False,
        backend_sources: tuple[str, ...],
        gap_note: str | None = None,
    ) -> None:
        self.id = id
        self.seq = seq
        self.label = label
        self.print_only = print_only
        self.backend_sources = backend_sources
        self.gap_note = gap_note


CUSTOMER_MILESTONES: Final[tuple[MilestoneSpec, ...]] = (
    MilestoneSpec(
        id="order_received",
        seq=1,
        label="Pesanan Diterima",
        backend_sources=("accurate_sales_orders.created_at|source_synced_at",),
    ),
    MilestoneSpec(
        id="order_verified",
        seq=2,
        label="Verifikasi Pesanan",
        # SoT: Kepala Produksi accept_po → operational.acceptance.status=accepted.
        # Bukan field Accurate terpisah; jangan diganti status order_verified baru.
        backend_sources=("operational.acceptance.status=accepted",),
        gap_note=None,
    ),
    MilestoneSpec(
        id="design_prep",
        seq=3,
        label="Persiapan Desain",
        print_only=True,
        backend_sources=(
            "design_submissions",
            "operational_tasks[task_type=design]",
        ),
    ),
    MilestoneSpec(
        id="design_approved",
        seq=4,
        label="Persetujuan Desain",
        print_only=True,
        backend_sources=("po_gates.design=open", "design_approvals"),
    ),
    MilestoneSpec(
        id="material_prep",
        seq=5,
        label="Persiapan Material",
        backend_sources=("operational_tasks[task_type=persiapan-cup]",),
    ),
    MilestoneSpec(
        id="production_scheduled",
        seq=6,
        label="Penjadwalan Produksi",
        backend_sources=(
            "operational_tasks[task_type=penjadwalan]",
            "production_schedules",
        ),
    ),
    MilestoneSpec(
        id="print_prep",
        seq=7,
        label="Persiapan Cetak",
        print_only=True,
        backend_sources=("operational_tasks[task_type=screen]",),
    ),
    MilestoneSpec(
        id="printing",
        seq=8,
        label="Proses Cetak",
        print_only=True,
        backend_sources=("operational_tasks[task_type=cetak]",),
    ),
    MilestoneSpec(
        id="quality_check",
        seq=9,
        label="Pemeriksaan Kualitas",
        backend_sources=("operational_tasks[task_type=qc-packing]",),
    ),
    MilestoneSpec(
        id="production_complete",
        seq=10,
        label="Penyelesaian Produksi",
        backend_sources=(
            "qty.produced_validated >= qty.po (bila ledger produksi tersedia)",
            "operational_tasks production done aggregate",
        ),
        gap_note=(
            "Qty produksi tervalidasi belum ada kontrak ledger di main; "
            "menggunakan agregat task cetak/qc bila koleksi PR#6 ada."
        ),
    ),
    MilestoneSpec(
        id="shipping_prep",
        seq=11,
        label="Persiapan Pengiriman",
        backend_sources=("delivery_notes|surat_jalan (bila tersedia)",),
        gap_note="Backend SJ/delivery belum di main; Pending hingga modul delivery merge.",
    ),
    MilestoneSpec(
        id="ready_to_ship",
        seq=12,
        label="Barang Siap Dikirim",
        backend_sources=("loading confirmation / siap-kirim gate",),
        gap_note="Event loading belum tersedia di main.",
    ),
    MilestoneSpec(
        id="in_transit",
        seq=13,
        label="Dalam Pengiriman",
        backend_sources=("operational_tasks[task_type=pengiriman] partial/in-progress",),
    ),
    MilestoneSpec(
        id="delivered",
        seq=14,
        label="Barang Diterima",
        backend_sources=("delivery evidence confirmations (non soft-deleted)",),
        gap_note="Bukti pengiriman belum ada kontrak di main.",
    ),
    MilestoneSpec(
        id="order_complete",
        seq=15,
        label="Pesanan Selesai",
        backend_sources=(
            "final gate: qty delivered >= qty wajib kirim AND no open obligations",
        ),
        gap_note=(
            "Gate final belum di main; tidak boleh menandai selesai tanpa bukti Qty."
        ),
    ),
)

MILESTONE_BY_ID: Final[dict[str, MilestoneSpec]] = {
    m.id: m for m in CUSTOMER_MILESTONES
}

# Bobot milestone TIDAK dipakai untuk % produksi.
# % produksi = qty tervalidasi / qty PO (sumber terpisah).


def slugify_customer(name: str) -> str:
    """Label URL saja — bukan autentikasi."""
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:80] or "pelanggan"


def slugify_po_reference(po_no: str) -> str:
    """Label URL untuk nomor PO (aman path)."""
    import re

    raw = (po_no or "").strip().lower()
    raw = raw.replace("/", "-").replace(" ", "-")
    raw = re.sub(r"[^a-z0-9._-]+", "-", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    return raw[:96] or "po"
