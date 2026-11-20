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

1. Validasi **SHA40 penuh** approved = HEAD (prefix pendek ditolak).
2. Wajib `--pr26-merged-ref` = SHA40 merge commit PR #26 yang **ancestor** HEAD
   (canonical: `cff64838d62ef233fd7c02f73b6ca403913d3f26`).
3. Verifikasi guard Loading **dan** Delivery di tree (`_require_accepted_po_for_note`
   + call sites) — bukan pencarian simbol semata.
4. Pastikan regression Accepted ditandai PASS (`--guard-regression-ok`).
5. Pastikan indeks Master sudah tersedia (tidak createIndex di bootstrap M5).
6. Konfirmasi backup Master/grants/config/DB (`--backup-ok`).
7. Konfirmasi tidak ada deploy/repair lain (`--no-other-deploy`).
8. Bootstrap idempotent `loading-sj` (+ grants Helper/KP).
9. **Permission reconciliation terbatas** (hanya `production-head`/`loading-sj`):
   - Grant benar → NO_OP
   - Grant legacy `view,edit,cancel` → tambah `confirm` (CAS atomic) → RECONCILED
   - Grant custom/ambigu → HOLD (tidak overwrite)
10. Verifikasi Master + least privilege (kontrak penuh).
11. Upsert `V4_LOADING_SJ_TASK_ENABLED=1` ke `backend.env`.
12. **Restart backend wajib** (`--restart-cmd`) — bukan opsional.
13. **Probe backend berjalan** `GET /api/health` — `status=ok`, `mongodb`,
    `features.loading_sj_task_enabled=true`. Env proses script **bukan** bukti.
14. Baru boleh `activated=True`. Kegagalan probe/restart → HOLD (bukan SUCCESS palsu).
15. Simpan audit JSON keluaran script (before/after reconciliation).

Reconciliation **hanya** di approved release path (`activate_loading_sj_release`).
Tidak di startup, request API, atau import modul.

## Bootstrap saja (tanpa flag)

```bash
PYTHONPATH=backend python3 scripts/bootstrap_loading_sj.py \
  --expected-db-name <DB>
```

- Target DB eksplisit; mismatch → tolak.
- Production → `--allow-production` (hanya dengan approval).
- Material mismatch existing → STOP tanpa overwrite.

## HOLD conditions

- approved_sha bukan SHA40 penuh / ≠ HEAD
- Merge commit PR #26 bukan ancestor HEAD / tidak SHA40
- Guard Loading/Delivery belum efektif di tree
- Master/grant invalid atau terlalu luas (termasuk grant KP custom ≠ legacy)
- Reconciliation CAS miss / concurrent modification
- Restart backend hilang/gagal
- Probe backend berjalan gagal / flag masih OFF di proses berjalan
- Index Master hilang
- Backup / deploy isolation / health gagal

Jangan set flag ON untuk melewati gate. Env script ≠ aktivasi efektif.

## Coding phase

M5 coding = PR + CI saja. **NO** production Master write, **NO** merge,
**NO** deploy, **NO** flag production ON tanpa Owner.
