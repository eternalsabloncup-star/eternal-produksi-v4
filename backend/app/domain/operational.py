"""Identitas domain operasional V4 (Checkpoint A).

Kontrak identitas mengikuti sumber Accurate: PO, item line, Surat Jalan.
Dua item line tidak pernah digabung meskipun kode barangnya sama.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, Field

OperationalRole = Literal[
    "production-head",
    "designer",
    "helper",
    "screener",
    "operator",
    "driver",
]

OPERATIONAL_ROLES: Final[tuple[OperationalRole, ...]] = (
    "production-head",
    "designer",
    "helper",
    "screener",
    "operator",
    "driver",
)

TaskAction = Literal[
    "view",
    "input",
    "confirm",
    "approve",
    "edit",
    "delete",
    "manage_evidence",
]

TASK_ACTIONS: Final[tuple[TaskAction, ...]] = (
    "view",
    "input",
    "confirm",
    "approve",
    "edit",
    "delete",
    "manage_evidence",
)

# Cakupan unit tugas — menentukan identitas task_key.
TaskScope = Literal["po", "po_line", "po_line_allocation", "delivery_note"]


class TaskTypeSpec(BaseModel):
    """Definisi jenis pekerjaan. Default terkunci Owner, tetap dapat dikonfigurasi."""

    task_type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    scope: TaskScope
    default_role: OperationalRole
    # Aksi yang relevan untuk jenis pekerjaan ini (bukan daftar permission aktor).
    actions: list[TaskAction] = Field(default_factory=lambda: ["view", "confirm"])
    # Tugas koordinasi ditampilkan merah penuh di UI (ketentuan Owner).
    coordination: bool = False
    # Konfirmasi parsial berulang diperbolehkan (ledger Qty).
    partial_qty: bool = False


# Default TERKUNCI per dokumen aturan bisnis 18 Nov 2026.
# Ini default pemetaan, bukan pembatas permanen: Master dapat mengubahnya.
DEFAULT_TASK_TYPES: Final[tuple[TaskTypeSpec, ...]] = (
    TaskTypeSpec(
        task_type="design",
        label="Design",
        scope="po",
        default_role="designer",
        actions=["view", "input", "confirm", "manage_evidence"],
    ),
    TaskTypeSpec(
        task_type="penjadwalan",
        label="Penjadwalan",
        scope="po_line",
        default_role="production-head",
        actions=["view", "input", "confirm", "edit"],
        partial_qty=True,
    ),
    TaskTypeSpec(
        task_type="penjadwalan-ulang",
        label="Penjadwalan Ulang",
        scope="po_line",
        default_role="production-head",
        actions=["view", "input", "confirm", "edit"],
        partial_qty=True,
    ),
    TaskTypeSpec(
        task_type="approval-sampel",
        label="Approval Sampel",
        scope="po_line_allocation",
        default_role="production-head",
        actions=["view", "approve"],
    ),
    TaskTypeSpec(
        task_type="qty-loading-sj-berbeda",
        label="Qty Loading dan SJ Berbeda",
        scope="delivery_note",
        default_role="production-head",
        actions=["view", "confirm"],
        coordination=True,
    ),
    TaskTypeSpec(
        task_type="pengiriman-gagal-koordinasi",
        label="Pengiriman Gagal — Koordinasi Administrasi",
        scope="delivery_note",
        default_role="production-head",
        actions=["view", "confirm"],
        coordination=True,
    ),
    TaskTypeSpec(
        task_type="persiapan-cup",
        label="Persiapan Cup",
        scope="po_line",
        default_role="helper",
        actions=["view", "input", "confirm"],
        partial_qty=True,
    ),
    TaskTypeSpec(
        task_type="qc-packing",
        label="QC & Packing",
        scope="po_line",
        default_role="helper",
        actions=["view", "input", "confirm"],
        partial_qty=True,
    ),
    TaskTypeSpec(
        task_type="screen",
        label="Screen",
        scope="po_line_allocation",
        default_role="screener",
        actions=["view", "input", "confirm"],
        partial_qty=True,
    ),
    TaskTypeSpec(
        task_type="konfirmasi-sample",
        label="Upload/Konfirmasi Sample",
        scope="po_line_allocation",
        default_role="operator",
        actions=["view", "input", "confirm", "manage_evidence"],
    ),
    TaskTypeSpec(
        task_type="cetak",
        label="Cetak",
        scope="po_line_allocation",
        default_role="operator",
        actions=["view", "input", "confirm"],
        partial_qty=True,
    ),
    TaskTypeSpec(
        task_type="pengiriman",
        label="Pengiriman",
        scope="delivery_note",
        default_role="driver",
        actions=["view", "input", "confirm", "manage_evidence"],
    ),
)

DEFAULT_TASK_TYPE_BY_ID: Final[dict[str, TaskTypeSpec]] = {
    spec.task_type: spec for spec in DEFAULT_TASK_TYPES
}


class TaskRef(BaseModel):
    """Referensi sumber Accurate untuk satu unit tugas."""

    accurate_po_id: str | None = None
    po_no: str | None = None
    accurate_line_id: str | None = None
    item_code: str | None = None
    process_kind: str | None = None
    allocation_id: str | None = None
    accurate_delivery_note_id: str | None = None
    sj_no: str | None = None


def _part(label: str, value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"Identitas tugas wajib memuat {label}.")
    return f"{label}:{text}"


def build_task_key(task_type: str, scope: TaskScope, ref: TaskRef) -> str:
    """Kunci tugas deterministik — mencegah duplikasi saat sync/refresh/retry."""
    tt = (task_type or "").strip()
    if not tt:
        raise ValueError("task_type wajib diisi.")

    if scope == "po":
        tail = [_part("po", ref.accurate_po_id)]
    elif scope == "po_line":
        tail = [_part("po", ref.accurate_po_id), _part("line", ref.accurate_line_id)]
    elif scope == "po_line_allocation":
        tail = [
            _part("po", ref.accurate_po_id),
            _part("line", ref.accurate_line_id),
            _part("alloc", ref.allocation_id),
        ]
    elif scope == "delivery_note":
        tail = [_part("sj", ref.accurate_delivery_note_id)]
    else:  # pragma: no cover - dicegah oleh Literal
        raise ValueError(f"Scope tugas tidak dikenal: {scope}")

    return "task:" + ":".join([tt, *tail])


def build_allocation_id(accurate_line_id: str, sequence: int) -> str:
    """ID alokasi operator unik per item line (bukan per PO)."""
    line = (accurate_line_id or "").strip()
    if not line:
        raise ValueError("accurate_line_id wajib untuk alokasi.")
    if sequence < 1:
        raise ValueError("Urutan alokasi mulai dari 1.")
    return f"alloc:{line}:{sequence}"


def normalize_operational_role(role: str | None) -> OperationalRole | None:
    raw = (role or "").strip().lower()
    return raw if raw in OPERATIONAL_ROLES else None  # type: ignore[return-value]


def task_ref_to_document(ref: TaskRef) -> dict[str, Any]:
    return {k: v for k, v in ref.model_dump().items() if v not in (None, "")}
