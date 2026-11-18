"""Alur Design: upload → konfirmasi Designer → link customer → approval + TTD."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.repositories import design_repository as design
from app.repositories import evidence_repository as evidence
from app.repositories import gate_repository as gates
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event
from app.services.task_engine import require_action

DESIGN_SCOPE = "design"
SIGNATURE_SCOPE = "design-signature"


class DesignStateError(RuntimeError):
    """Aksi desain tidak sah pada state saat ini."""


def submit_design(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    po_no: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> dict[str, Any]:
    require_action(db, user, task_type="design", action="input")
    actor = str(user.get("email_normalized") or "")

    stored = evidence.store_evidence(
        db,
        scope=DESIGN_SCOPE,
        scope_id=accurate_po_id,
        filename=filename,
        content_type=content_type,
        content=content,
        uploaded_by=actor,
        kind="design-file",
        context={"po_no": po_no},
    )
    submission = design.create_submission(
        db,
        accurate_po_id=accurate_po_id,
        po_no=po_no,
        evidence_id=stored["evidence_id"],
        filename=filename,
        submitted_by=actor,
    )
    append_audit_event(
        db,
        action="design.submission.upload",
        actor_email=actor,
        target_email=None,
        detail={
            "accurate_po_id": accurate_po_id,
            "version": submission["version"],
            "evidence_id": stored["evidence_id"],
        },
    )
    return {"submission": submission, "evidence": stored}


def confirm_design(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    version: int,
) -> dict[str, Any]:
    require_action(db, user, task_type="design", action="confirm")
    actor = str(user.get("email_normalized") or "")
    submission = design.confirm_submission(
        db, accurate_po_id=accurate_po_id, version=version, confirmed_by=actor
    )
    append_audit_event(
        db,
        action="design.submission.confirm",
        actor_email=actor,
        target_email=None,
        detail={"accurate_po_id": accurate_po_id, "version": version},
    )
    return submission


def create_customer_link(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
) -> dict[str, Any]:
    """Link approval hanya untuk desain yang sudah dikonfirmasi Designer."""
    require_action(db, user, task_type="design", action="input")
    latest = design.latest_submission(db, accurate_po_id)
    if latest is None:
        raise DesignStateError("Belum ada file desain untuk PO ini.")
    if latest.get("confirmed_at") is None:
        raise DesignStateError("Desain belum dikonfirmasi Designer.")

    actor = str(user.get("email_normalized") or "")
    raw_token, link = design.create_approval_link(
        db,
        accurate_po_id=accurate_po_id,
        design_version=int(latest["version"]),
        created_by=actor,
    )
    append_audit_event(
        db,
        action="design.approval_link.create",
        actor_email=actor,
        target_email=None,
        detail={
            "accurate_po_id": accurate_po_id,
            "design_version": latest["version"],
            "expires_at": link["expires_at"].isoformat(),
        },
    )
    # Token mentah hanya dikembalikan sekali; database menyimpan hash-nya.
    return {"token": raw_token, "link": {k: v for k, v in link.items() if k != "token_hash"}}


def public_approval_context(
    db: Database, raw_token: str, po_doc: dict[str, Any] | None
) -> dict[str, Any]:
    """Data minimum untuk halaman customer — tanpa data internal V4."""
    link = design.find_valid_link(db, raw_token)
    if link is None:
        raise LookupError("Link approval tidak valid atau sudah kedaluwarsa.")
    if po_doc is None:
        raise LookupError("PO tidak ditemukan.")

    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    print_items = [
        {
            "item_name": i.get("item_name"),
            "item_code": i.get("item_code"),
            "qty": i.get("qty"),
            "unit": i.get("unit"),
            "color": i.get("color"),
        }
        for i in (header.get("items") or [])
        if isinstance(i, dict) and i.get("process_kind") == "Cetak"
    ]
    submission = design.latest_submission(db, link["accurate_po_id"]) or {}
    approval = design.get_approval(
        db,
        accurate_po_id=link["accurate_po_id"],
        design_version=int(link["design_version"]),
    )
    return {
        "po_no": po_doc.get("po_no") or header.get("po_no"),
        "customer": header.get("customer"),
        "design_version": link["design_version"],
        "design_filename": submission.get("filename"),
        "design_evidence_id": submission.get("evidence_id"),
        "items": print_items,
        "already_approved": approval is not None,
        "approved_at": (approval or {}).get("approved_at"),
    }


def approve_design(
    db: Database,
    *,
    raw_token: str,
    signer_name: str,
    signature_png: bytes,
) -> dict[str, Any]:
    """Satu tanda tangan digital untuk seluruh item Cetak pada PO."""
    link = design.find_valid_link(db, raw_token)
    if link is None:
        raise LookupError("Link approval tidak valid atau sudah kedaluwarsa.")
    if not signer_name.strip():
        raise ValueError("Nama penanda tangan wajib diisi.")
    if not signature_png:
        raise ValueError("Tanda tangan digital wajib diisi.")

    accurate_po_id = str(link["accurate_po_id"])
    design_version = int(link["design_version"])

    existing = design.get_approval(
        db, accurate_po_id=accurate_po_id, design_version=design_version
    )
    if existing is not None:
        return {"approval": existing, "duplicate": True, "gates": gates.get_gates(db, accurate_po_id)}

    stored = evidence.store_evidence(
        db,
        scope=SIGNATURE_SCOPE,
        scope_id=accurate_po_id,
        filename=f"signature-v{design_version}.png",
        content_type="image/png",
        content=signature_png,
        uploaded_by=f"customer:{signer_name.strip()}",
        kind="design-signature",
        context={"design_version": design_version},
    )
    approval = design.record_approval(
        db,
        accurate_po_id=accurate_po_id,
        design_version=design_version,
        signer_name=signer_name,
        signature_evidence_id=stored["evidence_id"],
        token_hash=link["token_hash"],
    )
    if approval.get("duplicate"):
        return {"approval": approval, "duplicate": True, "gates": gates.get_gates(db, accurate_po_id)}

    gate_doc = gates.set_gate(
        db,
        accurate_po_id=accurate_po_id,
        gate="design",
        state=gates.GATE_OPEN,
        reason=f"Approval desain v{design_version} oleh {signer_name.strip()}.",
        actor_email=None,
        detail={"design_version": design_version, "signer_name": signer_name.strip()},
    )
    append_audit_event(
        db,
        action="design.approval.customer",
        actor_email=None,
        target_email=None,
        detail={
            "accurate_po_id": accurate_po_id,
            "design_version": design_version,
            "signer_name": signer_name.strip(),
            "signature_evidence_id": stored["evidence_id"],
        },
    )

    design_task = tasks.get_task(db, f"task:design:po:{accurate_po_id}")
    if design_task is not None and design_task.get("status") != tasks.TASK_STATUS_DONE:
        tasks.tasks_collection(db).update_one(
            {"task_key": design_task["task_key"]},
            {"$set": {"status": tasks.TASK_STATUS_DONE}},
        )

    return {"approval": approval, "duplicate": False, "gates": gate_doc}
