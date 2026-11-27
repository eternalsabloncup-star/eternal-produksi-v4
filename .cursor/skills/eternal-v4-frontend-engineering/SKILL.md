---
name: eternal-v4-frontend-engineering
description: Aturan Cursor untuk implementasi FE V4, reuse, feature preservation, interaksi, dan self-correction.
---

# Frontend Engineering 1.0

Cursor executor: baca design contract Owner,
design-system dan layout-system.

Audit aktual sebelum coding;
gunakan komponen existing terlebih dahulu.

Implementasi minimum dalam scope;
satu main agent, eternal-fast-precision,
no subagent/delegation/video.

No prod data/write; read-only production baseline
hanya sesuai production-baseline-smoke.

Jangan menambah dependency, refactor luas,
rewrite shell.css, menghapus DEMO, atau mengubah
API/backend/data/permission/business gate tanpa izin.

Jangan memindah guard saat ekstraksi UI;
UI hiding bukan security.

Inventaris semua tombol, filter, search, sort,
pagination, column visibility, field, validation,
status, permission, API bindings, navigation,
loading/error/empty, side effects.

Buat before/after matrix dan regresi untuk tiap
behavior terdampak, terutama accepted PO,
design/payment gate, revisi qty, QC/Packing,
Loading SJ, delivery, inventory, Accurate,
tracking, RBAC.

Preserve data semantics dan idempotency.

Jangan menambah fake status atau default
yang mengubah aturan.

Shared component/token/CSS:
petakan semua direct dan indirect consumers,
PR paralel dan CSS specificity.

Gunakan scoped selectors dan token,
hindari !important baru tanpa alasan bukti.

Jangan mengubah komponen global untuk satu
halaman bila perubahan lokal cukup.

Jangan duplikasi komponen bila reuse aman.

Uji representative consumers setiap layout
dan seluruh jalur berisiko; overlap material HOLD.

Audit historis:
DataTable lintas jadwal/inbox/flow/tugas/
buffer/cat/delivery/laporan; shell.css besar,
modal class alias, KPI fixed height;
bukan izin refactor semua.

Dialog:
- Accessible name.
- Role/aria-modal sesuai jenis.
- Fokus awal.
- Focus trap.
- Focus return.
- Escape dan backdrop aman.

Jangan tutup/membuang draft atau transaksi
in-flight tanpa kontrak.
Escape boleh memicu konfirmasi yang sah.

Form:
- Label.
- Focus-visible.
- Validation error.
- Disabled/busy.
- Double-submit prevention.

Terapkan ui-form-interaction-test:
ketik karakter demi karakter,
bukan .fill sebagai bukti.

Minimal field yang terdampak:
focus input, ketik lima karakter valid,
pastikan fokus dan nilai tetap,
lanjut tiga karakter valid tanpa klik ulang,
pastikan nilai terakumulasi, tidak reset,
tidak remount bila dapat dibuktikan.

Numeric test pakai digit valid.

Uji dropdown clipping, keyboard,
long text dan scroll.

Cursor wajib visual QA, perbaiki sendiri
temuan dalam scope dan ulang hanya tes
terdampak hingga PASS.

Stop untuk blocker, drift, business change,
akses tidak tersedia, scope baru atau
approval gate.

Jangan membuat PR/commit/push sebelum
preview aman dan izin finalisasi.

Final report Bahasa Indonesia dengan
diff/checkpoint, test evidence,
residual risks dan verdict.

Build/typecheck/diff-check/CI sesuai existing.

Tidak menyamakan fixture success dengan
production write success.
