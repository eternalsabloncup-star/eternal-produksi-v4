# Historical PO Recovery Allowlist (`historical_po_recovery_20261121`)

Milestone P3 — implementasi + test + PR. **Belum** production ingest.

## Kontrak

- Global date floor tetap `2026-11-01`.
- Hanya 80 Accurate PO ID di manifest sealed yang boleh exception floor.
- Default CLI: **DRY_RUN** (0 writes).
- APPLY production: multi-guard (`--apply --execute --confirm-batch-id --i-understand-this-writes-production --expect-live-sha` + checksum + count). Bukan satu env var.
- Tidak terhubung timer / startup / API publik.
- Tidak menerima arbitrary PO ID di luar allowlist.

## Manifest

- Path: `backend/app/manifests/historical_po_recovery_20261121.json`
- Label/batch: `historical_po_recovery_20261121`
- Sumber audit: `sj-missing-po-audit.md` (P2)

## CLI

```bash
PYTHONPATH=backend python3 scripts/historical_po_recovery.py \
  --expect-manifest-sha256 <sha256> \
  --expect-po-count 80 \
  --expect-batch-id historical_po_recovery_20261121
```

APPLY production (Owner milestone berikutnya — jangan jalankan tanpa approval):

```bash
PYTHONPATH=backend python3 scripts/historical_po_recovery.py \
  --apply --execute \
  --confirm-batch-id historical_po_recovery_20261121 \
  --i-understand-this-writes-production \
  --expect-live-sha <sha40> \
  --expect-manifest-sha256 <sha256> \
  --expect-po-count 80
```

## Di luar scope

- SJ replay / oneshot / timer
- Merge/deploy tanpa Owner
- Perubahan Accepted / Payment / stok / permission
