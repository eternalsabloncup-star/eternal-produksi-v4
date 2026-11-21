import { describe, expect, it } from "vitest";
import { SjApiLineItems } from "./SjApiLineItems";
import source from "./SjApiLineItems.tsx?raw";

describe("SjApiLineItems responsive contract", () => {
  it("exports the shared line-items component", () => {
    expect(SjApiLineItems).toBeTypeOf("function");
  });

  it("keeps desktop table columns and order", () => {
    expect(source).toContain("ep-sj-items-desktop");
    expect(source).toContain("<th>Baris SJ</th>");
    expect(source).toContain("<th>Item Line PO</th>");
    expect(source).toContain("<th>Kode Barang</th>");
    expect(source).toContain("<th>Nama Barang</th>");
    expect(source).toContain("<th>Kts.</th>");
    expect(source).toContain("<th>Satuan</th>");
    expect(source).toContain("<th>Gudang/Outlet</th>");
    expect(source).toContain("<th>Catatan Item</th>");
  });

  it("adds mobile compact list with qty, detail expand, and show-all", () => {
    expect(source).toContain("ep-sj-items-mobile");
    expect(source).toContain("ep-sj-item-name");
    expect(source).toContain("ep-sj-item-qty");
    expect(source).toContain("Lihat Semua Barang");
    expect(source).toContain("Tampilkan Lebih Sedikit");
    expect(source).toContain("Sembunyikan Detail");
    expect(source).toContain("Total Surat Jalan");
    expect(source).toContain("formatQtyByUnit(qtyByUnit(lines))");
    expect(source).toContain("MOBILE_PREVIEW_COUNT = 3");
  });

  it("does not invent empty warehouse or notes values in expand panel", () => {
    expect(source).toContain('(item.warehouse_name || "").trim()');
    expect(source).toContain('(item.detail_notes || "").trim()');
    expect(source).toContain("{warehouse ? (");
    expect(source).toContain("{notes ? (");
  });
});
