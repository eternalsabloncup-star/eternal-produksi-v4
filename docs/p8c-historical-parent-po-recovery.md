# P8C — Historical Parent PO Recovery Design

Design only. **Production APPLY tidak diotorisasi** di P8C.

## Tujuan

Pulihkan parent Sales Order lokal yang hilang agar enam SJ MULTI_SO dapat di-ingest native multi-PO — **setelah** Owner menyegel manifest dan menyetujui APPLY terpisah.

## Sumber bukti (P8B)

| SJ | Parent SO (Accurate) | Lokal lengkap? |
| --- | --- | --- |
| 119068 | 89867, 93905, 94059 | tidak (0/3) |
| 119155 | 93319, 92201 | tidak (0/2) |
| 119721 | 93854, 93807, 93125 | sebagian (1/3; hilang 93807, 93125) |
| 119950 | 93084, 91580, 92300 | tidak (0/3) |
| 119979 | 94688, 94581 | ya |
| 120806 | 91902, 94106 | ya |
| 120851 | — (null) | **excluded forever** sampai Accurate perbaikan identitas |

### Kandidat parent hilang (deterministik dari audit)

```
89867, 91580, 92201, 92300, 93084, 93125, 93319, 93807, 93905, 94059
```

Count expected (setelah verifikasi Accurate read-only): **10**.

`trans_date` / `branch_id` / customer **belum** disegel di repo — wajib diisi dari Accurate detail read-only sebelum checksum manifest.

## Reuse infrastruktur P6

Jangan buat engine baru. Pakai:

- `backend/app/services/historical_po_recovery.py`
- `scripts/historical_po_recovery.py`
- Pola multi-guard APPLY + writer fence + durable audit
- Manifest sealed JSON + `manifest_sha256` + `po_count` + `batch_id`

Batch label usulan (belum sealed):

`historical_parent_po_recovery_p8c`

## Kontrak recovery (wajib)

1. Read-only Accurate SO identity verification  
2. Branch verification (50/150)  
3. Customer identity verification  
4. Item-line + quantity verification  
5. Existing PO detection → `already_present`  
6. Duplicate prevention  
7. Deterministic allowlist (hanya ID di atas / hasil audit ulang)  
8. Manifest checksum  
9. Explicit expected count  
10. Idempotent official PO ingest  
11. Shared writer fence (`po_repair_maintenance`)  
12. Durable audit  
13. Partial failure reconciliation  
14. DRY_RUN default (0 write)  
15. APPLY hanya dengan Owner approval exact (multi-flag CLI)  

## Sequencing (milestone berikutnya)

1. Accurate read-only audit → isi `trans_date` + `branch_id` per ID.  
2. Seal manifest + checksum + PR tooling (bila beda batch dari P6).  
3. DRY_RUN Owner.  
4. APPLY parent PO saja (bukan SJ).  
5. Baru kemudian controlled multi-PO SJ ingest (6 SJ; exclude 120851).  

**Update P10:** tooling multi-batch + sealed P8C manifest + controlled multi-PO SJ replay diimplementasikan di `docs/p10-sealed-p8c-parent-and-multi-po-sj.md`. Production APPLY tetap butuh otorisasi Owner terpisah.

## Di luar scope P8C

- Production APPLY  
- SJ replay  
- Perbaikan 120851  
- Master seed / index production  
- Arbitrary PO di luar daftar kandidat  

## Guard test (sudah ada / dipertegas)

- `test_historical_po_recovery.py` — DRY_RUN 0 write, APPLY tanpa execute ditolak  
- `test_p8c_native_multi_po.py::test_historical_recovery_apply_guard_and_dry_approval`  
