"""Alur sampel: draft → confirm → (opsional) approve. Tanpa migrasi data."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.services import production_service as prod

PNG = b"\x89PNG sample-flow"


def draft_and_confirm(
    db: Database,
    operator: dict[str, Any],
    allocation_id: str,
    *,
    filename: str = "sample.png",
    content: bytes = PNG,
) -> dict[str, Any]:
    prod.upload_sample_draft(
        db,
        operator,
        allocation_id=allocation_id,
        filename=filename,
        content_type="image/png",
        content=content,
    )
    return prod.confirm_sample_submission(
        db, operator, allocation_id=allocation_id
    )["sample"]


def approve_sample(
    db: Database,
    operator: dict[str, Any],
    head: dict[str, Any],
    allocation_id: str,
) -> dict[str, Any]:
    submitted = draft_and_confirm(db, operator, allocation_id)
    return prod.decide_sample(
        db,
        head,
        allocation_id=allocation_id,
        version=int(submitted["version"]),
        approved=True,
    )
