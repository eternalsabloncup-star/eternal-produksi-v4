---
name: eternal-v4-layout-system
description: Standar layout workspace tabel, KPI, sidebar, modal, responsive desktop dan mobile Eternal V4; gunakan bersama design-system.
---

# Layout System 1.0

Workspace tabel: full width sisa sidebar;
single viewport dengan topbar/KPI/toolbar/filter
tetap terlihat selama table body scroll,
header sticky, pagination terjangkau.

Jangan paksa tinggi kosong tanpa fungsi;
uji data 0, 1, banyak baris.

Horizontal scroll hanya dalam tabel/area
yang memang perlu; scrollbar tampak saat
overflow dan akses aksi sticky kanan bila
cocok tanpa overlap.

Filter header, sort, search, column visibility,
preferences, reset, pagination, selection dan
semua aksi dipertahankan.

Numeric alignment konsisten; teks panjang bisa
dibaca via wrap/detail/tooltip yang dapat diakses.

Jangan menyembunyikan kolom tanpa akses pemulihan.

Sidebar desktop 64/168 px dengan label expanded,
active state jelas, control di batas sidebar.

KPI tinggi adaptif; tidak ada clipping walau
expanded, 9 kartu, nilai besar atau 1280px.

Gunakan min-width/grid/wrap yang tepat,
jangan paksa satu baris pada mobile.

Page header tidak diduplikasi jika topbar cukup.

Toolbar satu baris desktop bila muat, wrap atau
accessible menu pada layar sempit tanpa
menghapus fitur.

Disabled filter dengan penjelasan tetap
dipertahankan.

Settings, dashboard, public tracking/approval,
long form boleh scroll dokumen; jangan paksa
single viewport ke halaman panjang.

Modal header/footer terjangkau, body scroll,
max-height sesuai viewport, hindari nested
overflow; tabel modal scroll internal tanpa
memotong aksi.

Width sesuai isi dan kontrak,
bukan satu angka untuk semua.

Modal mobile margin aman,
form satu kolom bila perlu.

Mobile: gunakan MobileOpsShell untuk workflow
operasional, bottom nav dan safe-area.

Jangan mengecilkan desktop rail/KPI menjadi
tidak terbaca.

Fallback AppShell pada viewport kecil harus
menjaga akses role/route sah dan fungsi yang
belum tersedia di mobile.

Tidak auto redirect ke route yang tidak setara,
tidak menolak akses.

Pilihan breakpoint tepat dibuktikan per kontrak.
Audit historis desktop >=1280 dan mobile <=768
hanya rekomendasi; rentang antara wajib aman.

Uji 1920/1440/1366/1280/768/430/390/360
sesuai scope.

Tidak ada overlap topbar, aksi terpotong,
horizontal page overflow atau scroll trap.
