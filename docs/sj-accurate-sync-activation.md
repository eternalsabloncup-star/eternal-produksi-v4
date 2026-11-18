# Rencana Aktivasi Sinkronisasi Surat Jalan Accurate → Eternal V4

Dokumen ini memisahkan **kesiapan kode**, **bukti non-production**, dan **aktivasi production**.
Tidak ada langkah yang mengizinkan penulisan ke Accurate Online.

---

## 1. Status kode (PR #6)

| Komponen | Perilaku |
| --- | --- |
| `AccurateReadClient` | GET `delivery-order/list.do` + `detail.do` saja |
| `normalize_delivery_note` | Fail-closed; multi-SO → `MULTI_SO_NOT_SUPPORTED` |
| `delivery_sync` | Pagination, lock, skip PO belum terdaftar, partial failure |
| Kill switch | `V4_SJ_SYNC_ENABLED` default **OFF** |
| Mongo guard | `assert_safe_mongo_sync_target` — blokir `eternal_v4`, `eternal_produksi_v4`, dll. |
| API | `POST /api/delivery/sync/accurate` butuh `operations.full` + CSRF + kill switch + guard test |
| Auto-sync / webhook | **Tidak ada** |

---

## 2. Bukti isolasi non-production (wajib sebelum sync)

Jangan mengklaim aman hanya dari hostname. Verifikasi:

1. `DB_NAME` / `MONGODB_DB` diawali `eternal_v4_test_`
2. `APP_ENV` + URI lolos `assert_safe_mongo_sync_target`
3. `authSource` sesuai user test (bila kontrak VPS dipakai)
4. Host Accurate **bukan** `*.test`
5. Pin DB numerik (`V4_ACCURATE_DB_ID`) bila `V4_ACCURATE_REQUIRE_DB_PIN=1`

Perintah audit (tanpa sync):

```bash
PYTHONPATH=backend python3 scripts/verify_sj_sync_isolation.py
```

Sync manual non-prod (hanya setelah audit `live_sync_ready=true`):

```bash
export V4_SJ_SYNC_ENABLED=1
PYTHONPATH=backend python3 scripts/accurate_sj_sync_nonprod.py --max-pages 3 --max-rows 60
```

---

## 3. Checklist uji non-prod (Owner / VPS)

| Uji | Kriteria PASS |
| --- | --- |
| Sync pertama | `created`/`skipped` tercatat; tanpa error fatal list |
| Sync berulang | `unchanged` naik; tidak ada duplikat `accurate_sj_id` |
| Pagination | `pages` > 1 bila listing > `page_size` |
| Revisi | ubah detail di Accurate → `updated` + versi SJ naik (Opsi B) |
| Error parsial | satu SJ gagal tidak menghapus SJ lain |
| Isolasi Mongo | sync ke `eternal_v4` / `eternal_produksi_v4` ditolak |
| Isolasi Accurate | tidak ada panggilan `save.do` / `delete.do` / `bulk-save.do` |
| Kill switch | `V4_SJ_SYNC_ENABLED=0` → sync ditolak |

PO terkait harus sudah ada di DB test (sync SO terpisah) atau SJ akan `skipped` (`po_not_registered`).

---

## 4. Rencana aktivasi production (keputusan CEO)

### 4.1 Prasyarat

- [ ] Non-prod live sync PASS tercatat (tanggal, aktor, counters)
- [ ] Backup Mongo production (`eternal_v4`) sebelum aktivasi
- [ ] Index Loading/SJ diselaraskan (lihat catatan migrasi index di PR)
- [ ] Role dengan `operations.full` dibatasi
- [ ] Kill switch default OFF di environment production sampai jam-H

### 4.2 Konfigurasi production (usulan)

- Jalur **terpisah** dari test: jangan memakai `mongo_guard=test` untuk ops.
- Hanya setelah CEO: implementasi eksplisit `assert_safe_mongo_production_sync_target` + flag terpisah (mis. `V4_SJ_SYNC_PRODUCTION_ENABLED`) — **belum diaktifkan di kode tahap ini**.
- Monitoring: counters `created/updated/failed`, durasi, lock contention; alert bila `failed` > ambang.
- Kill switch: matikan `V4_SJ_SYNC_ENABLED` / flag prod segera bila anomali.
- Rollback: matikan flag; data SJ tetap; tidak menghapus bukti Loading/pengiriman; restore backup hanya bila korupsi terbukti.

### 4.3 Yang tetap dilarang tanpa keputusan baru

- Auto-scheduler / polling background
- Webhook Accurate production
- Soft-delete massal SJ yang absen dari listing
- Menyamakan Qty Loading otomatis dengan Qty SJ

---

## 5. Audit webhook Accurate

| Pertanyaan | Temuan |
| --- | --- |
| Apakah V4 punya endpoint webhook SJ? | **Tidak** |
| Apakah sync manual bergantung webhook? | **Tidak** — pull GET list/detail |
| Kapan webhook boleh dipertimbangkan? | Setelah sync manual non-prod **dan** prod stabil, plus auth signature webhook, idempotency, rate limit, dan kill switch |
| Status sekarang | **Dilarang diaktifkan** |

---

## 6. Keputusan yang dibutuhkan CEO / Owner

1. Provisioning kredensial Accurate **live** ke lingkungan yang menjalankan sync non-prod (bukan stub `*.test`).
2. Penunjukan DB test resmi (`eternal_v4_test_*`) + user Mongo dengan authSource yang benar.
3. Persetujuan menyalakan `V4_SJ_SYNC_ENABLED` di non-prod untuk uji live.
4. Setelah bukti non-prod: persetujuan desain jalur production sync (flag terpisah, backup, window).
5. Semantik `charField3` dan kebijakan multi-SO / SJ absen.
