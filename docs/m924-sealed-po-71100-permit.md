# M9.24 — Sealed historical recovery permit for Accurate PO 71100

**STATUS:** engineering only. Production APPLY / merge / deploy **not** authorized by this document.

## Why

SJ `121064` is quarantined (`po_not_registered`) because parent Accurate PO `71100` (`PO/2026/02/16/000036`, `transDate` 2026-02-16) is before `ACCURATE_SYNC_START_DATE` / date_floor `2026-11-01`.  
Ordinary one-PO production sync correctly refuses the floor without a historical permit. Existing sealed batches do not list `71100`.

## What this adds

- New sealed batch: `historical_po_recovery_m924_po_71100` (exact 1 PO).
- Registry wiring in `historical_po_recovery.BATCH_REGISTRY`.
- Exact identity: ID `71100`, number `PO/2026/02/16/000036`, date `2026-02-16`, branch `50`, customer `36200`, related SJ `121064`.
- `require_po_no=True` — payload number must match sealed `po_no`.
- Floor bypass only via `issue_floor_permit` inside controlled `recover_one_po` / `run_historical_po_recovery`.

## What this does not do

- Does not change global date_floor.
- Does not auto-accept the PO.
- Does not recover SJ `121064` or `120851`.
- Does not create Loading SJ for an unaccepted PO.
- Does not alter SJ timer/flags/checkpoints.
- Does not modify P6/P8C sealed manifests.

## Future production APPLY (separate Owner OA)

```bash
PYTHONPATH=backend python3 scripts/historical_po_recovery.py \
  --expect-batch-id historical_po_recovery_m924_po_71100 \
  --expect-po-count 1 \
  --expect-manifest-sha256 <FULL_SHA256_FROM_SEALED_MANIFEST> \
  --expect-id-list-sha256 <FULL_ID_LIST_SHA256_FROM_SEALED_MANIFEST> \
  --apply --execute \
  --confirm-batch-id historical_po_recovery_m924_po_71100 \
  --i-understand-this-writes-production \
  --expect-live-sha <LIVE_SHA40>
```

Read hashes from the sealed JSON after checkout of the approved SHA. Do not guess.
