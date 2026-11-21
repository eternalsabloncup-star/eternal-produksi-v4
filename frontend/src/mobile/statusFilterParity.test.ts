/**
 * Parity filter status PWA ↔ web Tugas Tim.
 * Empat bucket saja; cancelled tidak actionable.
 */
import { describe, expect, it } from "vitest";
import type { OperationalTask } from "../api/operational";
import {
  actionableOpenCount,
  aggregateStatusCounts,
  allowTaskAction,
  filterTasksByStatus,
  isCancelledOrInactiveTask,
  type StatusFilter,
} from "../components/teamTaskUi";

function task(
  partial: Partial<OperationalTask> &
    Pick<OperationalTask, "task_key" | "status" | "task_type">,
): OperationalTask {
  return {
    scope: "po",
    assigned_role: "operator",
    qty_confirmed: 0,
    ref: {},
    ...partial,
  };
}

describe("mobile status filter parity with web", () => {
  const rows = [
    task({ task_key: "1", task_type: "cetak", status: "belum_dikerjakan" }),
    task({ task_key: "2", task_type: "cetak", status: "sebagian" }),
    task({ task_key: "3", task_type: "cetak", status: "selesai" }),
    task({
      task_key: "4",
      task_type: "loading-sj",
      status: "belum_dikerjakan",
      context: { loading_cancelled: true },
    }),
  ];

  it("keeps exactly four StatusFilter values", () => {
    const filters: StatusFilter[] = ["all", "belum", "sebagian", "selesai"];
    expect(filters).toHaveLength(4);
  });

  it("badges match aggregateStatusCounts + actionableOpenCount", () => {
    const c = aggregateStatusCounts(rows);
    expect(c).toEqual({ all: 4, belum: 1, sebagian: 1, selesai: 1 });
    expect(actionableOpenCount(rows)).toBe(2);
    expect(isCancelledOrInactiveTask(rows[3])).toBe(true);
    expect(filterTasksByStatus(rows, "belum")).toHaveLength(1);
    expect(filterTasksByStatus(rows, "all")).toHaveLength(4);
  });

  it("permission fail-closed when grants missing", () => {
    expect(
      allowTaskAction({
        isSuperAdmin: false,
        grantsByTaskType: null,
        taskType: "cetak",
        action: "confirm",
      }),
    ).toBe(false);
  });

  it("search+status combine over full dataset (40–100+)", () => {
    const many: OperationalTask[] = [];
    for (let i = 0; i < 120; i += 1) {
      many.push(
        task({
          task_key: `t-${i}`,
          task_type: i % 2 === 0 ? "cetak" : "screen",
          status:
            i % 3 === 0
              ? "selesai"
              : i % 3 === 1
                ? "sebagian"
                : "belum_dikerjakan",
          // 76 % 3 === 1 → sebagian
          outlet_name: i === 76 ? "Customer Khusus" : `Cust ${i}`,
          ref: { po_no: `PO-${i}` },
        }),
      );
    }
    const statusFiltered = filterTasksByStatus(many, "belum");
    const q = "customer khusus";
    const combined = statusFiltered.filter((t) => {
      const blob = `${t.outlet_name || ""} ${t.ref.po_no || ""}`.toLowerCase();
      return blob.includes(q);
    });
    // index 76 is "sebagian" → tidak masuk filter Belum
    expect(combined).toHaveLength(0);
    const sebagianHit = filterTasksByStatus(many, "sebagian").filter((t) =>
      `${t.outlet_name || ""}`.toLowerCase().includes(q),
    );
    expect(sebagianHit).toHaveLength(1);
    expect(sebagianHit[0].task_key).toBe("t-76");
    expect(many.length).toBe(120);
  });
});
