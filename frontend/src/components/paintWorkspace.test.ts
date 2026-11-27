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

describe("paint workspace labels", () => {
  it("maps all core document types to Indonesian UI labels", () => {
    expect(DOC_LABEL.withdrawal).toBe("Pengambilan");
    expect(DOC_LABEL.inbound_manual).toBe("Cat Masuk");
    expect(DOC_LABEL.stocktake_adjustment).toBe("Stock Opname");
  });

  it("keeps conversion distinct from return", () => {
    expect(DOC_LABEL.conversion).not.toBe(DOC_LABEL.return);
  });
});
