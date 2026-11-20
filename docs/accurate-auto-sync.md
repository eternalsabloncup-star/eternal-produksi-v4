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

## Index (manual maintenance)

- `accurate_auto_sync_status.uniq_auto_sync_job`
- `accurate_sales_invoices.uniq_accurate_invoice_id` (+ `idx_invoice_po_ids`)
- payment indexes existing (`uniq_accurate_receipt_id`, `idx_receipt_po`)

Jangan createIndex dari request/worker production.

## Gate

PO Accepted + Design open + Payment open → Scheduling.

Payment gate: evaluasi existing (positif + satu faktur → open; refund/multi-faktur/unmapped → REVIEW).
Tidak ada threshold pembayaran baru.