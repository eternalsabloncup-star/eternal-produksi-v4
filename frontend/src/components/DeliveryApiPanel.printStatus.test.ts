import { describe, expect, it } from "vitest";
import source from "./DeliveryApiPanel.tsx?raw";
import cell from "./PrintStatusCell.tsx?raw";

describe("kolom Status Cetak", () => {
  it("terdaftar di tabel dan bisa disembunyikan", () => {
    expect(source).toContain('id: "printStatus"');
    expect(source).toContain('label: "Status Cetak"');
    expect(source).toContain("hideable: true");
    expect(source).toContain("printStatusFilterText");
    expect(source).toContain("printStatusSortValue");
    expect(source).toContain("<PrintStatusCell");
  });

  it("tidak mengarang 0% ketika angka belum sah", () => {
    expect(cell).toContain("Belum tersedia");
    expect(cell).toContain("printStatusHasNumbers");
    expect(cell).toContain('role="progressbar"');
  });
});
