"""Helper tes: tandai PO sudah diterima tanpa memanggil HTTP."""

from __future__ import annotations

from datetime import datetime, timezone

from pymongo.database import Database


def mark_po_accepted(
    db: Database,
    accurate_po_id: str = "95106",
    *,
    by: str = "kp@eternal.local",
) -> None:
    now = datetime.now(timezone.utc)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": str(accurate_po_id)},
        {
            "$set": {
                "operational.acceptance": {
                    "status": "accepted",
                    "accepted_by": by,
                    "accepted_at": now,
                },
                "operational.inbox_status": "PO Diterima",
            }
        },
    )
