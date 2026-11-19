"""Live Tracking V4 — generate link, public view, internal view, dokumen berizin."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pymongo.database import Database

from app.domain.tracking_milestones import slugify_customer, slugify_po_reference
from app.repositories import tracking_link_repository as links
from app.repositories.po_repository import get_by_accurate_id, get_by_po_no
from app.services.audit_log import append_audit_event
from app.services.tracking_progress import (
    compute_progress,
    evaluate_milestones,
    po_has_print_items,
    qty_po_total,
)
from app.services.tracking_workflow_adapter import load_workflow_snapshot

ViewMode = Literal["customer", "internal"]

HELP_CONTACT = {
    "title": "Bantuan / Customer Service",
    "hours": "Senin–Sabtu 08:00–17:00 WIB",
    "phone": None,  # diisi Owner saat konfigurasi
    "email": "cs@eternalpackaging.id",
    "whatsapp": None,
    "note": "Sertakan nomor PO saat menghubungi kami.",
}


class TrackingError(Exception):
    def __init__(self, message: str, *, code: str = "tracking_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TrackingNotFound(TrackingError):
    def __init__(self, message: str = "Link pelacakan tidak valid.") -> None:
        super().__init__(message, code="not_found")


class TrackingForbidden(TrackingError):
    def __init__(self, message: str = "Akses ditolak.") -> None:
        super().__init__(message, code="forbidden")


def _header(po_doc: dict[str, Any]) -> dict[str, Any]:
    return dict(po_doc.get("header") or {})


def resolve_po(db: Database, accurate_po_id_or_po_no: str) -> dict[str, Any]:
    key = (accurate_po_id_or_po_no or "").strip()
    if not key:
        raise TrackingNotFound("PO tidak ditemukan.")
    doc = get_by_accurate_id(db, key)
    if doc is None:
        doc = get_by_po_no(db, key)
    if doc is None:
        raise TrackingNotFound("PO tidak ditemukan.")
    return doc


def build_public_path(*, customer_slug: str, po_slug: str, raw_token: str) -> str:
    return f"/t/{customer_slug}/{po_slug}/{raw_token}"


def generate_tracking_link(
    db: Database,
    *,
    accurate_po_id: str,
    actor_email: str | None,
    scopes: list[str] | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    po = resolve_po(db, accurate_po_id)
    header = _header(po)
    customer = str(header.get("customer") or "Pelanggan")
    po_no = str(po.get("po_no") or header.get("po_no") or "")
    customer_slug = slugify_customer(customer)
    po_slug = slugify_po_reference(po_no)
    raw, link = links.create_link(
        db,
        accurate_po_id=str(po["accurate_id"]),
        po_no=po_no,
        customer_name=customer,
        customer_slug=customer_slug,
        po_slug=po_slug,
        actor_email=actor_email,
        scopes=scopes,
        ttl_seconds=ttl_seconds,
    )
    path = build_public_path(
        customer_slug=customer_slug, po_slug=po_slug, raw_token=raw
    )
    append_audit_event(
        db,
        action="tracking.link.generate",
        actor_email=actor_email,
        target_email=None,
        detail={
            "accurate_po_id": str(po["accurate_id"]),
            "po_no": po_no,
            "link_id": link.get("id"),
            # Jangan log raw token.
        },
    )
    return {
        "token": raw,
        "path": path,
        "link": link,
        "accurate_po_id": str(po["accurate_id"]),
        "po_no": po_no,
        "customer_slug": customer_slug,
        "po_slug": po_slug,
    }


def regenerate_tracking_link(
    db: Database,
    *,
    accurate_po_id: str,
    actor_email: str | None,
    scopes: list[str] | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    revoked = links.revoke_active_for_po(
        db,
        accurate_po_id=str(accurate_po_id),
        actor_email=actor_email,
        reason="regenerate",
    )
    result = generate_tracking_link(
        db,
        accurate_po_id=accurate_po_id,
        actor_email=actor_email,
        scopes=scopes,
        ttl_seconds=ttl_seconds,
    )
    result["revoked_previous"] = revoked
    append_audit_event(
        db,
        action="tracking.link.regenerate",
        actor_email=actor_email,
        target_email=None,
        detail={
            "accurate_po_id": str(accurate_po_id),
            "revoked_previous": revoked,
            "link_id": result["link"].get("id"),
        },
    )
    return result


def revoke_tracking_link(
    db: Database,
    *,
    link_id: str,
    actor_email: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    doc = links.revoke_link(
        db, link_id=link_id, actor_email=actor_email, reason=reason
    )
    if not doc:
        raise TrackingNotFound("Link tidak ditemukan atau sudah nonaktif.")
    append_audit_event(
        db,
        action="tracking.link.revoke",
        actor_email=actor_email,
        target_email=None,
        detail={"link_id": link_id, "reason": reason},
    )
    return doc


def list_tracking_links(db: Database, accurate_po_id: str) -> list[dict[str, Any]]:
    resolve_po(db, accurate_po_id)
    return links.list_links_for_po(db, str(accurate_po_id))


def _customer_safe_header(header: dict[str, Any]) -> dict[str, Any]:
    """Strip alamat pribadi sensitif dari view customer publik."""
    return {
        "customer": header.get("customer") or "",
        "po_no": header.get("po_no") or "",
        "po_date": header.get("po_date") or "",
        "eta_ship": header.get("eta_ship") or "",
        "expedition": header.get("expedition") or "",
        "ship_zone": header.get("ship_zone") or "",
        "urgent": bool(header.get("urgent")),
        "item_count": len(header.get("items") or []),
        # Item ringkas tanpa harga.
        "items": [
            {
                "item_code": i.get("item_code"),
                "item_name": i.get("item_name"),
                "qty": i.get("qty"),
                "unit": i.get("unit"),
                "process_kind": i.get("process_kind"),
                "color": i.get("color") or "",
            }
            for i in (header.get("items") or [])
        ],
    }


def _build_documents(
    *,
    po_doc: dict[str, Any],
    workflow: dict[str, Any],
    scopes: list[str],
    view: ViewMode,
) -> list[dict[str, Any]]:
    has_financial = "financial" in scopes or view == "internal"
    header = _header(po_doc)
    notes = [
        n
        for n in (workflow.get("delivery_notes") or [])
        if not n.get("superseded_by")
    ]
    evidence = workflow.get("delivery_evidence") or []
    success_events = [
        e
        for e in (workflow.get("delivery_events") or [])
        if e.get("status") == "berhasil"
    ]
    payments = workflow.get("payments") or []
    avail = workflow.get("availability") or {}

    docs: list[dict[str, Any]] = [
        {
            "category": "purchase_order",
            "label": "Purchase Order",
            "status": "available",
            "items": [
                {
                    "id": f"po-{po_doc.get('accurate_id')}",
                    "number": po_doc.get("po_no") or header.get("po_no"),
                    "date": header.get("po_date"),
                    "preview_allowed": True,
                    "download_allowed": view == "internal",
                    "summary": {
                        "customer": header.get("customer"),
                        "item_count": len(header.get("items") or []),
                        "qty_total": qty_po_total(po_doc),
                    },
                }
            ],
        },
        {
            "category": "sales_invoice",
            "label": "Faktur Penjualan",
            "status": "locked" if not has_financial else "waiting",
            "items": [],
            "reason": (
                None
                if has_financial
                else "Faktur memerlukan otorisasi keuangan / sesi internal."
            ),
            "note": (
                "Sumber faktur Accurate belum terhubung ke Live Tracking."
                if has_financial
                else None
            ),
        },
        {
            "category": "delivery_note",
            "label": "Surat Jalan",
            "status": (
                "available"
                if notes
                else ("waiting" if avail.get("delivery_notes") else "waiting")
            ),
            "items": [
                {
                    "id": str(n.get("accurate_sj_id") or n.get("sj_no") or i),
                    "number": n.get("sj_no") or n.get("number"),
                    "date": n.get("ship_date") or n.get("sj_date") or n.get("date"),
                    "qty": sum(float(it.get("qty") or 0) for it in (n.get("items") or []))
                    or n.get("qty")
                    or n.get("qty_total"),
                    "status": n.get("revision_status") or "recorded",
                    "preview_allowed": True,
                    "download_allowed": view == "internal",
                }
                for i, n in enumerate(notes)
            ],
            "note": None
            if notes or avail.get("delivery_notes")
            else "Modul surat jalan belum tersedia di backend saat ini.",
        },
        {
            "category": "sales_receipt",
            "label": "Penerimaan Penjualan",
            "status": "locked" if not has_financial else ("available" if payments else "waiting"),
            "items": [
                {
                    "id": str(p.get("id") or i),
                    "number": p.get("receipt_no") or p.get("id"),
                    "date": p.get("receipt_date"),
                    "amount": p.get("amount") if has_financial else None,
                    "preview_allowed": has_financial,
                    "download_allowed": False,
                }
                for i, p in enumerate(payments)
            ]
            if has_financial
            else [],
            "reason": (
                None
                if has_financial
                else "Penerimaan penjualan tidak dibuka lewat tracking link publik."
            ),
        },
        {
            "category": "delivery_proof",
            "label": "Bukti Surat Jalan / Bukti Pengiriman",
            "status": "available" if success_events else "waiting",
            "items": [
                {
                    "id": str(e.get("delivery_event_id") or e.get("id") or i),
                    "number": e.get("accurate_sj_id"),
                    "confirmed_at": e.get("created_at"),
                    "recipient_name": e.get("recipient_name") if view == "internal" else None,
                    "has_photo": bool(e.get("photo_evidence_ids")),
                    "has_signature": bool(e.get("signature_evidence_id")),
                    "preview_allowed": True,
                    "download_allowed": view == "internal",
                }
                for i, e in enumerate(success_events)
            ],
            "note": None
            if success_events or avail.get("delivery_events") or evidence
            else "Bukti pengiriman belum tersedia di backend saat ini.",
        },
    ]
    return docs


def _order_status_label(progress: dict[str, Any], milestones: list[dict[str, Any]]) -> str:
    if progress.get("order_complete"):
        return "Selesai"
    active = progress.get("active_milestone") or {}
    state = active.get("state")
    if state == "attention_required":
        return "Perlu Perhatian"
    if state == "in_progress":
        return "Dalam Proses"
    if any(m["state"] == "completed" for m in milestones):
        return "Berjalan"
    return "Menunggu"


def build_tracking_payload(
    db: Database,
    *,
    po_doc: dict[str, Any],
    scopes: list[str],
    view: ViewMode,
) -> dict[str, Any]:
    workflow = load_workflow_snapshot(db, str(po_doc["accurate_id"]))
    milestones = evaluate_milestones(po_doc, workflow)
    progress = compute_progress(milestones, po_doc, workflow)
    header = _header(po_doc)
    safe_header = _customer_safe_header(header)
    documents = _build_documents(
        po_doc=po_doc, workflow=workflow, scopes=scopes, view=view
    )

    payload: dict[str, Any] = {
        "view": view,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_control": "no-store",
        "referrer_policy": "no-referrer",
        "order": {
            "accurate_po_id": str(po_doc["accurate_id"]) if view == "internal" else None,
            "po_no": po_doc.get("po_no") or header.get("po_no"),
            "customer": safe_header["customer"],
            "po_date": safe_header["po_date"],
            "eta_ship": safe_header["eta_ship"] or None,
            "has_print_items": po_has_print_items(po_doc),
            "status_label": _order_status_label(progress, milestones),
            "last_updated_at": _ts(po_doc.get("updated_at") or po_doc.get("source_synced_at")),
            "summary": safe_header,
        },
        "progress": progress,
        "milestones": milestones
        if view == "internal"
        else [
            {
                "id": m["id"],
                "seq": m["seq"],
                "label": m["label"],
                "state": m["state"],
                "detail": m["detail"]
                if m["state"] != "attention_required"
                else "Tim kami sedang menindaklanjuti.",
                "timestamp": m["timestamp"],
                "relevant": m["relevant"],
            }
            for m in milestones
        ],
        "documents": documents,
        "help": HELP_CONTACT,
        "polling": {
            "interval_ms": 30000,
            "strategy": "light-poll",
        },
    }

    if view == "internal":
        payload["internal"] = {
            "workflow_availability": workflow.get("availability"),
            "gates": workflow.get("gates"),
            "task_counts": {
                k: len(v) for k, v in (workflow.get("tasks_by_type") or {}).items()
            },
            "gaps": sorted(
                {
                    g
                    for m in milestones
                    for g in (m.get("gaps") or [])
                    if m.get("state") in ("pending", "unknown")
                }
            ),
            "qty_compare": {
                "qty_po": progress["production"]["qty_po"],
                "qty_produced": progress["production"]["qty_produced"],
                "qty_qc": progress["production"]["qty_qc_passed"],
                "qty_shipped": progress["shipping"]["qty_shipped"],
                "qty_remaining": progress["shipping"]["qty_remaining"],
            },
        }
    return payload


def _ts(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return None


def public_tracking_view(
    db: Database,
    *,
    customer_slug: str,
    po_slug: str,
    raw_token: str,
) -> dict[str, Any]:
    """Autentikasi = token. Slug hanya label; mismatch → 404 (tidak ekspansi akses)."""
    link = links.resolve_active_link(db, raw_token)
    if link is None:
        raise TrackingNotFound()

    # Binding ketat ke satu order + slug label.
    if link.get("customer_slug") != customer_slug or link.get("po_slug") != po_slug:
        raise TrackingNotFound()

    po = get_by_accurate_id(db, link["accurate_po_id"])
    if po is None:
        raise TrackingNotFound("Pesanan terkait tidak ditemukan.")

    # Pastikan po_no masih cocok (label PO berubah → jangan buka data lain).
    current_po_slug = slugify_po_reference(str(po.get("po_no") or ""))
    if current_po_slug != po_slug:
        raise TrackingNotFound()

    links.touch_access(db, link["token_hash"])
    scopes = list(link.get("scopes") or ["operational"])
    # Publik tidak pernah dapat financial lewat tracking link saja.
    scopes = [s for s in scopes if s != "financial"]

    payload = build_tracking_payload(db, po_doc=po, scopes=scopes, view="customer")
    payload["link"] = {
        "status": link.get("status"),
        "scopes": scopes,
        "expires_at": _ts(link.get("expires_at")),
        "customer_slug": customer_slug,
        "po_slug": po_slug,
    }
    return payload


def internal_tracking_view(
    db: Database,
    *,
    accurate_po_id: str,
) -> dict[str, Any]:
    po = resolve_po(db, accurate_po_id)
    payload = build_tracking_payload(
        db, po_doc=po, scopes=["operational", "financial"], view="internal"
    )
    payload["links"] = links.list_links_for_po(db, str(po["accurate_id"]))
    return payload
