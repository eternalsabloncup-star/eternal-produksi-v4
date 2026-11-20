# Loading SJ — Release Activation Procedure (M5)

Dokumen ini mengikat **bootstrap Master `loading-sj`** dan **penyalakan
`V4_LOADING_SJ_TASK_ENABLED=1`** ke satu prosedur deployment yang disetujui Owner.

Tidak ada aktivasi manual kedua setelah deploy. Tidak dijalankan dari coding
tanpa approval. Default kode tetap OFF.

## Kontrak Master (Owner)

| Field | Nilai |
| --- | --- |
| task_type | `loading-sj` |
| label | `Loading Surat Jalan` |
| scope | `delivery_note` |
| assigned_role | `helper` |

### Permission matrix

| Role | view | confirm | edit | cancel |
| --- | --- | --- | --- | --- |
| Helper | ALLOW | ALLOW | ALLOW | DENY |
| Production Head | ALLOW | ALLOW (wajib) | ALLOW | ALLOW |
| Driver | tidak ada grant loading-sj otomatis | — | — | — |
| Admin / Director / SA | pertahankan akses existing; tanpa grant tambahan M5 | — | — | — |

Aksi Loading SJ hanya **Sesuai** / **Tidak Sesuai** (Qty SJ Accurate vs Qty Packing per item).
Tidak ada input qty Loading manual. `Tidak Sesuai` (Helper atau KP) membuat/mempertahankan
tugas selisih untuk KP — tidak auto-selesai, tidak membuka delivery sendiri.

## Dependency wajib

1. PR #24 Production Flow (Loading SJ workflow) sudah di main.
2. **PR #26 Accepted guard** (Loading + Delivery) sudah **merged** ke target SHA.
3. Regression guard Accepted PASS.
4. Guard efektif di source target sebelum flag ON.

Jika PR #26 belum merged / guard belum di target → **ACTIVATION = HOLD**.

## Urutan aktivasi (otomatis dalam satu prosedur)

Script: `scripts/activate_loading_sj_release.py`

1. Validasi exact approved SHA = HEAD deploy.
2. Pastikan PR #26 merged (simbol `_require_accepted_po_for_note` + call sites).
3. Pastikan guard ada di `delivery_service` / mapping API.
4. Pastikan regression Accepted ditandai PASS (`--guard-regression-ok`).
5. Pastikan indeks Master sudah tersedia (tidak createIndex di bootstrap M5).
6. Konfirmasi backup Master/grants/config/DB (`--backup-ok`).
7. Konfirmasi tidak ada deploy/repair lain (`--no-other-deploy`).
8. Bootstrap idempotent `loading-sj` (+ grants Helper/KP).
9. Verifikasi Master + least privilege.
10. Upsert `V4_LOADING_SJ_TASK_ENABLED=1` ke `backend.env`.
11. Restart backend terkendali (ops runbook VPS; callback opsional).
12. Verifikasi Settings efektif ON.
13. Verifikasi health backend/Mongo.
14. Simpan audit JSON keluaran script.

## Bootstrap saja (tanpa flag)

```bash
PYTHONPATH=backend python3 scripts/bootstrap_loading_sj.py \
  --expected-db-name <DB>
```

- Target DB eksplisit; mismatch → tolak.
- Production → `--allow-production` (hanya dengan approval).
- Material mismatch existing → STOP tanpa overwrite.

## HOLD conditions

- PR #26 belum merged / guard hilang di target SHA
- Master/grant invalid atau terlalu luas
- Index Master hilang
- Backup / deploy isolation / health gagal
- SHA tidak cocok

Jangan set flag ON untuk melewati gate.

## Coding phase

M5 coding = PR + CI saja. **NO** production Master write, **NO** merge,
**NO** deploy, **NO** flag production ON tanpa Owner.
