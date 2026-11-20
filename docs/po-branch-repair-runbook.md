# Runbook: Remediasi `branch_id` PO (Owner-gated, fail-closed)

## Tujuan

Mengisi `branch_id` kanonis (`50` / `150`) hanya untuk kandidat sealed yang sudah diverifikasi Accurate detail.

## Precondition (semua wajib — script menolak jika gagal)

1. **Owner approval** tertulis untuk write production.
2. **Deploy** exact SHA yang memuat helper CAS `set_branch_id_if_empty_and_unaccepted`.
3. Live repo `/home/eternaladmin/eternal-production-v4` ada; `git rev-parse HEAD` == `--expect-live-sha` (40-hex). **Tanpa direktori live ⇒ refuse.**
4. `APP_ENV=production`, DB `eternal_v4`, Mongo **27018** saja (27017 ditolak).
5. Kill-switch `V4_PO_AUTO_SYNC_ENABLED=0`.
6. **Maintenance lock** JSON (`workers_cleared=true`, `po_auto_sync=false`, token ≥16, usia ≤4 jam). Tidak ada worker `accurate_auto_sync_worker.py --job po`.
7. **Backup meta** baru (DB `eternal_v4`, port 27018, `archive_sha256`, usia ≤24 jam; file archive dicocokkan jika path ada).
8. Manifest **sealed** dari verify (`sealed=true`, `manifest_sha256`, daftar kandidat). Checksum harus cocok `--expect-manifest-sha256` + `--expect-candidate-count`.
9. Snapshot opsional: `--expect-total-po` / `--expect-missing-po` harus cocok bila diisi.

## Verifikasi Accurate (read-only)

```bash
PYTHONPATH=backend:scripts python3 scripts/po_branch_verify_accurate_ro.py \
  --delta ops/po-repair/delta_missing_branch.json \
  --checkpoint /tmp/m3_verify_checkpoint.jsonl \
  --out /tmp/m3_verify_report.json \
  --sealed-out /tmp/valid_repair_candidates.sealed.json \
  --allow-production-readonly
```

- `identity_match` wajib True; gagal → `REVIEW_REQUIRED_IDENTITY` (bukan kandidat).
- Checkpoint hanya resume jika `delta_sha256` + freshness (≤12 jam) cocok.

## Eksekusi write (Owner saja)

```bash
# 1) Tanpa flag write → refused, audit tetap ditulis
PYTHONPATH=backend:scripts python3 scripts/po_branch_repair_apply.py \
  --candidates /tmp/valid_repair_candidates.sealed.json \
  --expect-live-sha <APPROVED_40_HEX_SHA> \
  --expect-manifest-sha256 <MANIFEST_SHA256> \
  --expect-candidate-count 347 \
  --backup-meta /path/backup_meta.json \
  --maintenance-lock /path/maintenance_lock.json \
  --expect-total-po 547 \
  --expect-missing-po 347 \
  --audit-out /tmp/repair_audit_dry.json

# 2) Execute
PYTHONPATH=backend:scripts python3 scripts/po_branch_repair_apply.py \
  --candidates /tmp/valid_repair_candidates.sealed.json \
  --expect-live-sha <APPROVED_40_HEX_SHA> \
  --expect-manifest-sha256 <MANIFEST_SHA256> \
  --expect-candidate-count 347 \
  --backup-meta /path/backup_meta.json \
  --maintenance-lock /path/maintenance_lock.json \
  --expect-total-po 547 \
  --expect-missing-po 347 \
  --audit-out /tmp/repair_audit.json \
  --execute \
  --i-understand-this-writes-production
```

## Perilaku keselamatan saat write

- Pre-check per PO: kosong branch, belum accepted, tanpa task/alokasi/SJ/jadwal, identitas/tanggal tidak drift, Master aktif.
- Write memakai CAS Mongo `set_branch_id_if_empty_and_unaccepted` (anti TOCTOU acceptance pada dokumen yang sama).
- Dependensi lintas koleksi **bukan** transaksi atomik → maintenance isolation wajib; bila pre-check/CAS gagal karena race ⇒ **STOP**, sisa kandidat `skipped_after_stop`, `ok=false`.
- `ok=true` hanya jika semua kandidat `filled` atau `already_has_branch`, tanpa blocked/failed/skipped.

## Post-write verification

1. `po_missing_branch` turun sesuai `filled`.
2. Sampling kandidat: `branch_id` == `expected_branch_id`.
3. Tidak ada accepted/deps yang terisi.
4. Kill-switch tetap OFF; simpan audit JSON + backup stamp.

## Rollback

- Jangan unset massal.
- Kasus salah isi: prosedur Owner per-`accurate_id` + audit.
- Restore dump hanya dengan approval disaster recovery.

## Dilarang

- Write tanpa sealed manifest / backup / maintenance lock / live SHA.
- Auto-sync PO selama repair.
- Mongo `27017` / ERP legacy / merge-deploy di luar SHA disetujui.
