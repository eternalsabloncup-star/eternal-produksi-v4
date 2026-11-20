# M4 — Unified Release SJ + Live Tracking Dokumen

**STATUS:** kode release-ready; **aktivasi production BELUM**.

## Prasyarat merge

1. PR M4 merged ke `main`.
2. Owner menyetujui **exact SHA** artifact yang akan di-deploy.
3. Fresh backup `eternal_v4` (port 27018).
4. Dual-flag masih OFF sampai langkah aktivasi:
   - `V4_SJ_SYNC_ENABLED=0`
   - `V4_SJ_SYNC_PRODUCTION_ENABLED` unset/0

## Deploy artifact (tanpa SJ sync)

1. Deploy exact SHA ke VPS.
2. Restart backend; `/api/health` OK.
3. Pastikan Live Tracking dokumen (invoice/receipt/design) terbaca dari data Accurate yang sudah ada.
4. Koreksi `active-release.txt` = exact SHA aktif (setelah validasi).

## Aktivasi SJ (hanya setelah Owner approve SHA)

1. Install unit (belum enable timer):
   - `deploy/eternal-v4-accurate-sj.service`
   - `deploy/eternal-v4-accurate-sj.timer`
   - `scripts/vps_accurate_sj_job_flock.sh` (chmod +x)
2. Set env:
   - `V4_SJ_SYNC_ENABLED=1`
   - `V4_SJ_SYNC_PRODUCTION_ENABLED=1`
3. `systemctl enable --now eternal-v4-accurate-sj.timer`
4. Monitor checkpoint `job=sj`, `accurate_delivery_notes` count, review_required.
5. Smoke Live Tracking: kartu Surat Jalan menampilkan SJ valid; superseded tidak aktif.
6. Kill-switch: unset flags + `systemctl disable --now eternal-v4-accurate-sj.timer`.

## Guard yang tidak boleh dilanggar

- Ingest SJ ≠ auto-Accepted PO.
- Ingest SJ ≠ auto selesai Loading.
- Ingest SJ ≠ auto Delivered.
- Multi-SO invoice tidak dibuka penuh di tracking publik.
