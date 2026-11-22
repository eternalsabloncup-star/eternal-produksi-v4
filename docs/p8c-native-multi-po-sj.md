# P8C — Native Multi-PO Surat Jalan

Milestone implementasi model native: **satu Accurate SJ ID → satu dokumen lokal**, dengan **N parent PO** terverifikasi per baris.

## Keputusan Owner

- Native multi-PO diizinkan.
- Setiap baris wajib `salesOrderId` + `salesOrderDetailId` Accurate.
- Singular `accurate_po_id` hanya untuk single-SO (legacy).
- Multi-PO **tidak** mengisi singular parent palsu.
- SJ `120851` tetap karantina identitas (SO/detail null).
- Tidak ada merge/deploy/APPLY production di milestone ini.

## Model persisted

| Field | Single-SO | Multi-PO |
| --- | --- | --- |
| `accurate_sj_id` | kanonis | kanonis |
| `accurate_po_ids` | `[po]` | sorted unique parents |
| `parent_associations` | `[{accurate_po_id, po_no}]` | N entries |
| `is_multi_po` | `false` | `true` |
| `accurate_po_id` / `po_no` | diisi | **di-$unset** saat upsert |
| `items[].accurate_po_id` | parent SO baris | parent SO baris |
| `items[].accurate_line_id` | `salesOrderDetailId` | sama |

## Ingest

1. `normalize_delivery_note` — native multi-PO (bukan `MULTI_SO_NOT_SUPPORTED`).
2. Semua parent harus ada di `accurate_sales_orders` — else `po_not_registered` deferred.
3. Identitas hilang/konflik → `DELIVERY_NOTE_IDENTITY` quarantine.
4. Review resolved hanya setelah ingest sukses.
5. Loading task: **satu per SJ** (`task:loading-sj:sj:{id}`); tidak konfirmasi Qty.
6. Tidak ada Accepted / Payment / Inventory / Delivery confirmation side-effect.

## Loading / Delivery

- Validasi Qty Packing per item memakai `items[].accurate_po_id`.
- Confirm/cancel Loading menyimpan/menggunakan parent per item.
- Event Loading/Delivery: singular `accurate_po_id` kosong untuk multi-PO; `accurate_po_ids` lengkap.
- Semua parent harus Accepted sebelum Loading/Delivery confirm.

## Indeks

Spec `idx_sj_po_ids` pada `accurate_po_ids` ada di source `REQUIRED_DELIVERY_INDEX_SPECS`.

**Production createIndex: BLOCKED** — butuh bootstrap Owner terpisah. Tidak dijalankan di P8C.

## Kompatibilitas

- Dokumen legacy singular tetap dibaca lewat `note_parent_po_ids` / `item_belongs_to_po`.
- Controlled SJ Replay P7D tetap single-SO allowlist; multi-PO → `reason=multi_so` conflict (di luar manifest sealed).
- Discovery seed tetap memilih single-SO saja; multi-PO dihitung di `multi_so_rejected`.

## Aktivasi production

Urutan milestone berikut (bukan P8C):

1. Seal + DRY_RUN historical parent PO recovery (lihat `docs/p8c-historical-parent-po-recovery.md`).
2. Owner APPLY parent PO (bukan SJ).
3. Controlled multi-PO SJ ingest untuk 6 kasus MULTI_SO (bukan 120851).
4. Index bootstrap bila diperlukan.
