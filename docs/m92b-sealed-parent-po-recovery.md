# M9.2B — Sealed 10-PO Historical Parent Recovery

**STATUS:** engineering only. Production APPLY / merge authorization is separate.

## Purpose

Recover exactly 10 Accurate Sales Orders that are parents of open
`po_not_registered` SJ reviews, blocked by global `ACCURATE_SYNC_START_DATE=2026-11-01`.

Global date floor is **not** changed. Ordinary PO auto-sync remains floor-gated.

## Batch

| Field | Value |
| --- | --- |
| Batch ID | `historical_parent_po_recovery_m92b` |
| Manifest | `backend/app/manifests/historical_parent_po_recovery_m92b.json` |
| Count | 10 |
| Tooling | `scripts/historical_po_recovery.py` / `historical_po_recovery.py` |

IDs (sorted): `62913,93693,93696,93728,93785,93788,93852,93871,93901,93907`

Excluded forever from this batch: SJ `120851`.

## Checksums

- `id_list_sha256` uses tooling `compute_id_list_sha256` (newline-joined IDs **with trailing newline**).
- M9.2A audit value `fea5ffc8…` used the same ID order **without** trailing newline — same set, different hash convention.

## CLI (DRY_RUN)

```bash
PYTHONPATH=backend python3 scripts/historical_po_recovery.py \
  --expect-batch-id historical_parent_po_recovery_m92b \
  --expect-po-count 10 \
  --expect-manifest-sha256 <FULL_SHA256_FROM_SEALED_MANIFEST> \
  --expect-id-list-sha256 <FULL_ID_LIST_SHA256_FROM_SEALED_MANIFEST>
```

APPLY requires separate Owner authorization, live SHA guard, backup, and dual confirm flags.