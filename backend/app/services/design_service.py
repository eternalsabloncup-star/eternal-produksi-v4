"""Alur Design: upload → konfirmasi Designer → link customer → approval + TTD."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.repositories import design_repository as design
from app.repositories import evidence_repository as evidence
from app.repositories import gate_repository as gates
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event
from app.services.design_link_crypto import (
    DesignLinkCryptoError,
    decrypt_approval_token,
    encrypt_approval_token,
    parse_recovery_key,
)
from app.services.task_engine import require_action

DESIGN_SCOPE = "design"
SIGNATURE_SCOPE = "design-signature"


class DesignStateError(RuntimeError):
    """Aksi desain tidak sah pada state saat ini."""


class DesignLinkRecoveryError(RuntimeError):
    """Recovery Live Tracking gagal (fail-closed). Pesan aman untuk client."""

    def __init__(self, message: str = "Link Approval Design tidak tersedia.") -> None:
        super().__init__(message)
        self.message = message


def _strip_link_secrets(link: dict[str, Any]) -> dict[str, Any]:
    """Jangan expose token_hash / ciphertext ke API response."""
    return {
        k: v
        for k, v in link.items()
        if k not in {"token_hash", "token_ciphertext"}
    }


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
    design_version = int(latest["version"])

    import secrets as _secrets

    raw_token = _secrets.token_urlsafe(32)
    ciphertext: str | None = None
    try:
        recovery_key = parse_recovery_key(
            get_settings().design_approval_link_recovery_key
        )
        if recovery_key is not None:
            ciphertext = encrypt_approval_token(
                raw_token,
                accurate_po_id=accurate_po_id,
                design_version=design_version,
                key=recovery_key,
            )
    except DesignLinkCryptoError:
        ciphertext = None

    _token, link = design.create_approval_link(
        db,
        accurate_po_id=accurate_po_id,
        design_version=design_version,
        created_by=actor,
        raw_token=raw_token,
        token_ciphertext=ciphertext,
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
            "recoverable": bool(ciphertext),
        },
    )
    # Token mentah hanya dikembalikan sekali; database menyimpan hash (+ ciphertext).
    return {"token": raw_token, "link": _strip_link_secrets(link)}


def build_design_approval_eligibility(
    db: Database,
    *,
    accurate_po_id: str,
) -> dict[str, Any]:
    """Metadata minimum untuk Live Tracking popup — tanpa raw token.

    Source-of-truth: link approval yang benar-benar dibuat Designer + recoverable.
    Design Gate open / task done saja TIDAK cukup untuk eligible.
    """
    none_payload = {
        "eligible": False,
        "pending_approval": False,
        "design_version": None,
        "expires_at": None,
        "link_status": "none",
    }
    latest = design.latest_confirmed_submission(db, accurate_po_id)
    if latest is None:
        return none_payload

    version = int(latest["version"])
    approval = design.get_approval(
        db, accurate_po_id=accurate_po_id, design_version=version
    )
    if approval is not None:
        return {
            "eligible": False,
            "pending_approval": False,
            "design_version": version,
            "expires_at": None,
            "link_status": "approved",
        }

    link = design.find_active_recoverable_link_for_version(
        db, accurate_po_id=accurate_po_id, design_version=version
    )
    if link is None:
        # Bisa jadi ada link legacy / expired / revoked — jangan tebak token.
        return {
            "eligible": False,
            "pending_approval": False,
            "design_version": version,
            "expires_at": None,
            "link_status": "unavailable",
        }

    expires_at = link.get("expires_at")
    expires_iso = None
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_iso = expires_at.isoformat()

    return {
        "eligible": True,
        "pending_approval": True,
        "design_version": version,
        "expires_at": expires_iso,
        "link_status": "active",
    }


def recover_approval_token_for_po(
    db: Database,
    *,
    accurate_po_id: str,
) -> tuple[str, dict[str, Any]]:
    """Decrypt raw token untuk latest confirmed pending version. Fail-closed."""
    eligibility = build_design_approval_eligibility(db, accurate_po_id=accurate_po_id)
    if not eligibility.get("eligible"):
        raise DesignLinkRecoveryError()

    version = int(eligibility["design_version"])
    link = design.find_active_recoverable_link_for_version(
        db, accurate_po_id=accurate_po_id, design_version=version
    )
    if link is None:
        raise DesignLinkRecoveryError()

    ciphertext = link.get("token_ciphertext")
    if not ciphertext or not isinstance(ciphertext, str):
        raise DesignLinkRecoveryError()

    try:
        key = parse_recovery_key(get_settings().design_approval_link_recovery_key)
    except DesignLinkCryptoError as exc:
        raise DesignLinkRecoveryError() from exc
    if key is None:
        raise DesignLinkRecoveryError()

    try:
        raw = decrypt_approval_token(
            ciphertext,
            accurate_po_id=accurate_po_id,
            design_version=version,
            key=key,
        )
    except DesignLinkCryptoError as exc:
        raise DesignLinkRecoveryError() from exc

    # Verifikasi hash — ciphertext rusak/tampered yang kebetulan decrypt harus ditolak.
    if design.hash_token(raw) != link.get("token_hash"):
        raise DesignLinkRecoveryError()

    return raw, link


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
