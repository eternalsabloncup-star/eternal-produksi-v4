import { describe, expect, it, vi } from "vitest";
import type { OperationalTask } from "./operational";

vi.mock("./operational", async () => {
  const actual = await vi.importActual<typeof import("./operational")>(
    "./operational",
  );
  return {
    ...actual,
    fetchTasks: vi.fn(),
  };
});

import { fetchTasks } from "./operational";
import {
  aggregateOpenCountsByRole,
  emptyOpenTaskCounts,
  fetchOpenTeamTaskCountsFromApi,
  formatOpenTaskBadge,
  OPEN_TASK_FETCH_LIMIT,
} from "./openTaskCounts";
import { actionableOpenCount } from "../components/teamTaskUi";

function task(
  role: OperationalTask["assigned_role"],
  key: string,
  status: OperationalTask["status"] = "belum_dikerjakan",
  extra?: Partial<OperationalTask>,
): OperationalTask {
  return {
    task_key: key,
    task_type: "cetak",
    scope: "po_line_allocation",
    assigned_role: role,
    status,
    qty_confirmed: 0,
    ref: {},
    ...extra,
  };
}

describe("aggregateOpenCountsByRole", () => {
  it("counts open tasks per assigned_role and skips selesai", () => {
    const counts = aggregateOpenCountsByRole([
      task("designer", "a"),
      task("designer", "b"),
      task("designer", "done", "selesai"),
      task("operator", "c"),
      task("driver", "d"),
    ]);
    expect(counts.designer).toBe(2);
    expect(counts.operator).toBe(1);
    expect(counts.driver).toBe(1);
    expect(counts.helper).toBe(0);
  });

  it("dedupes task_key and excludes loading_cancelled", () => {
    const counts = aggregateOpenCountsByRole([
      task("designer", "same"),
      task("designer", "same"),
      task("designer", "x", "belum_dikerjakan", {
        context: { loading_cancelled: true },
      }),
      task("helper", "h1", "sebagian"),
    ]);
    expect(counts.designer).toBe(1);
    expect(counts.helper).toBe(1);
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

describe("actionableOpenCount / badge formula", () => {
  it("Designer 3 Belum + 1 Selesai → badge 3", () => {
    const items = [
      task("designer", "1"),
      task("designer", "2"),
      task("designer", "3"),
      task("designer", "4", "selesai"),
    ];
    expect(actionableOpenCount(items)).toBe(3);
  });

  it("2 Belum + 2 Sebagian + 3 Selesai → badge 4", () => {
    const items = [
      task("operator", "b1"),
      task("operator", "b2"),
      task("operator", "s1", "sebagian"),
      task("operator", "s2", "sebagian"),
      task("operator", "d1", "selesai"),
      task("operator", "d2", "selesai"),
      task("operator", "d3", "selesai"),
    ];
    expect(actionableOpenCount(items)).toBe(4);
  });

  it("all selesai → badge 0", () => {
    expect(
      actionableOpenCount([
        task("screener", "a", "selesai"),
        task("screener", "b", "selesai"),
      ]),
    ).toBe(0);
  });

  it("cancelled loading task not counted", () => {
    expect(
      actionableOpenCount([
        task("driver", "x", "belum_dikerjakan", {
          context: { loading_cancelled: true },
        }),
        task("driver", "y"),
      ]),
    ).toBe(1);
  });
});

describe("fetchOpenTeamTaskCountsFromApi", () => {
  it("aggregates per-role done=false into sidebar counts", async () => {
    vi.mocked(fetchTasks).mockImplementation(
      async (params: { role?: string; done?: boolean }) => {
        expect(params.done).toBe(false);
        const items: OperationalTask[] = [];
        if (params.role === "designer") {
          items.push(
            task("designer", "d1"),
            task("designer", "d2"),
            task("designer", "d3"),
          );
        }
        if (params.role === "helper") {
          items.push(task("helper", "h1", "sebagian"));
        }
        return { items, total: items.length, source: "test" };
      },
    );
    const result = await fetchOpenTeamTaskCountsFromApi();
    expect(result.status).toBe("ready");
    expect(result.counts.designer).toBe(3);
    expect(result.counts.helper).toBe(1);
    expect(result.counts.operator).toBe(0);
    expect(result.counts.driver).toBe(0);
    expect(result.counts.screener).toBe(0);
    expect(result.counts["production-head"]).toBe(0);
    expect(fetchTasks).toHaveBeenCalledTimes(6);
  });
});

describe("formatOpenTaskBadge", () => {
  it("shows loading and error indicators instead of fake zero", () => {
    expect(formatOpenTaskBadge(0, "loading")).toBe("…");
    expect(formatOpenTaskBadge(0, "error")).toBe("?");
    expect(formatOpenTaskBadge(0, "ready")).toBeNull();
  });

  it("marks truncated counts as lower bounds", () => {
    expect(formatOpenTaskBadge(OPEN_TASK_FETCH_LIMIT, "truncated")).toBe(
      `${OPEN_TASK_FETCH_LIMIT}+`,
    );
    expect(formatOpenTaskBadge(5, "truncated")).toBe("5+");
    expect(formatOpenTaskBadge(3, "ready")).toBe("3");
  });
});
