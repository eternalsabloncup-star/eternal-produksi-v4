---
name: eternal-v4-design-system
description: Standar visual dan tujuh keputusan Owner V4, wajib untuk desain dan implementasi UI V4.
---

# Eternal Design System — standar 1.0

Arah: Modern Compact Enterprise ERP.

Pertahankan identitas hijau Eternal, DM Sans,
React/TypeScript/Vite, SVG icons existing, token CSS
dan komponen existing.

Tidak ada migrasi framework, Tailwind/shadcn atau
dependency baru tanpa audit dan izin.

Prioritas informasi operasional, keterbacaan,
aksesibilitas dan efisiensi; hindari dekorasi,
judul ganda, ruang kosong buatan, card berulang.

## OWNER LOCKED — 27 Nov 2026

1. KPI tinggi adaptif terhadap isi/viewport;
   tidak ada label atau angka terpotong,
   jangan mengecilkan font ekstrem,
   jangan menghapus KPI.
   Desktop satu baris bila muat,
   wrap/grid bila tidak.

2. MobileOpsShell untuk operasional,
   fallback AppShell desktop yang aman.
   Tidak memblokir role/route/fitur tanpa
   keputusan bisnis; pertahankan akses sah.

3. Tabel: scrollbar terlihat saat overflow dan
   sticky action column sesuai kebutuhan,
   tanpa menutupi data atau mengubah permission.

4. Sidebar collapsed 64 px / expanded 168 px;
   expanded tampil label teks;
   collapse control menempel pada batas sidebar,
   hit area layak.

5. Workspace tabel full width;
   halaman lain boleh punya max-width yang sesuai.

6. Manajemen Cat: tunggu PR #71 selesai DAN
   terverifikasi; cek status/HEAD/CI dan baseline
   terbaru, jangan memakai panel inline lama
   sebagai standar.
   Happy path fixture wajib terbukti sebelum
   perubahan terkait lulus.

7. API components satu-satunya acuan preview/desain;
   DEMO tidak jadi referensi, penghapusan hanya
   setelah audit dan izin terpisah.

## Token dan komponen

Audit historis 27 Nov:
tokens.css primary #1a4d3e,
bg #eef4f0,
paper #fff,
ink #14241c,
muted #5f7268,
line #c9dbd0,
green #15835d,
amber #a76d13,
red #bd4254,
nav #102d24,
table header #123d30,
radius 7px,
DM Sans.

Verifikasi source aktual.

Pertahankan identitas; konsolidasikan
warna/typography/spacing/radius/motion ke token,
bukan hex hardcode baru berulang.

Warna status harus sesuai semantik bisnis dan
label teks, bukan hanya warna.
KPI theme bukan status bisnis.

Usulan belum dikunci:
- Skala tipe 10/11/12/14/18 atau 13–14 isi.
- Spacing kelipatan 4.
- Input desktop 32–40.
- Mobile target sentuh nyaman 44.
- Radius 6–8.
- Motion 120–200ms.

Pilih nilai berdasarkan audit dan kontrak Owner;
jangan mengubah global typography serentak.

Ukur kontras teks normal 4.5:1, besar 3:1,
kontrol/focus relevan; target WCAG 2.2 AA sesuai
scope, bukan klaim sertifikasi.

Gunakan DataTable, KpiSummary, AppShell,
MobileOpsShell, StatusBadge, TaskWorkModalShell
sebagai kandidat reuse.

Modal existing belum lolos keyboard pada audit M1,
bukan standar final.

Komponen khusus matrix permission/stocktake boleh
tetap khusus.

Standardisasi tombol, input, dropdown, table,
dialog, toast, skeleton, empty/error/loading dan
pagination bertahap; preserve setiap fungsi.
