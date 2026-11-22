import { describe, expect, it } from "vitest";
import {
  FLOW_CHIP_ROW1_COUNT,
  buildProductionFlowCards,
  formatStageCountBadge,
  splitProductionFlowCardRows,
} from "./ProductionFlowTrack";

describe("ProductionFlowTrack cards", () => {
  it("builds exactly 17 cards: Semua + 15 stages + Selesai", () => {
    const cards = buildProductionFlowCards();
    expect(cards).toHaveLength(17);
    expect(cards[0]).toEqual({ id: "flow-all", label: "Semua" });
    expect(cards[cards.length - 1]).toEqual({
      id: "flow-selesai",
      label: "Selesai",
    });
    expect(cards.map((c) => c.id)).toEqual([
      "flow-all",
      "flow-order-masuk",
      "flow-design",
      "flow-approval-design",
      "flow-payment",
      "flow-approval-payment",
      "flow-penjadwalan",
      "flow-screener",
      "flow-persiapan",
      "flow-operator",
      "flow-konfirmasi-sample",
      "flow-cetak",
      "flow-packing",
      "flow-surat-jalan",
      "flow-loading",
      "flow-pengiriman",
      "flow-selesai",
    ]);
  });

  it("splits into 9 + 8 rows", () => {
    const [row1, row2] = splitProductionFlowCardRows(buildProductionFlowCards());
    expect(FLOW_CHIP_ROW1_COUNT).toBe(9);
    expect(row1).toHaveLength(9);
    expect(row2).toHaveLength(8);
    expect(row1[0].id).toBe("flow-all");
    expect(row2[row2.length - 1].id).toBe("flow-selesai");
  });

  it("formats badge including zero", () => {
    expect(formatStageCountBadge(0, "ready")).toBe("0");
    expect(formatStageCountBadge(12, "ready")).toBe("12");
    expect(formatStageCountBadge(undefined, "loading")).toBe("…");
    expect(formatStageCountBadge(5, "error")).toBe("?");
  });
});
