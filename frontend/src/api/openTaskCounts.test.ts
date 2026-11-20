import { afterEach, describe, expect, it, vi } from "vitest";
import type { OperationalTask } from "./operational";
import {
  aggregateOpenCountsByRole,
  emptyOpenTaskCounts,
  fetchOpenTeamTaskCountsFromApi,
  formatOpenTaskBadge,
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
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reads authoritative counts from /api/tasks/open-counts", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        counts: {
          designer: 3,
          helper: 1,
          operator: 0,
          driver: 0,
          screener: 0,
          "production-head": 0,
        },
        truncated: false,
        source: "mongodb",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchOpenTeamTaskCountsFromApi();
    expect(result.status).toBe("ready");
    expect(result.counts.designer).toBe(3);
    expect(result.counts.helper).toBe(1);
    expect(result.counts.operator).toBe(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/tasks/open-counts");
  });

  it("keeps accurate totals above the old list limit of 1000", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        counts: { designer: 1500, helper: 0, operator: 0, driver: 0, screener: 0, "production-head": 0 },
        truncated: false,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await fetchOpenTeamTaskCountsFromApi();
    expect(result.status).toBe("ready");
    expect(result.counts.designer).toBe(1500);
    expect(formatOpenTaskBadge(1500, result.status)).toBe("1500");
  });

  it("surfaces HTTP errors without fake zeros as ready", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }),
    );
    const result = await fetchOpenTeamTaskCountsFromApi();
    expect(result.status).toBe("error");
    expect(result.counts.designer).toBe(0);
  });
});

describe("formatOpenTaskBadge", () => {
  it("shows loading and error indicators instead of fake zero", () => {
    expect(formatOpenTaskBadge(0, "loading")).toBe("…");
    expect(formatOpenTaskBadge(0, "error")).toBe("?");
    expect(formatOpenTaskBadge(0, "ready")).toBeNull();
  });

  it("marks truncated counts as lower bounds when backend signals truncation", () => {
    expect(formatOpenTaskBadge(1000, "truncated")).toBe("1000+");
    expect(formatOpenTaskBadge(5, "truncated")).toBe("5+");
    expect(formatOpenTaskBadge(3, "ready")).toBe("3");
  });
});
