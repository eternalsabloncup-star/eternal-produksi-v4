# Aturan Bisnis Terkunci — Eternal Production V4

Dokumen ini hanya memuat keputusan Owner yang sudah **terkunci**. Hal yang
belum diputuskan dicatat terpisah sebagai blocker dan tidak boleh diterapkan
sebagai aturan sistem.

---

## ATURAN TERKUNCI — REVISI SJ SEBAGIAN ITEM / PEMBATALAN LOADING

- **Keputusan:** Opsi B — Pembatalan Loading dan Loading Ulang.
- **Tanggal keputusan:** 19 November 2026.
- **Cakupan:** Surat Jalan (SJ) Accurate yang direvisi sebagian itemnya,
  beserta dampaknya pada Loading, Qty QC & Packing, dan tugas Driver.

### Prinsip

1. SJ dibuat dan direvisi di Accurate; V4 hanya menyimpan snapshot sumber dan
   mengelola workflow operasionalnya.
2. Setiap revisi menambah versi baru; snapshot sebelumnya tidak pernah ditimpa.
3. Relasi item SJ ke item line PO memakai `accurate_line_id`. Tanpa referensi
   itu, sinkronisasi ditolak — relasi tidak boleh ditebak dari nama barang,
   kesamaan PO, atau kesamaan Qty.
4. Qty QC & Packing yang sudah dipakai Loading tidak boleh terpakai dua kali.

### A. SJ belum Loading

- Revisi disimpan sebagai versi baru beserta daftar item yang berubah.
- Item Cetak divalidasi terhadap Qty QC & Packing yang masih tersedia.
- Item Non-Cetak mengikuti Qty pada SJ yang berlaku.
- Setelah seluruh item valid, Loading dikonfirmasi seperti biasa. Tidak ada
  pembatalan karena belum ada Loading terkonfirmasi.

### B. SJ sudah Loading, pengiriman belum berhasil

- Status SJ menjadi `PERLU_LOADING_ULANG`.
- Loading lama tidak lagi aktif untuk keperluan operasional.
- Backend menolak konfirmasi pengiriman yang mengacu Loading lama.
- Pembatalan Loading dilakukan lewat aksi berizin yang mencatat alasan, aktor,
  dan waktu.
- Reversi Qty memakai jumlah yang benar-benar dikonsumsi Loading tersebut,
  dijalankan dalam satu transaksi MongoDB.
- Konfirmasi QC & Packing tidak dihapus oleh reversi; hanya ketersediaannya
  yang dikembalikan.
- Loading ulang wajib mencakup seluruh item SJ versi terbaru.
- Tugas Driver yang sah mengikuti Loading baru; tidak boleh ada dua Loading
  aktif atau dua tugas Driver aktif untuk satu SJ.

### C. Pengiriman gagal, belum diterima customer

- Perlakuan sama dengan huruf B.
- Riwayat percobaan gagal, alasan kegagalan, dan tugas koordinasi Kepala
  Produksi tetap tersimpan dan tidak dihapus oleh Loading ulang.

### D. SJ sudah berhasil dikirim

- Loading lama tidak dibatalkan otomatis.
- Qty yang sudah menjadi histori pengiriman berhasil tidak direverse.
- Foto, tanda tangan, nama penerima, dan status pengiriman historis tidak
  diubah.
- Perubahan dari Accurate disimpan sebagai peristiwa koreksi dan SJ ditandai
  `PERLU_KOREKSI_ADMINISTRASI`.
- Tidak ada proses pengiriman ulang otomatis; tindak lanjut fisik maupun
  administratif memerlukan prosedur terpisah yang belum ditetapkan.

### Permission

- Pembatalan Loading memakai aksi `cancel` pada jenis pekerjaan `pengiriman`.
- Aksi ini **tidak** diberikan ke role mana pun secara default; Owner
  memberikannya melalui Master/Pengaturan.
- Percobaan tanpa izin ditolak backend dengan 403, bukan sekadar disembunyikan
  di UI.

### Audit

Setiap revisi dan pembatalan mencatat: identitas SJ, versi sebelum dan
sesudah, item yang berubah, Loading yang dibatalkan, Qty yang dikembalikan,
aktor, waktu, dan alasan. Riwayat bersifat append-only.

---

## Status integrasi Accurate

Konektor read-only Delivery Order + sync manual ber-guard sudah ada di kode
(GET list/detail, kill switch `V4_SJ_SYNC_ENABLED` default OFF, Mongo test
`eternal_v4_test_*` saja). **Aktivasi live sync** (non-prod maupun production)
tetap memerlukan verifikasi Owner/CEO: kredensial live, DB test terisolasi,
dan bukti uji — lihat `docs/sj-accurate-sync-activation.md`. Webhook dan
auto-sync dilarang sampai diputuskan terpisah.

---

## PEMETAAN FIELD SURAT JALAN ACCURATE → MODEL V4

Sumber struktur: halaman Accurate `DeliveryOrder` (module `DO`) yang diberikan
Owner — `columnDef` untuk header dan `memorizeFieldDef.detailField.detailItem`
untuk item. HTML halaman **bukan** respons REST API: nama field dipakai sebagai
schema, nilainya tidak pernah diasumsikan ada.

### Header

| Field Accurate | Model Mongo | Catatan |
| --- | --- | --- |
| `id` | `accurate_sj_id` | Wajib; fail-closed |
| `number` | `sj_no` | Wajib; fail-closed |
| `salesOrderId` / `detailItem[].salesOrder.id` | `accurate_po_id` | Wajib; dua Sales Order dalam satu SJ ditolak |
| `salesOrderNumber` / `detailItem[].salesOrder.number` | `po_no` | Identitas PO V4 |
| `poNumber` | `customer_po_no` | Nomor PO milik pelanggan, **bukan** `po_no` |
| `transDate` | `sj_date` | Juga menjadi `ship_date` selama Accurate tidak mengirim tanggal kirim terpisah (`source_meta.ship_date_source`) |
| `customer.name` | `customer_name` | |
| `customerNo` | `customer_no` | |
| `toAddress` | `destination` | |
| `contactInfo` | `contact_info` | Nama kontak; nomor telepon belum terbukti dikirim |
| `masterSalesman.name` | `salesman_name` | |
| `createdByUserName` | `created_by_user_name` | |
| `shipment.name` | `shipment` | |
| `charField3` | `ship_zone` | Label halaman "Zona Pengiriman"; nilai **belum diverifikasi** → ditandai `source_meta.char_field3_zone_unproven` |
| `branch.name` | `branch_name` | |
| `description` | `description` | |
| `printedByUser` | `printed_by_user` | **Bukan** pembuat data |
| `deliveryPrintedTime` | `printed_time` | |
| `deliveryPackingNumber` | `packing_number` | |
| `statusName` | `accurate_status_name` | Status Accurate, terpisah dari status operasional V4 |
| `attachmentExist` | `attachment_exist` | Tri-state: `true`/`false`/tidak dikirim |

### Item line

| Field Accurate | Model Mongo | Catatan |
| --- | --- | --- |
| `detailItem[].id` | `sj_line_id` | Wajib; duplikat ditolak |
| `detailItem[].salesOrderDetailId` | `accurate_line_id` | Wajib; relasi item line PO |
| `detailItem[].item.no` | `item_code` | |
| `detailItem[].detailName` | `item_name` | |
| `detailItem[].quantity` | `qty` | |
| `detailItem[].itemUnit.name` | `unit` | Satuan berbeda tidak pernah dijumlahkan |
| `detailItem[].warehouse.name` | `warehouse_name` | Gudang/outlet |
| `detailItem[].detailNotes` | `detail_notes` | |

### Aturan isi

- Field yang tidak dikirim Accurate disimpan kosong dan dicatat di
  `source_meta.absent_fields`; UI menampilkannya sebagai kosong.
- Nomor telepon kontak belum terbukti ada pada payload SJ → selalu kosong
  sampai Owner memverifikasi sumbernya.
- Perubahan header saja dicatat sebagai `changed_header_fields` dan **tidak**
  menaikkan versi SJ; aturan Opsi B tetap hanya dipicu perubahan item line atau
  tanggal kirim.

## Blocker aturan bisnis (belum diputuskan)

- Rumus Ready Gudang, mutasi, dan Status Buffer final.
- Relasi SJ pengganti ber-ID baru tanpa referensi pengganti yang terbukti.
- Reject/scrap dan koreksi Qty produksi.
- Konversi satuan campuran (mis. PCS ↔ PACK).
- Restore bukti yang sudah di-soft-delete.
- Payment: refund, pembatalan penerimaan, multi-faktur, dan overpayment.
