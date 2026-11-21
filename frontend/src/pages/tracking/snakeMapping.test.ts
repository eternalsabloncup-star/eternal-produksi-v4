import { describe, expect, it } from "vitest";
import type { TrackingMilestone } from "../../api/tracking";
import {
  MILESTONE_ID_TO_SNAKE_LABEL,
  OWNER_SNAKE_STAGES,
  buildSnakeStages,
  timelineMilestoneLabel,
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

  it("aligns timeline labels with snake OWNER_SNAKE_STAGES", () => {
    expect(timelineMilestoneLabel("order_received", "Pesanan Diterima")).toBe(
      "Order Masuk",
    );
    expect(timelineMilestoneLabel("design_prep", "Persiapan Desain")).toBe(
      "Design",
    );
    expect(timelineMilestoneLabel("design_approved", "Persetujuan Desain")).toBe(
      "Approval Design",
    );
    expect(timelineMilestoneLabel("printing", "Proses Cetak")).toBe("Cetak");
    expect(timelineMilestoneLabel("quality_check", "Pemeriksaan Kualitas")).toBe(
      "Packing",
    );
    expect(timelineMilestoneLabel("in_transit", "Dalam Pengiriman")).toBe(
      "Pengiriman",
    );
    expect(timelineMilestoneLabel("delivered", "Barang Diterima")).toBe(
      "Pengiriman",
    );
    // Unmapped backend milestones keep API label.
    expect(
      timelineMilestoneLabel("order_verified", "Verifikasi Pesanan"),
    ).toBe("Verifikasi Pesanan");
    expect(
      timelineMilestoneLabel("production_complete", "Penyelesaian Produksi"),
    ).toBe("Penyelesaian Produksi");
    expect(timelineMilestoneLabel("order_complete", "Pesanan Selesai")).toBe(
      "Pesanan Selesai",
    );

    const mappedIds = Object.keys(MILESTONE_ID_TO_SNAKE_LABEL).sort();
    expect(mappedIds).toEqual(
      [
        "order_received",
        "design_prep",
        "design_approved",
        "production_scheduled",
        "print_prep",
        "material_prep",
        "printing",
        "quality_check",
        "shipping_prep",
        "ready_to_ship",
        "in_transit",
        "delivered",
      ].sort(),
    );
  });
});
