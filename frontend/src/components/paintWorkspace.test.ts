import { describe, expect, it } from "vitest";
import {
  allRequiredCounted,
  canCreateStocktakeSession,
  canEnterStocktakePreview,
  canFinalizeStocktake,
  conversionMassStatus,
  isPhysicalCountFilled,
  isPreviewStale,
  parsePhysicalCount,
  resolvePaintUserEmail,
  stocktakeCountsFingerprint,
} from "./paintWorkspaceGuards";

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

describe("stocktake stage gates", () => {
  const lines = [
    { paint_id: "a", counted_g: "10" },
    { paint_id: "b", counted_g: "0" },
  ];

  it("treats empty as uncounted and zero as valid", () => {
    expect(isPhysicalCountFilled("")).toBe(false);
    expect(isPhysicalCountFilled("  ")).toBe(false);
    expect(isPhysicalCountFilled("0")).toBe(true);
    expect(parsePhysicalCount("0")).toBe(0);
    expect(parsePhysicalCount("")).toBeNull();
    expect(parsePhysicalCount("-1")).toBeNull();
  });

  it("blocks preview until every required line is filled", () => {
    expect(canEnterStocktakePreview("50", [{ paint_id: "a", counted_g: "" }])).toBe(
      false,
    );
    expect(canEnterStocktakePreview("", lines)).toBe(false);
    expect(canEnterStocktakePreview("50", lines)).toBe(true);
    expect(allRequiredCounted([{ paint_id: "a", counted_g: "" }, ...lines])).toBe(
      false,
    );
  });

  it("blocks session without matching successful preview fingerprint", () => {
    const fp = stocktakeCountsFingerprint(lines);
    expect(
      canCreateStocktakeSession({
        branchId: "50",
        lines,
        preview: null,
        previewFingerprint: null,
      }),
    ).toBe(false);
    expect(
      canCreateStocktakeSession({
        branchId: "50",
        lines,
        preview: { status: "preview", persisted: false },
        previewFingerprint: fp,
      }),
    ).toBe(true);
    expect(
      canCreateStocktakeSession({
        branchId: "50",
        lines: [{ paint_id: "a", counted_g: "11" }, { paint_id: "b", counted_g: "0" }],
        preview: { status: "preview", persisted: false },
        previewFingerprint: fp,
      }),
    ).toBe(false);
  });

  it("marks preview stale when counts change", () => {
    const fp = stocktakeCountsFingerprint(lines);
    expect(isPreviewStale(lines, fp)).toBe(false);
    expect(
      isPreviewStale([{ paint_id: "a", counted_g: "9" }, { paint_id: "b", counted_g: "0" }], fp),
    ).toBe(true);
  });

  it("blocks finalize without session or incomplete counts", () => {
    expect(canFinalizeStocktake({ stocktakeId: null, lines })).toBe(false);
    expect(
      canFinalizeStocktake({
        stocktakeId: "pst_1",
        lines: [{ paint_id: "a", counted_g: "" }],
      }),
    ).toBe(false);
    expect(canFinalizeStocktake({ stocktakeId: "pst_1", lines })).toBe(true);
  });
});

describe("conversion mass status", () => {
  it("never reports balanced for empty/incomplete inputs", () => {
    expect(
      conversionMassStatus({
        sourcesReady: false,
        resultReady: false,
        sourceTotal: 0,
        destG: 0,
        wasteG: 0,
      }),
    ).toBe("incomplete");
    expect(
      conversionMassStatus({
        sourcesReady: true,
        resultReady: true,
        sourceTotal: 0,
        destG: 0,
        wasteG: 0,
      }),
    ).toBe("incomplete");
  });

  it("reports balanced and unbalanced for valid inputs", () => {
    expect(
      conversionMassStatus({
        sourcesReady: true,
        resultReady: true,
        sourceTotal: 100,
        destG: 90,
        wasteG: 10,
      }),
    ).toBe("balanced");
    expect(
      conversionMassStatus({
        sourcesReady: true,
        resultReady: true,
        sourceTotal: 100,
        destG: 95,
        wasteG: 10,
      }),
    ).toBe("unbalanced");
  });
});

describe("paint user picker", () => {
  const users = [
    { email: "a@eternal.local", display_name: "Alice" },
    { email: "b@eternal.local", display_name: "Bob" },
  ];

  it("accepts backend email or unique display name, rejects free-typed email", () => {
    expect(resolvePaintUserEmail("a@eternal.local", users)).toBe("a@eternal.local");
    expect(resolvePaintUserEmail("Alice", users)).toBe("a@eternal.local");
    expect(resolvePaintUserEmail("forged@evil.test", users)).toBeNull();
  });
});
