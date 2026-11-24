"""M8.3 — read-only advisory for machine capacity & urgent quota.

Fail-closed: never invent capacity defaults, quota formulas, or margin rules.
Never mutates schedules. Never enables enforcement without Owner-approved policy
and complete authoritative inputs.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.repositories import org_master_repository as org
from app.repositories import schedule_repository as schedules

STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_POLICY_NOT_CONFIGURED = "POLICY_NOT_CONFIGURED"
STATUS_OK = "OK"
STATUS_UNKNOWN = "UNKNOWN"

# Optional machine document fields — present only when Owner has populated them.
# Never hardcode historical 17_000 pcs/shift as a universal default.
OPTIONAL_OUTPUT_FIELD = "output_per_shift_pcs"
OPTIONAL_MACHINE_TYPE_FIELD = "machine_type"

# Urgent daily quota / sales distribution / margin floor are Owner policy —
# no approved config collection exists in V4 yet.
URGENT_POLICY_CONFIG_COLLECTION = "urgent_quota_policy"


def line_reservation_snapshot(
    db: Database,
    *,
    accurate_line_id: str,
    qty_target: float,
) -> dict[str, Any]:
    """Authoritative line-qty reservation (existing schedule SoT).

    Units: pcs on one PO item line. Released/cancelled allocations are excluded
    via status=active only.
    """
    reserved = float(schedules.active_allocated_qty(db, accurate_line_id))
    target = float(qty_target or 0)
    remaining = max(target - reserved, 0.0)
    utilization = (reserved / target) if target > 0 else None
    return {
        "status": STATUS_OK,
        "unit": "pcs",
        "qty_target": target,
        "reserved_capacity": reserved,
        "remaining_capacity": remaining,
        "projected_utilization": utilization,
        "enforcement_active": True,
        "authority": "production_allocations.status=active + line qty",
        "notes": [
            "Line reservation already enforced by scheduling_service.create_allocations.",
            "Released allocations are excluded from reserved_capacity.",
        ],
    }


def machine_capacity_snapshot(
    db: Database,
    *,
    branch_code: str | None = None,
    schedule_date: str | None = None,
    shift_code: str | None = None,
) -> dict[str, Any]:
    """Machine/shift capacity advisory — fail-closed to INSUFFICIENT_DATA.

    Does not hardcode Automatic≈17000. Does not treat monthly totals as daily capacity.
    Allocations today have no machine_code / shift_code binding, so reserved
    machine capacity cannot be attributed even when output_per_shift_pcs exists.
    """
    org.require_org_master_indexes(db)
    machines = org.list_docs(db, org.MACHINES)
    shifts = org.list_docs(db, org.SHIFTS)

    branch = (branch_code or "").strip().upper() or None
    if branch:
        machines = [
            m
            for m in machines
            if (str(m.get("branch_code") or "").strip().upper() or None) == branch
            or m.get("branch_code") in (None, "")
        ]

    active_machines = [m for m in machines if m.get("is_active", True)]
    active_shifts = [s for s in shifts if s.get("is_active", True)]

    gaps: list[str] = []
    if not active_machines:
        gaps.append("master_machines empty or no active machine for branch filter")
    if not active_shifts:
        gaps.append("master_shifts empty or no active shift")

    typed = 0
    with_output = 0
    available_sum = 0.0
    for m in active_machines:
        if str(m.get(OPTIONAL_MACHINE_TYPE_FIELD) or "").strip():
            typed += 1
        raw = m.get(OPTIONAL_OUTPUT_FIELD)
        if raw is None or raw == "":
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            gaps.append(
                f"machine {m.get('code')}: {OPTIONAL_OUTPUT_FIELD} not numeric"
            )
            continue
        if val <= 0:
            gaps.append(
                f"machine {m.get('code')}: {OPTIONAL_OUTPUT_FIELD} must be > 0"
            )
            continue
        with_output += 1
        available_sum += val

    if active_machines and with_output < len(active_machines):
        gaps.append(
            f"{len(active_machines) - with_output} active machine(s) missing "
            f"{OPTIONAL_OUTPUT_FIELD}"
        )
    if active_machines and typed < len(active_machines):
        gaps.append(
            f"{len(active_machines) - typed} active machine(s) missing "
            f"{OPTIONAL_MACHINE_TYPE_FIELD}"
        )

    # Schedules are operator-bound, not machine-bound — cannot deduct reserved.
    gaps.append(
        "production_allocations have no machine_code/shift_code; "
        "machine-level RESERVED_CAPACITY cannot be attributed"
    )
    if shift_code:
        gaps.append(
            "shift filter requested but allocations are not shift-bound; "
            "shift isolation for machine capacity is UNKNOWN"
        )
    if schedule_date:
        # Date is recorded on allocations but without machine binding it does not
        # yield per-machine remaining capacity.
        gaps.append(
            "schedule_date known on allocations but not linkable to machines"
        )

    status = STATUS_INSUFFICIENT_DATA
    return {
        "status": status,
        "unit": "pcs_per_shift",
        "available_capacity": None if gaps else available_sum,
        "reserved_capacity": None,
        "remaining_capacity": None,
        "projected_utilization": None,
        "requested_qty": None,
        "active_machine_count": len(active_machines),
        "active_shift_count": len(active_shifts),
        "machines_with_output": with_output,
        "machines_with_type": typed,
        "branch_code": branch,
        "schedule_date": schedule_date,
        "shift_code": (shift_code or "").strip().upper() or None,
        "enforcement_active": False,
        "authority": "master_machines + master_shifts (incomplete for capacity math)",
        "gaps": gaps,
        "notes": [
            "Historical Automatic≈17000 pcs/shift is reference-only — not applied.",
            "No invented capacity default. Scheduling writes remain operator-allocation only.",
        ],
    }


def urgent_quota_snapshot(
    db: Database,
    *,
    production_date: str | None = None,
    sales_owner: str | None = None,
    requested_urgent_qty: float | None = None,
) -> dict[str, Any]:
    """Urgent quota advisory — POLICY_NOT_CONFIGURED until Owner approves formula."""
    org.require_org_master_indexes(db)
    policy = db[URGENT_POLICY_CONFIG_COLLECTION].find_one({}, {"_id": 0})
    gaps: list[str] = []
    if policy is None:
        gaps.append(
            f"collection {URGENT_POLICY_CONFIG_COLLECTION} absent — "
            "no approved URGENT_DAILY_QUOTA / sales distribution config"
        )
    else:
        # Config may exist later; still refuse to invent distribution math.
        if policy.get("daily_quota_pcs") is None:
            gaps.append("daily_quota_pcs missing in urgent policy config")
        if not policy.get("distribution_formula_approved"):
            gaps.append("distribution_formula_approved not true — no enforcement")

    # Consumption cannot be computed without approved semantics.
    gaps.append(
        "urgent consumption ledger not defined; header.urgent is display/filter only"
    )
    if sales_owner is None or not str(sales_owner).strip():
        gaps.append("sales_owner empty or not provided for quota isolation check")

    return {
        "status": STATUS_POLICY_NOT_CONFIGURED,
        "unit": "pcs_per_day",
        "urgent_daily_quota": None,
        "urgent_already_allocated": None,
        "urgent_remaining": None,
        "requested_urgent_qty": requested_urgent_qty,
        "sales_owner": (sales_owner or "").strip() or None,
        "branch": None,
        "production_date": (production_date or "")[:10] or None,
        "enforcement_active": False,
        "authority": "none — historical 10000 pcs/day Owner direction not codified",
        "gaps": gaps,
        "notes": [
            "Do not infer urgency from deadline alone.",
            "header.urgent (Accurate) is KPI/filter only — not a quota engine.",
            "No default urgent approval actor invented.",
        ],
    }


def urgent_eligibility_snapshot(po_doc: dict[str, Any]) -> dict[str, Any]:
    """Eligibility advisory from authoritative PO fields only.

    Margin restriction (Rp55/pcs) requires an approved margin SoT — absent today.
    """
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    urgent_flag = bool(header.get("urgent") is True)
    seller = str(header.get("seller") or "").strip() or None
    customer = str(header.get("customer") or "").strip() or None

    # Margin is not on PoHeaderModel / Accurate normalize path.
    margin_gaps = [
        "no authoritative selling-margin field on PO header or line",
        "do not infer margin from unit price alone",
        "Rp55/pcs minimum-margin urgent restriction POLICY_REQUIRED",
    ]

    return {
        "status": STATUS_INSUFFICIENT_DATA if margin_gaps else STATUS_OK,
        "urgent_flag": urgent_flag,
        "urgent_flag_source": "header.urgent",
        "sales_owner": seller,
        "sales_owner_source": "header.seller",
        "customer": customer,
        "margin_pcs": None,
        "margin_source": None,
        "margin_status": STATUS_INSUFFICIENT_DATA,
        "eligible_for_urgent_priority": None,
        "enforcement_active": False,
        "gaps": margin_gaps,
        "notes": [
            "Urgent flag is informational until quota + margin policy are approved.",
            "No automatic conversion regular→urgent.",
        ],
    }


def build_capacity_advisory(
    db: Database,
    *,
    po_doc: dict[str, Any] | None = None,
    accurate_line_id: str | None = None,
    qty_target: float | None = None,
    branch_code: str | None = None,
    schedule_date: str | None = None,
    shift_code: str | None = None,
    requested_qty: float | None = None,
) -> dict[str, Any]:
    """Composite read-only advisory for M8.3 UI / ops review."""
    line_snap: dict[str, Any] | None = None
    if accurate_line_id and qty_target is not None:
        line_snap = line_reservation_snapshot(
            db, accurate_line_id=accurate_line_id, qty_target=qty_target
        )
        if requested_qty is not None:
            line_snap = {
                **line_snap,
                "requested_qty": float(requested_qty),
                "would_exceed_line": (
                    float(line_snap["reserved_capacity"]) + float(requested_qty)
                    > float(line_snap["qty_target"])
                ),
            }

    seller = None
    if po_doc is not None:
        header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
        seller = str(header.get("seller") or "").strip() or None

    machine = machine_capacity_snapshot(
        db,
        branch_code=branch_code,
        schedule_date=schedule_date,
        shift_code=shift_code,
    )
    urgent_quota = urgent_quota_snapshot(
        db,
        production_date=schedule_date,
        sales_owner=seller,
        requested_urgent_qty=requested_qty if (po_doc and _po_urgent(po_doc)) else None,
    )
    eligibility = (
        urgent_eligibility_snapshot(po_doc) if po_doc is not None else None
    )

    return {
        "milestone": "M8_3_URGENT_SCHEDULING_MACHINE_CAPACITY",
        "advisory_only": True,
        "enforcement": {
            "line_qty_reservation": True,
            "machine_shift_capacity": False,
            "urgent_quota": False,
            "urgent_margin_restriction": False,
            "auto_reschedule": False,
            "auto_urgent_override": False,
        },
        "line_reservation": line_snap,
        "machine_capacity": machine,
        "urgent_quota": urgent_quota,
        "urgent_eligibility": eligibility,
        "policy_holds": [
            "URGENT_DAILY_QUOTA value and reset rules",
            "sales-ownership quota distribution formula",
            "minimum-margin (Rp55/pcs) margin source of truth",
            "machine_type taxonomy + output_per_shift_pcs population",
            "allocation↔machine/shift binding before machine remaining capacity",
            "urgent approval workflow / actor",
        ],
    }


def _po_urgent(po_doc: dict[str, Any]) -> bool:
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    return bool(header.get("urgent") is True)


def assert_no_invented_machine_enforcement(advisory: dict[str, Any]) -> None:
    """Test helper: machine/urgent enforcement must stay off while inputs incomplete."""
    enf = advisory.get("enforcement") or {}
    assert enf.get("machine_shift_capacity") is False
    assert enf.get("urgent_quota") is False
    assert enf.get("urgent_margin_restriction") is False
    assert enf.get("auto_reschedule") is False
    mc = advisory.get("machine_capacity") or {}
    assert mc.get("enforcement_active") is False
    assert mc.get("status") in {
        STATUS_INSUFFICIENT_DATA,
        STATUS_POLICY_NOT_CONFIGURED,
        STATUS_UNKNOWN,
    }
