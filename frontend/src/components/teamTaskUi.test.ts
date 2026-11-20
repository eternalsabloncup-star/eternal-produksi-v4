import { describe, expect, it } from "vitest";
import type { OperationalTask } from "../api/operational";
import {
  aggregateStatusCounts,
  allocationCanReviseQty,
  allocationReviseBounds,
  allowTaskAction,
  filterTasksByStatus,
  filterTasksForActor,
  formatCountBadge,
  isSchedulingTask,
  latestDraftSample,
  remainingQty,
  sampleStatusClass,
  sampleStatusLabel,
  taskDisplayLabel,
} from "./teamTaskUi";

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

describe("teamTaskUi counts & filters", () => {
  const rows = [
    task({ task_key: "a", task_type: "screen", status: "belum_dikerjakan" }),
    task({ task_key: "b", task_type: "screen", status: "sebagian" }),
    task({ task_key: "c", task_type: "screen", status: "selesai" }),
    task({ task_key: "d", task_type: "screen", status: "belum_dikerjakan" }),
  ];

  it("aggregates Semua / Belum / Sebagian / Selesai from full list", () => {
    expect(aggregateStatusCounts(rows)).toEqual({
      all: 4,
      belum: 2,
      sebagian: 1,
      selesai: 1,
    });
  });

  it("filters by status without mutating source counts", () => {
    const sebagian = filterTasksByStatus(rows, "sebagian");
    expect(sebagian).toHaveLength(1);
    expect(aggregateStatusCounts(rows).all).toBe(4);
    expect(filterTasksByStatus(rows, "belum")).toHaveLength(2);
    expect(filterTasksByStatus(rows, "selesai")).toHaveLength(1);
  });

  it("marks truncated badges", () => {
    expect(formatCountBadge(12, false)).toBe("12");
    expect(formatCountBadge(12, true)).toBe("12+");
  });
});

describe("teamTaskUi labels & scheduling", () => {
  it("renames failed-delivery coordination for Owner wording", () => {
    expect(
      taskDisplayLabel({
        task_type: "pengiriman-gagal-koordinasi",
        label: "Pengiriman Gagal — Koordinasi Administrasi",
      }),
    ).toBe("Koordinasi Pengiriman Gagal");
  });

  it("detects scheduling task types", () => {
    expect(isSchedulingTask("penjadwalan")).toBe(true);
    expect(isSchedulingTask("penjadwalan-ulang")).toBe(true);
    expect(isSchedulingTask("cetak")).toBe(false);
  });

  it("computes remaining qty", () => {
    expect(
      remainingQty(
        task({
          task_key: "x",
          task_type: "cetak",
          status: "sebagian",
          qty_target: 100,
          qty_confirmed: 40,
        }),
      ),
    ).toBe(60);
  });
});

describe("allowTaskAction", () => {
  it("super admin always allowed", () => {
    expect(
      allowTaskAction({
        isSuperAdmin: true,
        grantsByTaskType: new Map(),
        taskType: "cetak",
        action: "confirm",
      }),
    ).toBe(true);
  });

  it("uses Master grants when present", () => {
    const grants = new Map([["cetak", ["view" as const]]]);
    expect(
      allowTaskAction({
        isSuperAdmin: false,
        grantsByTaskType: grants,
        taskType: "cetak",
        action: "confirm",
      }),
    ).toBe(false);
    expect(
      allowTaskAction({
        isSuperAdmin: false,
        grantsByTaskType: grants,
        taskType: "cetak",
        action: "view",
      }),
    ).toBe(true);
  });

  it("fail-closed when grants not loaded", () => {
    expect(
      allowTaskAction({
        isSuperAdmin: false,
        grantsByTaskType: null,
        taskType: "cetak",
        action: "confirm",
      }),
    ).toBe(false);
  });
});

describe("filterTasksForActor", () => {
  it("hides foreign operator allocations for operator role", () => {
    const rows = [
      task({
        task_key: "t1",
        task_type: "cetak",
        status: "belum_dikerjakan",
        context: { operator_email: "opa@eternal.local" },
      }),
      task({
        task_key: "t2",
        task_type: "cetak",
        status: "belum_dikerjakan",
        context: { operator_email: "opb@eternal.local" },
      }),
    ];
    const filtered = filterTasksForActor(rows, {
      isSuperAdmin: false,
      actorEmail: "opa@eternal.local",
      operationalRole: "operator",
    });
    expect(filtered.map((t) => t.task_key)).toEqual(["t1"]);
  });

  it("hides foreign driver tasks", () => {
    const rows = [
      task({
        task_key: "d1",
        task_type: "pengiriman",
        status: "belum_dikerjakan",
        context: { assigned_driver_email: "drva@eternal.local" },
      }),
      task({
        task_key: "d2",
        task_type: "pengiriman",
        status: "belum_dikerjakan",
        context: { assigned_driver_email: "drvb@eternal.local" },
      }),
    ];
    const filtered = filterTasksForActor(rows, {
      isSuperAdmin: false,
      actorEmail: "drva@eternal.local",
      operationalRole: "driver",
    });
    expect(filtered.map((t) => t.task_key)).toEqual(["d1"]);
  });
});

describe("sample ulang labels", () => {
  it("shows Sampel Ulang when needs_resubmit", () => {
    expect(
      taskDisplayLabel({
        task_type: "konfirmasi-sample",
        label: "Konfirmasi Sampel",
        context: { needs_resubmit: true, status: "ditolak" },
      }),
    ).toBe("Sampel Ulang");
  });
});

describe("sample draft helpers", () => {
  it("labels draft status", () => {
    expect(sampleStatusLabel("draft")).toBe("Draft");
    expect(sampleStatusClass("draft")).toContain("is-draft");
  });

  it("picks latest draft version", () => {
    expect(
      latestDraftSample([
        { status: "ditolak", version: 1 },
        { status: "draft", version: 2 },
        { status: "draft", version: 3 },
        { status: "menunggu_approval", version: 4 },
      ])?.version,
    ).toBe(3);
    expect(latestDraftSample([{ status: "disetujui", version: 1 }])).toBeNull();
  });
});

describe("allocation revise eligibility", () => {
  it("uses can_revise from API when present", () => {
    expect(
      allocationCanReviseQty({
        status: "active",
        can_revise: true,
        qty_printed: 800,
      }),
    ).toBe(true);
    expect(
      allocationCanReviseQty({
        status: "active",
        can_revise: false,
        qty_printed: 0,
      }),
    ).toBe(false);
  });

  it("falls back to active status when can_revise absent", () => {
    expect(allocationCanReviseQty({ status: "active", qty_printed: 500 })).toBe(
      true,
    );
    expect(
      allocationCanReviseQty({ status: "released", qty_printed: 0 }),
    ).toBe(false);
  });

  it("computes min from printed and max from capacity", () => {
    expect(
      allocationReviseBounds({
        qty: 2000,
        qty_printed: 1000,
        qty_revise_min: 1000,
        qty_revise_max: 3000,
      }),
    ).toEqual({ min: 1000, max: 3000 });
    expect(
      allocationReviseBounds({
        qty: 2000,
        qty_printed: 1000,
        lineQtyTarget: 5000,
        otherActiveQty: 2000,
      }),
    ).toEqual({ min: 1000, max: 3000 });
  });
});
