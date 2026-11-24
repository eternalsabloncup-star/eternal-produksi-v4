"""M8.3 — permanent read-only / informational capacity & urgent advisory.

Owner decision: advisory only. Do NOT introduce automatic machine-capacity,
urgent-quota, or margin-based scheduling enforcement. Missing inputs surface as
INSUFFICIENT_DATA or POLICY_NOT_CONFIGURED. Never invent capacity defaults.
Never mutates schedules.
"""

from __future__ import annotations

from typing import Any, Collection

from pymongo.database import Database

from app.repositories import org_master_repository as org
from app.repositories import schedule_repository as schedules

STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_POLICY_NOT_CONFIGURED = "POLICY_NOT_CONFIGURED"
STATUS_OK = "OK"
STATUS_UNKNOWN = "UNKNOWN"

# Optional machine document fields — present only when populated in Settings.
# Never hardcode historical 17_000 pcs/shift as a universal default.
OPTIONAL_OUTPUT_FIELD = "output_per_shift_pcs"
OPTIONAL_MACHINE_TYPE_FIELD = "machine_type"

# Optional urgent config collection — indicators only when present & complete.
URGENT_POLICY_CONFIG_COLLECTION = "urgent_quota_policy"


def line_reservation_snapshot(
    db: Database,
    *,
    accurate_line_id: str,
    qty_target: float,
) -> dict[str, Any]:
    """Authoritative line-qty reservation (existing schedule SoT).

    Units: pcs on one PO item line. Released/cancelled allocations are excluded
    via status=active only. This is the pre-existing scheduling validation —
    not a new M8.3 restriction.
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


def _machine_branch(m: dict[str, Any]) -> str | None:
    return (str(m.get("branch_code") or "").strip().upper() or None)


def machine_capacity_snapshot(
    db: Database,
    *,
    branch_code: str | None = None,
    schedule_date: str | None = None,
    shift_code: str | None = None,
    allowed_branch_codes: Collection[str] | None = None,
) -> dict[str, Any]:
    """Machine/shift capacity advisory — fail-closed to INSUFFICIENT_DATA.

    Does not hardcode Automatic≈17000. Does not treat monthly totals as daily capacity.
    Allocations today have no machine_code / shift_code binding, so reserved
    machine capacity cannot be attributed even when output_per_shift_pcs exists.

    ``allowed_branch_codes``:
      - None → elevated / all-branch caller; no branch ACL filter.
      - set → limited caller; only machines whose branch_code is in the set
        (unscoped machines with empty branch_code are excluded).
    """
    org.require_org_master_indexes(db)
    machines = org.list_docs(db, org.MACHINES)
    shifts = org.list_docs(db, org.SHIFTS)

    branch = (branch_code or "").strip().upper() or None
    allowed: set[str] | None = None
    if allowed_branch_codes is not None:
        allowed = {
            str(c).strip().upper()
            for c in allowed_branch_codes
            if str(c).strip()
        }

    if allowed is not None:
        machines = [m for m in machines if _machine_branch(m) in allowed]

    if branch:
        machines = [m for m in machines if _machine_branch(m) == branch]

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
        gaps.append(
            "schedule_date known on allocations but not linkable to machines"
        )

    return {
        "status": STATUS_INSUFFICIENT_DATA,
        "unit": "pcs_per_shift",
        "available_capacity": None,
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
        "branch_scope": sorted(allowed) if allowed is not None else "all",
        "enforcement_active": False,
        "authority": "master_machines + master_shifts (informational; incomplete for capacity math)",
        "gaps": gaps,
        "notes": [
            "Historical Automatic≈17000 pcs/shift is reference-only — not applied.",
            "M8.3 is advisory only — machine capacity does not block scheduling.",
        ],
    }


def urgent_quota_snapshot(
    db: Database,
    *,
    production_date: str | None = None,
    sales_owner: str | None = None,
    requested_urgent_qty: float | None = None,
) -> dict[str, Any]:
    """Urgent quota indicators — POLICY_NOT_CONFIGURED when config absent.

    Informational only. Owner rejected automatic urgent quota enforcement.
    """
    org.require_org_master_indexes(db)
    policy = db[URGENT_POLICY_CONFIG_COLLECTION].find_one({}, {"_id": 0})
    gaps: list[str] = []
    daily_quota: float | None = None
    if policy is None:
        gaps.append(
            f"collection {URGENT_POLICY_CONFIG_COLLECTION} absent — "
            "urgent quota indicators unavailable"
        )
    else:
        raw = policy.get("daily_quota_pcs")
        if raw is None or raw == "":
            gaps.append("daily_quota_pcs missing in urgent policy config")
        else:
            try:
                daily_quota = float(raw)
            except (TypeError, ValueError):
                gaps.append("daily_quota_pcs not numeric")
                daily_quota = None
        if not policy.get("distribution_formula_approved"):
            gaps.append(
                "distribution_formula_approved not true — "
                "per-sales remaining cannot be computed"
            )

    gaps.append(
        "urgent consumption ledger not defined; header.urgent is display/filter only"
    )
    if sales_owner is None or not str(sales_owner).strip():
        gaps.append("sales_owner empty or not provided for quota isolation display")

    status = (
        STATUS_POLICY_NOT_CONFIGURED
        if daily_quota is None or gaps
        else STATUS_OK
    )
    # Even if config appears complete, remaining stays None without a ledger.
    return {
        "status": STATUS_POLICY_NOT_CONFIGURED if daily_quota is None else status,
        "unit": "pcs_per_day",
        "urgent_daily_quota": daily_quota,
        "urgent_already_allocated": None,
        "urgent_remaining": None,
        "requested_urgent_qty": requested_urgent_qty,
        "sales_owner": (sales_owner or "").strip() or None,
        "branch": None,
        "production_date": (production_date or "")[:10] or None,
        "enforcement_active": False,
        "authority": "optional urgent_quota_policy (informational only)",
        "gaps": gaps,
        "notes": [
            "Do not infer urgency from deadline alone.",
            "header.urgent (Accurate) is KPI/filter only — not a quota engine.",
            "M8.3 does not enforce urgent quotas automatically.",
        ],
    }


def urgent_eligibility_snapshot(po_doc: dict[str, Any]) -> dict[str, Any]:
    """Eligibility indicators from authoritative PO fields only.

    Margin restriction is not applied — Owner rejected automatic margin-based
    scheduling limits. Missing margin → INSUFFICIENT_DATA display only.
    """
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    urgent_flag = bool(header.get("urgent") is True)
    seller = str(header.get("seller") or "").strip() or None
    customer = str(header.get("customer") or "").strip() or None

    margin_gaps = [
        "no authoritative selling-margin field on PO header or line",
        "do not infer margin from unit price alone",
        "margin-based scheduling restriction is not part of M8.3 (advisory only)",
    ]

    return {
        "status": STATUS_INSUFFICIENT_DATA,
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
            "Urgent flag is informational; M8.3 does not auto-convert or override.",
            "No automatic margin-based scheduling restriction.",
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
    allowed_branch_codes: Collection[str] | None = None,
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
        allowed_branch_codes=allowed_branch_codes,
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
        "mode": "INFORMATIONAL",
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
        # Data limitations for display — not Owner decisions pending activation.
        "data_limitations": [
            "machine remaining capacity needs machine/shift binding on allocations",
            "urgent remaining needs optional urgent_quota_policy + consumption ledger",
            "margin indicator needs an authoritative margin field (display only)",
        ],
    }


def _po_urgent(po_doc: dict[str, Any]) -> bool:
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    return bool(header.get("urgent") is True)


def assert_no_invented_machine_enforcement(advisory: dict[str, Any]) -> None:
    """Test helper: machine/urgent/margin/auto-reschedule enforcement stay off."""
    enf = advisory.get("enforcement") or {}
    assert advisory.get("advisory_only") is True
    assert advisory.get("mode") == "INFORMATIONAL"
    assert enf.get("machine_shift_capacity") is False
    assert enf.get("urgent_quota") is False
    assert enf.get("urgent_margin_restriction") is False
    assert enf.get("auto_reschedule") is False
    assert enf.get("auto_urgent_override") is False
    mc = advisory.get("machine_capacity") or {}
    assert mc.get("enforcement_active") is False
    uq = advisory.get("urgent_quota") or {}
    assert uq.get("enforcement_active") is False
    assert mc.get("status") in {
        STATUS_INSUFFICIENT_DATA,
        STATUS_POLICY_NOT_CONFIGURED,
        STATUS_UNKNOWN,
    }
    assert "policy_holds" not in advisory
