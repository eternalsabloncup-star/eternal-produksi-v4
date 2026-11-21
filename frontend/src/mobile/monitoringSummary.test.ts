import { describe, expect, it } from "vitest";
import type { OperationalTask } from "../api/operational";
import { buildMonitoringSummary } from "./monitoringSummary";

function task(
  partial: Partial<OperationalTask> &
    Pick<OperationalTask, "task_key" | "status" | "task_type">,
): OperationalTask {
  return {
    scope: "po",
    assigned_role: "helper",
    qty_confirmed: 0,
    ref: {},
    ...partial,
  };
}

describe("buildMonitoringSummary", () => {
  it("mirrors status badge buckets and coordination", () => {
    const rows = [
      task({ task_key: "a", task_type: "screen", status: "belum_dikerjakan" }),
      task({ task_key: "b", task_type: "screen", status: "sebagian" }),
      task({ task_key: "c", task_type: "screen", status: "selesai" }),
      task({
        task_key: "d",
        task_type: "qty-loading-sj-berbeda",
        status: "belum_dikerjakan",
        coordination: true,
      }),
      task({
        task_key: "e",
        task_type: "loading-sj",
        status: "belum_dikerjakan",
        context: { loading_cancelled: true },
      }),
    ];
    const s = buildMonitoringSummary(rows);
    expect(s.all).toBe(5);
    expect(s.belum).toBe(2);
    expect(s.sebagian).toBe(1);
    expect(s.selesai).toBe(1);
    expect(s.coordination).toBe(1);
  });

  it("aggregates qty shortfall only when qty_target present", () => {
    const withQty = [
      task({
        task_key: "a",
        task_type: "cetak",
        status: "sebagian",
        qty_target: 100,
        qty_confirmed: 40,
      }),
    ];
    expect(buildMonitoringSummary(withQty).qtyShortfall).toBe(60);
    const noQty = [
      task({ task_key: "b", task_type: "design", status: "belum_dikerjakan" }),
    ];
    expect(buildMonitoringSummary(noQty).qtyShortfall).toBeNull();
  });
});
