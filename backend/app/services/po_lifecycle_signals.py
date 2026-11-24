"""M8.4 — read-only Accurate SO lifecycle signal observation.

Observes possible cancel/void/status fields on Accurate Sales Order payloads
and persisted PO docs. Does NOT enforce cancellation side effects, does NOT
mutate production/scheduling/delivery, and does NOT invent order_verified.

Owner policy required before any cancel automation (see decision register).
"""

from __future__ import annotations

from typing import Any

STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_OBSERVED_UNMAPPED = "OBSERVED_UNMAPPED"
STATUS_NONE = "NONE"
STATUS_OK = "OK"

# Candidate Accurate keys — observation only; mapping to Eternal cancel is HOLD.
_CANCEL_BOOL_KEYS = (
    "cancelled",
    "canceled",
    "isCancelled",
    "isCanceled",
    "void",
    "voided",
    "isVoid",
)
_STATUS_KEYS = ("status", "statusName", "salesOrderStatus", "soStatus")


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_accurate_so_lifecycle_signals(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull candidate cancel/void/status fields from an Accurate SO payload.

    Returns observational metadata for raw_sync_meta — never raises for missing
    fields; never sets Eternal cancellation state.
    """
    if not isinstance(payload, dict):
        return {
            "lifecycle_signal_status": STATUS_INSUFFICIENT_DATA,
            "observed": {},
            "enforcement_active": False,
        }

    observed: dict[str, Any] = {}
    for key in _STATUS_KEYS:
        if key in payload and payload.get(key) is not None:
            observed[key] = _as_str(payload.get(key))
    for key in _CANCEL_BOOL_KEYS:
        if key in payload and payload.get(key) is not None:
            observed[key] = payload.get(key)

    # Nested status objects occasionally appear in Accurate payloads.
    status_obj = payload.get("status")
    if isinstance(status_obj, dict):
        name = _as_str(status_obj.get("name") or status_obj.get("statusName"))
        if name:
            observed["status.name"] = name

    if not observed:
        return {
            "lifecycle_signal_status": STATUS_NONE,
            "observed": {},
            "enforcement_active": False,
            "notes": [
                "No cancel/void/status candidate fields present on this Accurate payload.",
                "Eternal does not invent PO cancellation from missing Accurate fields.",
            ],
        }

    return {
        "lifecycle_signal_status": STATUS_OBSERVED_UNMAPPED,
        "observed": observed,
        "enforcement_active": False,
        "notes": [
            "Candidate Accurate lifecycle fields observed only.",
            "No Eternal cancel/void side effects — Owner mapping required.",
        ],
    }


def po_lifecycle_advisory(po_doc: dict[str, Any] | None) -> dict[str, Any]:
    """Informational projection of PO acceptance + any stored lifecycle signals."""
    from app.services.po_acceptance import acceptance_block, is_po_accepted

    header = (po_doc or {}).get("header") if isinstance(po_doc, dict) else {}
    header = header if isinstance(header, dict) else {}
    meta = header.get("raw_sync_meta") if isinstance(header.get("raw_sync_meta"), dict) else {}
    signals = meta.get("lifecycle_signals") if isinstance(meta.get("lifecycle_signals"), dict) else {}

    acceptance = acceptance_block(po_doc)
    return {
        "milestone": "M8_4_PO_CANCEL_VOID_ORDER_VERIFIED",
        "advisory_only": True,
        "mode": "INFORMATIONAL",
        "acceptance": acceptance,
        "order_verified_source": "operational.acceptance.status=accepted",
        "order_verified_completed": is_po_accepted(po_doc),
        "accurate_lifecycle_signals": signals or {
            "lifecycle_signal_status": STATUS_INSUFFICIENT_DATA,
            "observed": {},
            "enforcement_active": False,
            "notes": ["No lifecycle_signals on PO raw_sync_meta."],
        },
        "enforcement": {
            "auto_cancel_production": False,
            "auto_release_schedule": False,
            "auto_void_sj": False,
            "auto_reverse_delivery": False,
            "auto_reverse_inventory_cogs": False,
            "auto_delete_history": False,
            "order_verified_independent_status": False,
        },
        "policy_holds": [
            "Accurate SO cancel/void field mapping to Eternal lifecycle",
            "Side effects for cancel after acceptance/scheduling/production/loading/delivery",
            "Reimport / reopen / concurrent cancel+delivery semantics",
        ],
        "history_preservation": {
            "hard_delete_forbidden": True,
            "allocations": "retain",
            "loading_sj": "retain",
            "delivery_evidence": "retain",
            "tracking_events": "retain",
        },
    }
