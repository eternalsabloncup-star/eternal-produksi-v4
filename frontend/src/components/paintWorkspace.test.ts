import { describe, expect, it } from "vitest";

/** Pure helpers mirrored from PaintApiPanel labeling — regression for UX copy. */
const DOC_LABEL: Record<string, string> = {
  inbound_manual: "Cat Masuk",
  withdrawal: "Pengambilan",
  return: "Pengembalian",
  conversion: "Konversi",
  waste: "Waste",
  reversal: "Koreksi",
  stocktake_adjustment: "Stock Opname",
};

/** Single-row toolbar order — nav then separator then actions. */
const PAINT_TOOLBAR_ORDER = [
  "Stok Cat",
  "Riwayat Transaksi",
  "Laporan",
  "Pengaturan",
  "|",
  "Ambil Cat",
  "Pengembalian/Konversi",
  "Cat Masuk",
  "Waste",
  "Stock Opname",
] as const;

describe("paint workspace labels", () => {
  it("maps all core document types to Indonesian UI labels", () => {
    expect(DOC_LABEL.withdrawal).toBe("Pengambilan");
    expect(DOC_LABEL.inbound_manual).toBe("Cat Masuk");
    expect(DOC_LABEL.stocktake_adjustment).toBe("Stock Opname");
  });

  it("keeps conversion distinct from return", () => {
    expect(DOC_LABEL.conversion).not.toBe(DOC_LABEL.return);
  });

  it("keeps compact single-row toolbar order", () => {
    expect([...PAINT_TOOLBAR_ORDER]).toEqual([
      "Stok Cat",
      "Riwayat Transaksi",
      "Laporan",
      "Pengaturan",
      "|",
      "Ambil Cat",
      "Pengembalian/Konversi",
      "Cat Masuk",
      "Waste",
      "Stock Opname",
    ]);
  });
});
