---
name: eternal-v4-visual-qa
description: Wajib browser QA V4 untuk perubahan visual/interaksi; bukti screenshot, viewport, parity, self-correction.
---

# Visual QA 1.0

Gunakan browser automation existing atau Chrome
yang tersedia; jangan install dependency tanpa izin.

Fixture lokal pada komponen API production
yang sama:

VITE_INBOX_PO_SOURCE=api
VITE_API_SIMULATION=1 hanya DEV

Banner simulasi wajib;
production mode sim OFF.

Tidak pakai DEMO, production data, write test
atau screenshot mockup sebagai bukti.

Jika tooling/fixture tidak lengkap,
NOT_VERIFIED dan jelaskan blocker.

Baseline screenshot sebelum dan sesudah pada
route/state/role/data/viewport sama.

Screenshot eksplisit untuk audit/implementasi
visual, no video.

Simpan di luar tracked source kecuali
Owner mengizinkan.

Catat:
- Route.
- CSS viewport zoom 100%.
- Role dummy.
- Fixture/state.
- HEAD.
- Fingerprint working diff jika uncommitted.
- URL.
- Timestamp.
- Temuan.

Pastikan Owner bisa membuka forwarded preview
yang benar; localhost agent saja tidak cukup.

## Viewport Matrix

Desktop:
1920x1080
1440x900

Laptop:
1366x768
1280x720

Tablet:
768x1024

Mobile:
430x932
390x844
360x800

Pilih sesuai scope, alasan N/A.

Shared layout uji semua kelompok dan
representative consumers.

Uji:
- Expanded/collapsed sidebar.
- 0/1/banyak rows.
- Teks panjang/angka besar.
- Loading/empty/error.
- Disabled/permission.
- Popup.
- Scroll.
- Keyboard.

Jangan klaim halaman lain PASS dari satu screenshot.

Ukur overflow DOM dan clipping/overlap di dalam
elemen; page overflow 0 bukan bukti cukup.

Uji:
- Scroll horizontal/vertikal.
- Scrollbar terlihat saat perlu.
- Sticky header/aksi tidak menutupi isi.
- Semua kontrol dapat diklik.
- Dropdown/popup tidak terpotong.
- Focus ring.
- Escape/focus return.
- Contrast sesuai scope.
- Touch targets.
- Console/network errors.

Uji aksi nyata dengan data dummy,
termasuk gate disabled/enabled dan continuous
typing sesuai skill existing; bukan screenshot saja.

Catatan audit:
- Dashboard KPI clipping 1440.
- Mobile topbar/KPI rusak.
- Action table di ujung scrollbar tersembunyi.
- Schedule Escape gagal.
- Paint fixture /api/paint/ready 501 bukan
  bukti bug production.
- Happy-path NOT_VERIFIED sampai mock aman lengkap.
- Performa dan full WCAG belum terukur.

Ukur jika material, jangan claim PASS.

Self-correct semua temuan material dalam scope,
retest affected cases.

Jika fixture belum mendukung state wajib,
buat fixture DEV terisolasi hanya dalam scope
yang disetujui atau HOLD.

Jangan menutupi bug dengan CSS clipping.

Jalankan production-baseline-smoke read-only
terpisah pada versi live lama.

Sesudah deploy verifikasi exact asset/source
dan Owner parity sesuai eternal-v4-workflow.

HTTP200/build bukan visual parity.

Tidak ulang tes identik tanpa drift.
