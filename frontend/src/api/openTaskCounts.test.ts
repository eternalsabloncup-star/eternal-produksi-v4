import { describe, expect, it } from "vitest";
import type { OperationalTask } from "./operational";
import {
  aggregateOpenCountsByRole,
  emptyOpenTaskCounts,
  formatOpenTaskBadge,
  OPEN_TASK_FETCH_LIMIT,
} from "./openTaskCounts";

function task(
  role: OperationalTask["assigned_role"],
  key: string,
): OperationalTask {
  return {
    task_key: key,
    task_type: "cetak",
    scope: "po_line_allocation",
    assigned_role: role,
    status: "belum_dikerjakan",
    qty_confirmed: 0,
    ref: {},
  };
}

describe("aggregateOpenCountsByRole", () => {
  it("counts open tasks per assigned_role", () => {
    const counts = aggregateOpenCountsByRole([
      task("designer", "a"),
      task("designer", "b"),
      task("operator", "c"),
      task("driver", "d"),
    ]);
    expect(counts.designer).toBe(2);
    expect(counts.operator).toBe(1);
    expect(counts.driver).toBe(1);
    expect(counts.helper).toBe(0);
    expect(counts.screener).toBe(0);
    expect(counts["production-head"]).toBe(0);
  });

  it("starts from empty zeros for all six roles", () => {
    const empty = emptyOpenTaskCounts();
    expect(Object.keys(empty).sort()).toEqual(
      [
        "designer",
        "driver",
        "helper",
        "operator",
        "production-head",
        "screener",
      ].sort(),
    );
    expect(Object.values(empty).every((n) => n === 0)).toBe(true);
  });
});

describe("formatOpenTaskBadge", () => {
  it("shows loading and error indicators instead of fake zero", () => {
    expect(formatOpenTaskBadge(0, "loading")).toBe("…");
    expect(formatOpenTaskBadge(0, "error")).toBe("?");
    expect(formatOpenTaskBadge(0, "ready")).toBeNull();
  });

  it("marks truncated ceiling", () => {
    expect(formatOpenTaskBadge(OPEN_TASK_FETCH_LIMIT, "truncated")).toBe(
      `${OPEN_TASK_FETCH_LIMIT}+`,
    );
    expect(formatOpenTaskBadge(3, "ready")).toBe("3");
  });
});
