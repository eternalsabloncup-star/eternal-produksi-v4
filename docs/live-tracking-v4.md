# Eternal Live Tracking V4 — Laporan Modul

**STATUS:** PR only — NO MERGE — NO DEPLOY — NO PRODUCTION DB MUTATION

## A. Audit existing & kontrak backend

| Area | Temuan |
|------|--------|
| `main` | Foundation + auth + Accurate Inbox PO. Tidak ada workflow/delivery/PWA. |
| PR #6 (open) | Gates, tasks, scheduling, **dan** delivery (`accurate_delivery_notes`, `loading_events`, `delivery_events`, `evidence_files`). |
| Auth pattern | Session CSPRNG + SHA-256 hash, CSRF, rate limit — **direuse** untuk tracking link. |
| Integrasi PR #6 | Adapter baca-saja koleksi kontrak PR #6 **tanpa mengimpor/menyalin** modulnya. `order_complete` memakai Qty diterima sah (`delivery_events=berhasil` + bukti aktif), bukan Qty SJ semata. |

## B. Desain

- Customer: `/t/{customer-slug}/{po-slug}/{token}` — mobile-first, brand Eternal, 4 tab (Ringkasan / Timeline / Dokumen / Bantuan).
- Internal: `/app/tracking` — Generate / Copy / Revoke / Regenerate / status link.
- PWA scoped: `public/tracking/*` + SW `scope: /t/` (tidak mengambil alih `/app`).

## C. File & fitur

**Backend**

- `domain/tracking_milestones.py` — 15 milestone + slug helper
- `repositories/tracking_link_repository.py` — token hash, revoke, expiry
- `services/tracking_workflow_adapter.py` — kontrak opsional PR #6
- `services/tracking_progress.py` — evaluasi milestone + progres Qty terpisah
- `services/tracking_service.py` — public/internal payload + dokumen berizin
- `api/tracking.py` — public + internal endpoints, rate limit, Cache-Control no-store

**Frontend**

- `pages/tracking/LiveTrackingPage.tsx`
- `pages/tracking/TrackingAdminPage.tsx`
- `api/tracking.ts`, `styles/tracking.css`
- `public/tracking/{manifest,sw,offline,icons}`

## D. Mapping 15 milestone → event backend

| # | Milestone | Sumber | Catatan gap |
|---|-----------|--------|-------------|
| 01 | Pesanan Diterima | `accurate_sales_orders` created/synced | OK di main |
| 02 | Verifikasi Pesanan | — | **Gap:** belum ada event verifikasi → Pending |
| 03 | Persiapan Desain | design_submissions / task `design` | N/A bila semua Non-Cetak |
| 04 | Persetujuan Desain | `po_gates.design=open` / approvals | N/A Non-Cetak |
| 05 | Persiapan Material | task `persiapan-cup` | Pending jika koleksi belum ada |
| 06 | Penjadwalan Produksi | task `penjadwalan` / schedules | |
| 07 | Persiapan Cetak | task `screen` | N/A Non-Cetak |
| 08 | Proses Cetak | task `cetak` | N/A Non-Cetak |
| 09 | Pemeriksaan Kualitas | task `qc-packing` | |
| 10 | Penyelesaian Produksi | agregat cetak/qc | Gap ledger Qty produksi formal |
| 11 | Persiapan Pengiriman | delivery_notes | **Gap main:** modul SJ belum |
| 12 | Barang Siap Dikirim | note ready flag | Gap |
| 13 | Dalam Pengiriman | task `pengiriman` / note status | |
| 14 | Barang Diterima | delivery_evidence | Gap |
| 15 | Pesanan Selesai | qty shipped ≥ qty PO | Tidak boleh selesai tanpa Qty |

Progres: **milestone ratio** ≠ **production Qty ratio** ≠ **shipping Qty ratio**.

## E. Permission & keamanan tracking

1. Slug customer/PO = label saja; auth = token CSPRNG 22-char (~132 bit), hash SHA-256 di DB.
2. Mismatch slug/PO → 404 (tidak ekspansi akses).
3. Revoke / regenerate / expiry opsional.
4. Rate limit brute-force (reuse `LoginRateLimiter`).
5. Publik: tanpa Accurate ID, tanpa alamat sensitif, faktur & penerimaan **locked**.
6. `Cache-Control: no-store`, `Referrer-Policy: no-referrer`.
7. Soft-deleted evidence/SJ tidak ditampilkan.
8. Audit generate/revoke/regenerate tanpa raw token.
9. SW tidak cache `/api/*` / dokumen keuangan.

## F. Testing & CI

- Backend: `tests/test_tracking.py` (token valid/invalid/revoked/expired, cross-slug, Non-Cetak N/A, Qty progress, SJ partial/supersede/soft-delete, financial lock).
- Frontend: vitest format helpers + typecheck/build.
- CI workflow existing (pytest + typecheck + build).

## G. Risiko & keputusan Owner

| Risiko / keputusan | Rekomendasi |
|--------------------|-------------|
| PR #6 belum merge | Tracking hidup di main dengan Pending jujur; jangan copy modul workflow. |
| Event verifikasi (#02) | Owner definisikan kontrak verifikasi atau biarkan Pending. |
| Delivery/SJ backend | Tunggu modul delivery; jangan fabrikasi SJ/PDF. |
| Nomor CS / WhatsApp | Isi konfigurasi Owner (`HELP_CONTACT`). |
| Scope financial di link | Default ditolak; butuh auth khusus bila nanti diizinkan. |
| PWA Approval / Operasional | SW hanya `/t/` — hindari bentrok. |

**STOP — NO MERGE — NO DEPLOY.**
