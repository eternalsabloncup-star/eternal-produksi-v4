# Accurate Auto-Sync (Complete) — Eternal V4

## Arsitektur

- **Worker one-shot:** `scripts/accurate_auto_sync_worker.py --job po|invoice|receipt|sj|all`
- Bukan loop di proses FastAPI / uvicorn lifespan
- Interval target: **60 detik** via scheduler eksternal
- Tidak ada tombol “Sync Sekarang”

## Kill switch (default OFF)

| Flag | Default |
| --- | --- |
| `V4_PO_AUTO_SYNC_ENABLED` | `false` |
| `V4_INVOICE_AUTO_SYNC_ENABLED` | `false` |
| `V4_RECEIPT_AUTO_SYNC_ENABLED` | `false` |
| `V4_SJ_SYNC_ENABLED` | `false` |
| `V4_ACCURATE_AUTO_SYNC_INTERVAL_SECONDS` | `60` |

Saat OFF: **zero** request Accurate, **zero** write Mongo (tanpa lock/status mutation).

## Kontrak Accurate (terverifikasi live read-only)

| Dokumen | Path |
| --- | --- |
| Sales Order | `/accurate/api/sales-order/list.do` · `detail.do` |
| Sales Invoice | `/accurate/api/sales-invoice/list.do` · `detail.do` |
| Sales Receipt | `/accurate/api/sales-receipt/list.do` · `detail.do` |
| Delivery Order | `/accurate/api/delivery-order/list.do` · `detail.do` |

Relasi:

- Invoice → PO: `detailItem[].salesOrderId` (header `salesOrder` sering boolean — diabaikan)
- Receipt → Invoice: `detailInvoice[].invoiceId` + `paymentAmount`
- Receipt → PO: via snapshot invoice V4 (invoice sync harus jalan dulu)

### Shared list discovery (PO / SJ / Invoice / Receipt)

Live GET 2026-11-20 (VPS read-only; ids/`lastUpdate` only):

| Endpoint | `id\|asc` multipage | `lastUpdate\|desc` |
| --- | --- | --- |
| Sales Order | verified (prior) | verified (prior) |
| Delivery Order | **PASS** contiguous | **PASS** with `fields=id,lastUpdate` |
| Sales Invoice | **PASS** contiguous | **PASS** with `fields=id,lastUpdate` |
| Sales Receipt | **PASS** contiguous | **PASS** with `fields=id,lastUpdate` |

Default list rows may return only `id`; request `fields=id,lastUpdate` for timestamps. Sort differs from `id|desc`.

Shared engine (`discovery.py` + `checkpoint.py`):

| Param | Behavior |
| --- | --- |
| `sp.page` + `sp.pageSize` | Contiguous pages |
| `sp.sort=id\|asc` | Backfill; fail-closed if not monotonic |
| `sp.sort=lastUpdate\|desc` | Incremental crawl + page-1 hot refresh; fail-closed if timestamps not desc |
| `fields=id,lastUpdate` | Required for lastUpdate visibility |
| `filter.id.op=GREATER_*` | Do not use (SO live: silently ignored) |

Per-job checkpoint (`po` / `sj` / `invoice` / `receipt`):

- `backfill_complete`, `incremental_crawl_complete`, `incremental_next_page`, `incremental_cycles_completed`
- `incremental_pending` = backfill done but crawl unfinished / never completed one pass
- result `complete` = backfill + ≥1 incremental pass + zero failures (**not** “all Accurate data current this cycle” if pending)
- Advance only after full page success; mid-page failure keeps the page
- Same-cycle ID dedupe; no FastAPI background loop

## Index (manual maintenance)

- `accurate_auto_sync_status.uniq_auto_sync_job`
- `accurate_sales_invoices.uniq_accurate_invoice_id` (+ `idx_invoice_po_ids`)
- payment indexes existing (`uniq_accurate_receipt_id`, `idx_receipt_po`)

Jangan createIndex dari request/worker production.

## Gate

PO Accepted + Design open + Payment open → Scheduling.

Payment gate: evaluasi existing (positif + satu faktur → open; refund/multi-faktur/unmapped → REVIEW).
Tidak ada threshold pembayaran baru.