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


class TrackingConflict(TrackingError):
    def __init__(self, message: str = "Link aktif sudah ada untuk PO ini.") -> None:
        super().__init__(message, code="conflict")


class TrackingLegacyUnrecoverable(TrackingError):
    """Link aktif hash-only (pra-seal) — URL tidak dapat dipulihkan tanpa regenerate."""

    def __init__(
        self,
        message: str,
        *,
        link_id: str,
        accurate_po_id: str,
    ) -> None:
        super().__init__(message, code="legacy_unrecoverable")
        self.link_id = link_id
        self.accurate_po_id = accurate_po_id


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
    try:
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
    except links.TrackingActiveLinkExists as exc:
        raise TrackingConflict(str(exc)) from exc
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


def _lazy_expire_active_doc(db: Database, doc: dict[str, Any]) -> dict[str, Any] | None:
    """Expire lazy; return None jika bukan aktif lagi."""
    if not doc:
        return None
    if doc.get("status") == "revoked" or doc.get("revoked_at") is not None:
        return None
    exp = doc.get("expires_at")
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= datetime.now(timezone.utc):
            links.links_collection(db).update_one(
                {"_id": doc["_id"], "status": "active"},
                {"$set": {"status": "expired"}},
            )
            return None
    if doc.get("status") != "active":
        return None
    return doc


def reveal_active_tracking_url(
    db: Database,
    *,
    accurate_po_id: str,
    actor_email: str | None,
) -> dict[str, Any]:
    """Ambil path publik link aktif yang sama (tanpa regenerate).

    - Active + sealed → path (token di-unseal server-side).
    - Active hash-only legacy → TrackingLegacyUnrecoverable (bukan auto-regen).
    - Revoked/expired/absent → TrackingNotFound.
    Tidak menulis raw token ke audit/log.
    """
    from app.services.tracking_token_seal import (
        TrackingSealError,
        TrackingSealKeyMissing,
        unseal_raw_token,
    )

    po = resolve_po(db, accurate_po_id)
    po_id = str(po["accurate_id"])
    doc = _lazy_expire_active_doc(db, links.find_active_doc_for_po(db, po_id))
    if doc is None:
        raise TrackingNotFound("Tidak ada link Live Tracking aktif untuk PO ini.")

    link_id = str(doc.get("_id") or "")
    sealed = doc.get("token_sealed")
    if not sealed:
        raise TrackingLegacyUnrecoverable(
            "Link aktif ini dibuat sebelum penyimpanan URL terenkripsi. "
            "Salin ulang memerlukan pemulihan terkendali (regenerate) dengan "
            "persetujuan eksplisit operator — link lama akan dicabut.",
            link_id=link_id,
            accurate_po_id=po_id,
        )

    try:
        raw = unseal_raw_token(str(sealed))
    except TrackingSealKeyMissing:
        raise
    except TrackingSealError as exc:
        raise TrackingError(
            "Ciphertext Live Tracking tidak dapat dibuka. Periksa kunci seal server.",
            code="seal_error",
        ) from exc

    if links.hash_token(raw) != doc.get("token_hash"):
        raise TrackingError(
            "Integritas token Live Tracking gagal diverifikasi.",
            code="seal_integrity",
        )

    customer_slug = str(doc.get("customer_slug") or "")
    po_slug = str(doc.get("po_slug") or "")
    path = build_public_path(
        customer_slug=customer_slug, po_slug=po_slug, raw_token=raw
    )
    append_audit_event(
        db,
        action="tracking.link.reveal_url",
        actor_email=actor_email,
        target_email=None,
        detail={
            "accurate_po_id": po_id,
            "link_id": link_id,
            # Jangan log raw token / path lengkap (mengandung token).
        },
    )
    return {
        "status": "ok",
        "path": path,
        "link_id": link_id,
        "accurate_po_id": po_id,
        "customer_slug": customer_slug,
        "po_slug": po_slug,
        "url_recoverable": True,
    }


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


def _invoice_belongs_only_to_po(inv: dict[str, Any], po_id: str) -> bool:
    """Invoice boleh dibuka penuh hanya bila relasi single-PO terbukti.

    Fail-closed pada review_required, multi-PO, primary ambigu, atau line
    ``sales_order_id`` yang tidak seluruhnya milik PO ini.
    """
    target = str(po_id or "").strip()
    if not target:
        return False
    if inv.get("review_required") is True:
        return False
    po_ids = sorted(
        {str(x).strip() for x in (inv.get("accurate_po_ids") or []) if str(x).strip()}
    )
    primary = str(inv.get("primary_accurate_po_id") or "").strip()
    if len(po_ids) != 1 or po_ids[0] != target:
        return False
    if primary and primary != target:
        return False
    if not primary:
        # Kontrak normalized: single-PO wajib mengisi primary = po_ids[0].
        return False
    line_so_ids = [
        str(line.get("sales_order_id") or "").strip()
        for line in (inv.get("lines") or [])
        if isinstance(line, dict)
    ]
    # Tanpa line SO → mapping tidak cukup membuktikan detail milik PO ini.
    if not line_so_ids:
        return False
    if any(not sid or sid != target for sid in line_so_ids):
        return False
    return True


def _link_is_expired(link: dict[str, Any], *, now: datetime) -> bool:
    expires_at = link.get("expires_at")
    if expires_at is None:
        return False
    if isinstance(expires_at, datetime):
        exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        return exp < now
    return False


def _customer_visible_design_versions(
    workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    """Versi desain dengan izin customer terbukti — bukan sekadar approved.

    - Link aktif (belum revoke, belum expired) → preview metadata diizinkan.
    - Link used / sudah approved → riwayat diizinkan (customer pernah akses).
    - Hanya revoked/expired tanpa used/approval → tampil sebagai ditahan.
    File biner tidak pernah dilayani lewat Live Tracking.
    """
    links = workflow.get("design_approval_links") or []
    if not links:
        return []
    now = datetime.now(timezone.utc)
    subs_by_ver = {
        int(s.get("version") or 0): s for s in (workflow.get("design_submissions") or [])
    }
    approvals_by_ver = {
        int(a.get("design_version") or 0): a
        for a in (workflow.get("design_approvals") or [])
    }
    by_ver: dict[int, list[dict[str, Any]]] = {}
    for link in links:
        ver = int(link.get("design_version") or 0)
        if ver <= 0:
            continue
        by_ver.setdefault(ver, []).append(link)

    out: list[dict[str, Any]] = []
    for ver in sorted(by_ver):
        ver_links = by_ver[ver]
        sub = subs_by_ver.get(ver) or {}
        appr = approvals_by_ver.get(ver)
        has_active = False
        has_used = False
        has_revoked = False
        has_expired = False
        for link in ver_links:
            revoked = link.get("revoked_at") is not None
            expired = _link_is_expired(link, now=now)
            used = link.get("used_at") is not None
            if revoked:
                has_revoked = True
            if expired:
                has_expired = True
            if used:
                has_used = True
            if not revoked and not expired:
                has_active = True

        # Izin terbukti: link masih aktif, atau customer pernah memakai / approve.
        permission_proven = has_active or has_used or appr is not None
        if has_active:
            link_status = "active_share"
        elif has_used or appr is not None:
            link_status = "historical_share"
        elif has_revoked:
            link_status = "revoked"
        elif has_expired:
            link_status = "expired"
        else:
            link_status = "unavailable"

        out.append(
            {
                "id": f"design-v{ver}",
                "number": f"Desain v{ver}",
                "date": _ts(
                    sub.get("confirmed_at")
                    or sub.get("submitted_at")
                    or ver_links[-1].get("created_at")
                ),
                "version": ver,
                "filename": sub.get("filename") if permission_proven else None,
                "status": link_status,
                "approval_status": "approved" if appr else "not_approved",
                "approved_at": _ts((appr or {}).get("approved_at")),
                "preview_allowed": permission_proven,
                "download_allowed": False,
                "file_available": False,
                "note": (
                    "File desain tidak dilayani lewat Live Tracking."
                    if permission_proven
                    else "Izin customer untuk versi ini tidak berlaku atau sudah dicabut."
                ),
            }
        )
    return out


def _build_invoice_items(
    *,
    po_id: str,
    invoices: list[dict[str, Any]],
    view: ViewMode,
) -> tuple[list[dict[str, Any]], str | None]:
    items: list[dict[str, Any]] = []
    restricted = 0
    for inv in invoices:
        inv_id = str(inv.get("accurate_invoice_id") or "")
        if not inv_id:
            continue
        status_raw = str(inv.get("status") or "").lower()
        if status_raw in {"void", "batal", "cancelled", "canceled"}:
            continue
        if _invoice_belongs_only_to_po(inv, po_id):
            items.append(
                {
                    "id": inv_id,
                    "number": inv.get("invoice_no") or inv_id,
                    "date": inv.get("trans_date"),
                    "status": inv.get("status") or "recorded",
                    "amount": inv.get("total_amount"),
                    "preview_allowed": True,
                    "download_allowed": view == "internal",
                    "file_available": False,
                    "summary": {
                        "line_count": len(inv.get("lines") or []),
                        "single_po": True,
                    },
                }
            )
        else:
            restricted += 1
            # Jangan ekspos amount/lines — termasuk review_required single-PO.
            reason = "Faktur ditahan — relasi PO tidak terverifikasi penuh."
            if inv.get("review_required"):
                reason = str(
                    inv.get("review_reason")
                    or "Faktur berstatus REVIEW — detail tidak ditampilkan."
                )
            elif len([x for x in (inv.get("accurate_po_ids") or []) if str(x).strip()]) > 1:
                reason = (
                    "Faktur terkait lebih dari satu Sales Order — "
                    "detail tidak ditampilkan di Live Tracking."
                )
            items.append(
                {
                    "id": inv_id,
                    "number": inv.get("invoice_no") or inv_id,
                    "date": inv.get("trans_date"),
                    "status": "restricted",
                    "amount": None,
                    "preview_allowed": False,
                    "download_allowed": False,
                    "file_available": False,
                    "reason": reason,
                }
            )
    note = None
    if restricted:
        note = f"{restricted} faktur ditahan (tanpa nominal/item)."
    return items, note


def _receipt_flag(
    raw: dict[str, Any], key: str, *, default_false_when_missing: bool
) -> tuple[bool, bool]:
    """Parse flag Accurate. Returns ``(value, ambiguous)``.

    ``ambiguous=True`` → caller wajib REVIEW/fail-closed.
    """
    from app.integrations.accurate.normalize_invoice_receipt import parse_accurate_bool

    parsed, err = parse_accurate_bool(raw.get(key))
    if err:
        return True, True
    if parsed is None:
        return (False if default_false_when_missing else True), False
    return parsed, False


def _build_payment_items(
    *,
    payments: list[dict[str, Any]],
    po_id: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    target = str(po_id or "").strip()
    for i, p in enumerate(payments):
        mapped_po = str(p.get("accurate_po_id") or "").strip()
        if mapped_po != target or mapped_po in {"", "UNMAPPED"}:
            continue
        raw = p.get("raw_ref") if isinstance(p.get("raw_ref"), dict) else {}
        voided, void_ambiguous = _receipt_flag(
            raw, "void_cheq", default_false_when_missing=True
        )
        review_flag, review_ambiguous = _receipt_flag(
            raw, "review_required", default_false_when_missing=True
        )
        invoice_ids = [
            str(x).strip()
            for x in (raw.get("invoice_ids") or [])
            if str(x).strip()
        ]
        # Multi-faktur / alokasi ambigu → REVIEW; jangan verified.
        multi_invoice = len(invoice_ids) > 1
        amount = p.get("amount")
        amount_invalid = amount is None or (
            isinstance(amount, (int, float)) and float(amount) < 0
        )
        review = (
            review_flag
            or void_ambiguous
            or review_ambiguous
            or multi_invoice
            or amount_invalid
        )
        # Void/reversal tidak pernah "verified".
        if voided or void_ambiguous:
            status = "void" if voided and not void_ambiguous else "review"
            preview_allowed = False
            show_amount = False
            note = (
                "Penerimaan dibatalkan/void."
                if status == "void"
                else "Status void/reversal ambigu — ditahan untuk review."
            )
        elif review:
            status = "review"
            preview_allowed = True
            show_amount = False
            note = str(
                raw.get("review_reason")
                or (
                    "Alokasi multi-faktur — ditahan."
                    if multi_invoice
                    else "Menunggu review rekonsiliasi."
                )
            )
        else:
            status = "verified"
            preview_allowed = True
            show_amount = True
            note = (
                "Penerimaan terverifikasi dari Accurate "
                "(file bukti asli tidak tersedia di Live Tracking)."
            )
        receipt_no = (
            raw.get("receipt_no")
            or p.get("receipt_no")
            or p.get("accurate_receipt_id")
        )
        items.append(
            {
                "id": str(p.get("accurate_receipt_id") or i),
                "number": receipt_no,
                "date": p.get("receipt_date"),
                "amount": amount if show_amount else None,
                "invoice_no": p.get("invoice_no") if not multi_invoice else None,
                "status": status,
                "preview_allowed": preview_allowed,
                "download_allowed": False,
                "file_available": False,
                "note": note,
            }
        )
    return items


def _build_documents(
    *,
    po_doc: dict[str, Any],
    workflow: dict[str, Any],
    scopes: list[str],
    view: ViewMode,
) -> list[dict[str, Any]]:
    # Owner M4: invoice + receipt Accurate pesanan boleh di Live Tracking customer.
    # Scope financial internal tetap untuk field sensitif lain; bukan gate kartu ini.
    _ = scopes
    header = _header(po_doc)
    po_id = str(po_doc.get("accurate_id") or "")
    notes = [
        n
        for n in (workflow.get("delivery_notes") or [])
        if not n.get("superseded_by")
        and n.get("soft_deleted") is not True
        and n.get("deleted_at") is None
    ]
    evidence = workflow.get("delivery_evidence") or []
    success_events = [
        e
        for e in (workflow.get("delivery_events") or [])
        if e.get("status") == "berhasil"
    ]
    payments = workflow.get("payments") or []
    invoices = workflow.get("invoices") or []
    avail = workflow.get("availability") or {}
    design_items = _customer_visible_design_versions(workflow)
    invoice_items, invoice_note = _build_invoice_items(
        po_id=po_id, invoices=invoices, view=view
    )
    payment_items = _build_payment_items(payments=payments, po_id=po_id)
    viewable_invoices = [it for it in invoice_items if it.get("preview_allowed")]

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
            "category": "design",
            "label": "Design",
            "status": "available" if design_items else "waiting",
            "items": design_items,
            "note": None
            if design_items or not avail.get("design")
            else "Belum ada versi desain yang dikirim ke customer.",
        },
        {
            "category": "sales_invoice",
            "label": "Faktur Penjualan",
            "status": (
                "available"
                if viewable_invoices
                else ("waiting" if avail.get("invoices") else "waiting")
            ),
            "items": invoice_items,
            "note": invoice_note
            or (
                None
                if invoice_items or avail.get("invoices")
                else "Belum ada faktur Accurate untuk pesanan ini."
            ),
        },
        {
            "category": "delivery_note",
            "label": "Surat Jalan",
            "status": "available" if notes else "waiting",
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
                    "file_available": False,
                }
                for i, n in enumerate(notes)
            ],
            "note": None
            if notes or avail.get("delivery_notes")
            else "Surat Jalan Accurate belum tersinkronisasi.",
        },
        {
            "category": "sales_receipt",
            "label": "Penerimaan Penjualan",
            "status": "available" if payment_items else "waiting",
            "items": payment_items,
            "note": None
            if payment_items or avail.get("payments")
            else "Belum ada bukti penerimaan Accurate untuk pesanan ini.",
        },
        {
            "category": "delivery_proof",
            "label": "Bukti Surat Jalan / Bukti Pengiriman",
            "status": "available" if success_events else "waiting",
            "items": [
                {
                    "id": str(e.get("delivery_event_id") or e.get("id") or i),
                    "number": e.get("accurate_sj_id"),
                    "confirmed_at": _ts(e.get("created_at")),
                    # Nama penerima: Owner setuju bukti pengiriman untuk customer.
                    "recipient_name": e.get("recipient_name"),
                    "has_photo": bool(e.get("photo_evidence_ids")),
                    "has_signature": bool(e.get("signature_evidence_id")),
                    "preview_allowed": True,
                    "download_allowed": view == "internal",
                    "file_available": bool(e.get("photo_evidence_ids"))
                    or bool(e.get("signature_evidence_id")),
                }
                for i, e in enumerate(success_events)
            ],
            "note": None
            if success_events or avail.get("delivery_events") or evidence
            else "Bukti pengiriman belum tersedia.",
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
