import { describe, expect, it } from "vitest";
import { DeliverySjDetailModal } from "./DeliverySjDetailModal";
import source from "./DeliverySjDetailModal.tsx?raw";

describe("DeliverySjDetailModal UI contract (Owner revision)", () => {
  it("exports the shared modal component", () => {
    expect(DeliverySjDetailModal).toBeTypeOf("function");
  });

  it("removes Header Surat Jalan section and info grid", () => {
    expect(source).not.toContain("Header Surat Jalan");
    expect(source).not.toContain('data-testid="sj-detail-header"');
    expect(source).not.toContain('["Nomor SJ"');
    expect(source).not.toContain('["Alamat Lengkap"');
    expect(source).not.toContain('["Zona Pengiriman"');
  });

  it("removes both informative Accurate notes", () => {
    expect(source).not.toContain("Tidak dikirim Accurate pada SJ ini");
    expect(source).not.toContain("Nilainya ditampilkan kosong, bukan diisi asumsi");
    expect(source).not.toContain("char_field3_zone_unproven");
    expect(source).not.toContain("semantik nilainya belum diverifikasi Owner");
    expect(source).not.toContain('data-testid="sj-absent-fields"');
  });

  it("preserves modal head identity, body sections, and footer actions", () => {
    expect(source).toContain("Detail Surat Jalan · Accurate");
    expect(source).toContain("{note.sj_no}");
    expect(source).toContain("{display(note.customer_name)}");
    expect(source).toContain('data-testid="revision-status"');
    expect(source).toContain("Rincian Barang");
    expect(source).toContain("Operasional");
    expect(source).toContain("Validasi Qty Loading");
    expect(source).toContain("Perbedaan Qty");
    expect(source).toMatch(/className="ep-btn" onClick=\{onClose\}>\s*Tutup\s*</);
    expect(source).toContain("Konfirmasi Loading");
    expect(source).toContain("onConfirmLoading");
    expect(source).toContain("onDiscrepancy");
    expect(source).toContain("disabled={busy || !validation?.all_matched || row.loaded}");
  });

  it("wires mobile section label and compact operasional without dropping data", () => {
    expect(source).toContain("ep-sj-items-section-mobile");
    expect(source).toContain("Rincian Barang — {items.length} Baris");
    expect(source).toContain("ep-sj-ops");
    expect(source).toContain("Status Loading");
    expect(source).toContain("Status Pengiriman");
    expect(source).toContain("Status Pembatalan / Loading Ulang");
    expect(source).toContain("<SjApiLineItems items={items} />");
  });

  it("wraps detail tables for mobile horizontal scroll without collapsing height", () => {
    expect(source).toContain('className="ep-sj-tbl-scroll');
    expect(source).toContain("Validasi Qty Loading");
    expect(source).toContain("Riwayat Revisi SJ");
  });

  it("provides mobile compact lists for revisi and validasi", () => {
    expect(source).toContain('data-testid="sj-revision-list-mobile"');
    expect(source).toContain('data-testid="sj-validation-list-mobile"');
    expect(source).toContain("Tidak Cocok");
    expect(source).toContain("Qty SJ:");
    expect(source).toContain("Qty Tersedia:");
    expect(source).toContain("ep-sj-desk-only");
  });
});
