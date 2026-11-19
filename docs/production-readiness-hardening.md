# Eternal V4 — Production Readiness Hardening

**Baseline main:** `d32da70ef7e9c6eb65233a9a574241f25050e52f`

**PR #6 / #7:** sudah **merged** ke main (PO fields finalize + PWA Live Tracking).

Status verifikasi di lingkungan Cloud Agent vs VPS Owner:

| Area | Status |
| --- | --- |
| Source: no seed Master di request | **VERIFIED** (setelah hardening) |
| Source: no lazy createIndex di request | **VERIFIED** (setelah hardening) |
| Source: fail-closed Master / index | **VERIFIED** |
| Source: cache indeks isolasi host/port/db/client | **VERIFIED** |
| Source: `V4_SJ_SYNC_ENABLED` default OFF | **VERIFIED** |
| CI auto-deploy | **VERIFIED** tidak ada |
| Mongo V4 production 27018 | **BLOCKED** (akses VPS tidak tersedia di agent ini) |
| Backup / restore / rollback VPS | **BLOCKED** / **NOT VERIFIED** |
| Accurate production sync | **VERIFIED** tetap OFF / dilarang |

---

## Kebijakan request path

1. **Dilarang:** `seed_master_defaults` pada API/service request.
2. **Dilarang:** `createIndex` / `ensure_*_indexes` pada request path.
3. **Wajib:** `require_*_indexes` + `require_task_type_assigned_role` (fail-closed).
4. **Tanpa fallback role** (`or "operator"` dsb.) yang menyembunyikan Master kosong.

## Maintenance eksplisit (Owner)

```bash
# Di host yang punya kredensial Mongo target (bukan Cloud Agent sembarangan):
PYTHONPATH=backend python3 scripts/bootstrap_mongo.py --ensure-indexes
PYTHONPATH=backend python3 scripts/bootstrap_mongo.py --seed-master
```

Seed Master memakai `$setOnInsert` — **tidak menimpa** mapping Owner yang sudah diubah.

## Preflight indeks (read-only)

```bash
# Test 27019:
APP_ENV=test DB_NAME=eternal_v4_test_sj_activation \
PYTHONPATH=backend python3 scripts/mongo_index_preflight_readonly.py

# Production 27018 — HANYA setelah akses aman + flag eksplisit:
# (Owner di VPS) --allow-production-readonly
PYTHONPATH=backend python3 scripts/mongo_index_preflight_readonly.py \
  --allow-production-readonly
```

Skrip **tidak** menjalankan createIndex / seed / migrasi.

## Maintenance plan (TIDAK dieksekusi otomatis)

### A. Backup
1. Snapshot Mongo `eternal_v4` (27018) sebelum createIndex/seed/deploy.
2. Verifikasi artifact backup (ukuran, checksum, restore dry-run ke test).
3. Simpan tanggal + aktor + SHA aplikasi terkait.

### B. Indeks
1. Jalankan preflight read-only → daftar `missing` / `conflict`.
2. Cek kandidat duplikat sebelum unique (`accurate_sj_id`, dll.).
3. Setelah persetujuan Owner: `bootstrap_mongo.py --ensure-indexes` pada window maintenance.
4. Ulangi preflight hingga `ok=true`.

### C. Master
1. `bootstrap_mongo.py --seed-master` sekali (idempotent).
2. Verifikasi `assigned_role` + role-actions di UI Master.
3. Jangan seed saat startup/deploy/request.

### D. Rollback
1. Redeploy artifact commit sebelumnya (manual).
2. Restore DB hanya jika korupsi terbukti (dari backup terverifikasi).
3. Matikan flag Accurate bila anomali sync.

## Accurate

- Kill switch default **OFF**.
- Tidak ada scheduler/webhook production write.
- Production SJ sync memerlukan keputusan CEO terpisah.

## Instruksi Owner bila VPS belum diverifikasi

1. SSH ke VPS Eternal V4 (path runtime nyata, bukan tebakan).
2. Catat: hostname, branch/commit app aktif, unit systemd/docker, nginx site.
3. Pastikan proses V4 terisolasi dari ERP 27017.
4. Jalankan preflight Mongo 27018 read-only.
5. Laporkan disk, backup terakhir, prosedur restore — tanpa menyalin secret ke chat.
