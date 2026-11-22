# P10 — Sealed P8C Parent Recovery + Controlled Native Multi-PO SJ Apply Tooling

**STATUS:** engineering only. Production APPLY / deploy / merge **tidak** diotorisasi di dokumen ini.

## Architecture

### Parent PO recovery (reuse P6 engine)

- Module: `backend/app/services/historical_po_recovery.py`
- CLI: `scripts/historical_po_recovery.py`
- Audit: `historical_po_recovery_*` collections (dibedakan oleh `batch_id`)

**Batch registry (sealed only):**

| Batch ID | Count | Manifest |
| --- | --- | --- |
| `historical_po_recovery_20261121` | 80 | `backend/app/manifests/historical_po_recovery_20261121.json` |
| `historical_parent_po_recovery_p8c` | 10 | `backend/app/manifests/historical_parent_po_recovery_p8c.json` |

P6 behavior, checksum rules, and CLI defaults remain unchanged. P8C requires `--expect-id-list-sha256`.

### Native multi-PO SJ replay (new batch; P7D untouched)

- Module: `backend/app/services/controlled_multi_po_sj_replay.py`
- CLI: `scripts/controlled_multi_po_sj_replay.py`
- Audit: reuse `controlled_sj_replay_*` collections with **different** `batch_id`
- Batch: `controlled_multi_po_sj_replay_p10_20261123` (exact 6 SJ)
- Manifest: `backend/app/manifests/controlled_multi_po_sj_replay_p10_20261123.json`
- Excluded forever: `120851`

P7D batch `controlled_sj_replay_20261122` (150 single-SO) is **not** modified.

## Official CLI contract

### Parent PO — DRY_RUN (default)

```bash
PYTHONPATH=backend python3 scripts/historical_po_recovery.py \
  --expect-batch-id historical_parent_po_recovery_p8c \
  --expect-po-count 10 \
  --expect-manifest-sha256 <FULL_SHA256_FROM_SEALED_MANIFEST> \
  --expect-id-list-sha256 <FULL_ID_LIST_SHA256_FROM_SEALED_MANIFEST>
```

### Parent PO — APPLY (future Owner-only; multi-guard)

Requires `--apply --execute --confirm-batch-id … --i-understand-this-writes-production --expect-live-sha <LIVE_SHA40>`.

### Multi-PO SJ — DRY_RUN

```bash
PYTHONPATH=backend python3 scripts/controlled_multi_po_sj_replay.py \
  --expect-batch-id controlled_multi_po_sj_replay_p10_20261123 \
  --expect-sj-count 6 \
  --expect-manifest-sha256 <FULL_SHA256> \
  --expect-id-list-sha256 <FULL_ID_LIST_SHA256>
```

Do **not** paste guessed hashes. Read sealed JSON fields after checkout of the approved SHA.

## Manifest provenance

### P8C parents (10)

Source: P9 Accurate read-only seal probe (`accurate_ro_seal_probe.json`).

IDs (deterministic order):

`89867,91580,92201,92300,93084,93125,93319,93807,93905,94059`

- `id_list_sha256` = SHA256 of newline-joined IDs (same convention as SJ replay).
- P9 comma-join fingerprint retained in provenance only: `caafffe91513a018dbf2f2de3b107e03cfcbe4646286cd58e6f9b1cffc96e1b6`.
- Full `manifest_sha256` recomputed after enrichment (customer / related SJ / line hash) — not equal to the P9 pre-seal candidate `55839b37…`.

### Six SJ

IDs: `119068,119155,119721,119950,119979,120806`  
Excluded: `120851`

## Future production APPLY prerequisites

1. Owner merge of this PR.
2. Deploy exact merge SHA (separate milestone).
3. Fresh verified backup.
4. Exact LIVE SHA guard match.
5. No competing writer / healthy fence.
6. Accurate RO revalidation immediately before APPLY.
7. Parent APPLY before SJ APPLY for dependents.
8. Explicit Owner production APPLY authorization (this doc is **not** that).

### Index / audit metadata

- Production already has `idx_sj_po_ids` (P9).
- Audit index bootstrap remains test-only helpers; production APPLY fail-closed if indexes missing — do **not** create indexes in this engineering milestone.

## Recovery execution order

1. Parent PO DRY_RUN (P8C batch)
2. Parent PO APPLY (authorized future)
3. Parent reconciliation
4. Six-SJ DRY_RUN
5. Six-SJ APPLY (authorized future)
6. Review resolution + Loading ensure (canonical side effects only)
7. Final reconciliation

## Failure / partial completion

- CONFLICT / identity mismatch / lease loss / audit failure → STOP remaining sequence.
- No automatic rollback.
- No blind retry of committed writes.
- Release only this run’s lease.
- Incomplete audit → do not report SUCCESS.

## Idempotency

- Parent: `already_present` if official Accurate identity already ingested.
- SJ: one canonical doc per Accurate SJ; repeated APPLY → `already_present` + review/loading ensure without duplicate SJ/Loading confirmation.

## Post-APPLY reconciliation rules

- Exact parent associations and item attribution.
- Qty confirmed remains 0 for newly ensured Loading tasks.
- No Delivery confirmation.
- Accepted / Payment / Inventory unchanged by this tooling.
- P6/P7D historical audit records untouched.
