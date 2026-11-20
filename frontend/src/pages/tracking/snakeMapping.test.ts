import { describe, expect, it } from "vitest";
import type { TrackingMilestone } from "../../api/tracking";
import {
  OWNER_DOC_SLOTS,
  OWNER_SNAKE_STAGES,
  buildSnakeStages,
} from "./snakeMapping";

function m(
  partial: Partial<TrackingMilestone> & Pick<TrackingMilestone, "id" | "state">,
): TrackingMilestone {
  return {
    seq: 0,
    label: partial.id,
    detail: "",
    timestamp: null,
    relevant: true,
    ...partial,
  };
}

describe("snake display mapping", () => {
  it("defines exactly 15 owner stages", () => {
    expect(OWNER_SNAKE_STAGES).toHaveLength(15);
    expect(OWNER_SNAKE_STAGES.map((s) => s.label)).toEqual([
      "Order Masuk",
      "Design",
      "Approval Design",
      "Payment",
      "Approval Payment",
      "Penjadwalan",
      "Screener",
      "Persiapan",
      "Operator",
      "Konfirmasi Sample",
      "Cetak",
      "Packing",
      "Surat Jalan",
      "Loading",
      "Pengiriman",
    ]);
  });

  it("does not invent payment/operator completion without sources", () => {
    const stages = buildSnakeStages([
      m({ id: "order_received", state: "completed", seq: 1 }),
      m({ id: "design_prep", state: "completed", seq: 3 }),
    ]);
    expect(stages[0].visual).toBe("done");
    expect(stages[1].visual).toBe("done");
    expect(stages[3].visual).toBe("wait"); // Payment — no source
    expect(stages[3].gap).toBeTruthy();
    expect(stages[8].visual).toBe("wait"); // Operator — no source
  });

  it("maps pengiriman from delivered over in_transit", () => {
    const stages = buildSnakeStages([
      m({ id: "in_transit", state: "in_progress", seq: 13 }),
      m({ id: "delivered", state: "completed", seq: 14 }),
    ]);
    expect(stages[14].visual).toBe("done");
  });

  it("keeps five owner document cards in Design→Payment→Invoice→SJ→Ship order", () => {
    expect(OWNER_DOC_SLOTS.map((s) => s.key)).toEqual([
      "design",
      "payment",
      "invoice",
      "sj",
      "ship",
    ]);
    expect(OWNER_DOC_SLOTS.map((s) => s.title)).toEqual([
      "Design",
      "Pembayaran",
      "Faktur Penjualan",
      "Surat Jalan",
      "Pengiriman",
    ]);
  });
});
