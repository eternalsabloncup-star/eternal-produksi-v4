# Runbook: Remediasi `branch_id` PO (Owner-gated)

## Tujuan

Mengisi `branch_id` kanonis (`50` / `150`) pada PO existing yang kosong, **hanya** untuk kandidat yang sudah diverifikasi Accurate detail.

## Precondition (semua wajib)

1. **Owner approval** tertulis untuk write production.
2. **Deploy** exact SHA main yang memuat PR #21+ (helper CAS `set_branch_id_if_empty_and_unaccepted`).
3. Production `git rev-parse HEAD` == SHA deploy yang disetujui.
4. Kill-switch: `V4_PO_AUTO_SYNC_ENABLED=0` (dan jangan aktifkan selama repair).
5. Tidak ada deploy/mongodump/repair lain yang berjalan.
6. Backup checkpoint baru (`mongodump` DB `eternal_v4` di `27018` saja) + SHA256 tercatat.
7. Manifest kandidat dari `po_branch_verify_accurate_ro.py` kategori `VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR`.
8. Snapshot consistency: total/delta/missing di Mongo cocok dengan manifest input.

## Cakupan

- Hanya `accurate_id` di manifest kandidat valid.
- Tidak menyentuh PO yang sudah ber-`branch_id`.
- Tidak mengubah acceptance, task, alokasi, SJ, payment, permission, index.

## Eksekusi (Owner saja)

```bash
# 1) Dry refuse check (harus refused tanpa flag write)
PYTHONPATH=backend python3 scripts/po_branch_repair_apply.py \
  --candidates /path/valid_candidates.json \
  --expect-live-sha <APPROVED_LIVE_SHA> \
  --audit-out /tmp/repair_audit_dry.json

# 2) Execute (hanya setelah approval)
PYTHONPATH=backend python3 scripts/po_branch_repair_apply.py \
  --candidates /path/valid_candidates.json \
  --expect-live-sha <APPROVED_LIVE_SHA> \
  --audit-out /tmp/repair_audit.json \
  --execute \
  --i-understand-this-writes-production
```

## Post-write verification

1. Hitung ulang: `po_missing_branch` turun sesuai `filled`.
2. Sampling 10 kandidat: `branch_id` == `expected_branch_id`.
3. Pastikan tidak ada PO accepted/deps yang terisi diam-diam (CAS harus miss).
4. Kill-switch tetap OFF.
5. Simpan audit JSON + backup stamp.

## Rollback

- Jangan unset massal.
- Jika salah isi terbukti: revert kasus per-`accurate_id` lewat prosedur Owner terpisah + audit.
- Restore dump hanya jika Owner menyetujui disaster recovery.

## Dilarang

- Auto-sync PO selama repair.
- Write tanpa manifest Accurate-verified.
- Menyentuh Mongo `27017` / ERP legacy.
- Merge/deploy di luar SHA yang disetujui.
