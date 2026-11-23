import { describe, expect, it } from "vitest";
import type { OperationalTask } from "../api/operational";
import {
  aggregateStatusCounts,
  allocationCanReviseQty,
  allocationReviseBounds,
  actionableOpenCount,
  allowTaskAction,
  canShowQtyConfirmForm,
  filterTasksByStatus,
  filterTasksForActor,
  formatCountBadge,
  isActionableOpenTask,
  isCancelledOrInactiveTask,
  isPreliminaryLoadingTask,
  isSchedulingTask,
  latestDraftSample,
  preliminaryLoadingDisplay,
  remainingQty,
  sampleStatusClass,
  sampleStatusLabel,
  screenAllocQty,
  shortfallLabel,
  taskDisplayLabel,
  taskItemDisplayName,
  taskItemLines,
  taskItemSecondaryMeta,
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

  it("badge formula Belum+Sebagian matches actionable open count", () => {
    const counts = aggregateStatusCounts(rows);
    expect(counts.belum + counts.sebagian).toBe(3);
  });

  it("excludes loading_cancelled from Belum/Sebagian", () => {
    const withCancel = [
      ...rows,
      task({
        task_key: "cancel",
        task_type: "loading-sj",
        status: "belum_dikerjakan",
        context: { loading_cancelled: true },
      }),
    ];
    const counts = aggregateStatusCounts(withCancel);
    expect(counts.belum).toBe(2);
    expect(counts.sebagian).toBe(1);
    expect(counts.all).toBe(5);
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

  it("labels non-cetak packing shortfall", () => {
    expect(shortfallLabel("qc-packing", "Non-Cetak")).toBe("Belum Di-Packing");
    expect(shortfallLabel("qc-packing")).toBe("Belum Di-QC & Packing");
  });

  it("prefers Nama Barang over item_code / line id", () => {
    expect(
      taskItemDisplayName(
        task({
          task_key: "n1",
          task_type: "screen",
          status: "belum_dikerjakan",
          ref: {
            item_code: "100359",
            accurate_line_id: "106318",
            item_name: "12 oz Oval Inject Bening Super",
          },
        }),
      ),
    ).toBe("12 oz Oval Inject Bening Super");
    expect(
      taskItemSecondaryMeta({
        ref: { item_code: "100359", accurate_line_id: "106318" },
      }),
    ).toBe("Kode 100359 · Line 106318");
  });

  it("falls back honestly when Nama Barang missing", () => {
    expect(
      taskItemDisplayName(
        task({
          task_key: "n2",
          task_type: "screen",
          status: "belum_dikerjakan",
          ref: { item_code: "100359", accurate_line_id: "106318" },
        }),
      ),
    ).toBe("Nama barang belum tersedia");
  });

  it("QC packing resolves Nama Barang from ref.item_name (authoritative line)", () => {
    expect(
      taskItemDisplayName(
        task({
          task_key: "qc1",
          task_type: "qc-packing",
          status: "belum_dikerjakan",
          ref: {
            accurate_po_id: "10001",
            accurate_line_id: "L-100",
            item_code: "CUP-12OZ",
            item_name: "Cup 12oz Cetak",
          },
        }),
      ),
    ).toBe("Cup 12oz Cetak");
  });

  it("multi-item lines keep distinct names by accurate_line_id", () => {
    const rows = taskItemLines(
      task({
        task_key: "multi",
        task_type: "loading-sj",
        status: "belum_dikerjakan",
        item_lines: [
          {
            accurate_line_id: "L-100",
            item_name: "Cup 12oz Cetak",
            qty: 2000,
            unit: "PCS",
          },
          {
            accurate_line_id: "L-301",
            item_name: "Lid 16oz Non-Cetak",
            qty: 1000,
            unit: "PCS",
          },
        ],
        ref: {},
      }),
    );
    expect(rows.map((r) => r.itemName)).toEqual([
      "Cup 12oz Cetak",
      "Lid 16oz Non-Cetak",
    ]);
    expect(rows.map((r) => r.accurateLineId)).toEqual(["L-100", "L-301"]);
  });

  it("skips item_lines rows without item_name (no invent)", () => {
    expect(
      taskItemLines(
        task({
          task_key: "sparse",
          task_type: "qc-packing",
          status: "belum_dikerjakan",
          item_lines: [
            {
              accurate_line_id: "L-100",
              item_name: "",
              qty: 1,
              unit: "PCS",
            },
          ],
          ref: { accurate_line_id: "L-100", item_code: "CUP" },
        }),
      ),
    ).toEqual([]);
  });

  it("Designer PO-scoped multi-item names stay compact multiline", () => {
    const design = task({
      task_key: "design-1",
      task_type: "design",
      status: "belum_dikerjakan",
      assigned_role: "designer",
      scope: "po",
      item_name: "Produk A\nProduk B",
      item_lines: [
        {
          accurate_line_id: "L1",
          item_name: "Produk A",
          qty: 1000,
          unit: "PCS",
        },
        {
          accurate_line_id: "L2",
          item_name: "Produk B",
          qty: 2000,
          unit: "PCS",
        },
      ],
      ref: { accurate_po_id: "10001", po_no: "PO/1" },
    });
    expect(taskItemDisplayName(design)).toBe("Produk A\nProduk B");
    expect(taskItemLines(design).map((l) => l.itemName)).toEqual([
      "Produk A",
      "Produk B",
    ]);
    expect(taskItemDisplayName(design)).not.toBe("Produk A");
  });

  it("shows Screen qty confirm form without qty_target", () => {
    expect(
      canShowQtyConfirmForm({
        task_type: "screen",
        qty_target: undefined,
      }),
    ).toBe(true);
    expect(
      canShowQtyConfirmForm({
        task_type: "cetak",
        qty_target: undefined,
      }),
    ).toBe(false);
    expect(
      canShowQtyConfirmForm({
        task_type: "cetak",
        qty_target: 100,
      }),
    ).toBe(true);
  });

  it("hides qty confirm for preliminary Loading menunggu SJ", () => {
    expect(isPreliminaryLoadingTask("loading-menunggu-sj")).toBe(true);
    expect(
      canShowQtyConfirmForm({
        task_type: "loading-menunggu-sj",
        qty_target: 500,
      }),
    ).toBe(false);
    expect(
      taskDisplayLabel(
        task({
          task_key: "p1",
          task_type: "loading-menunggu-sj",
          status: "belum_dikerjakan",
          label: "Loading — Menunggu Surat Jalan",
        }),
      ),
    ).toBe("Loading — Menunggu Surat Jalan");
    const waiting = preliminaryLoadingDisplay({
      context: {
        waiting_for_sj: true,
        qty_printed: 200,
        display_status: "Menunggu Surat Jalan",
      },
    });
    expect(waiting.waiting).toBe(true);
    expect(waiting.statusText).toBe("Menunggu Surat Jalan");
    expect(waiting.sjLabel).toBe("Belum tersedia");
    expect(waiting.qtyPrinted).toBe(200);
    const linked = preliminaryLoadingDisplay({
      context: {
        waiting_for_sj: false,
        loading_task_ready: true,
        linked_sj_ids: ["SJ-1"],
        linked_sj_nos: ["SJ/001"],
      },
    });
    expect(linked.waiting).toBe(false);
    expect(linked.pendingLoading).toBe(false);
    expect(linked.loadingReady).toBe(true);
    expect(linked.sjLabel).toBe("SJ/001");
  });

  it("keeps SJ-without-loading-task preliminary actionable; ready closes it", () => {
    const waiting = task({
      task_key: "p-wait",
      task_type: "loading-menunggu-sj",
      status: "belum_dikerjakan",
      context: { waiting_for_sj: true, qty_printed: 100 },
    });
    const pending = task({
      task_key: "p-pending",
      task_type: "loading-menunggu-sj",
      status: "belum_dikerjakan",
      context: {
        waiting_for_sj: false,
        loading_task_ready: false,
        linked_sj_ids: ["SJ-2"],
        missing_loading_sj_ids: ["SJ-2"],
        display_status: "SJ tersedia — tugas Loading belum siap",
      },
    });
    const ready = task({
      task_key: "p-ready",
      task_type: "loading-menunggu-sj",
      status: "selesai",
      context: {
        waiting_for_sj: false,
        loading_task_ready: true,
        linked_sj_ids: ["SJ-1"],
        linked_sj_nos: ["SJ/001"],
      },
    });
    expect(isActionableOpenTask(waiting)).toBe(true);
    expect(isActionableOpenTask(pending)).toBe(true);
    expect(isCancelledOrInactiveTask(pending)).toBe(false);
    expect(isActionableOpenTask(ready)).toBe(false);
    expect(isCancelledOrInactiveTask(ready)).toBe(true);
    expect(actionableOpenCount([waiting, pending, ready])).toBe(2);
    const pendingView = preliminaryLoadingDisplay(pending);
    expect(pendingView.pendingLoading).toBe(true);
    expect(pendingView.statusText).toContain("belum siap");
  });

  it("reads Screen qty_alokasi as info only", () => {
    expect(
      screenAllocQty(
        task({
          task_key: "s1",
          task_type: "screen",
          status: "belum_dikerjakan",
          context: { qty_alokasi: 250 },
        }),
      ),
    ).toBe(250);
    expect(
      screenAllocQty(
        task({
          task_key: "c1",
          task_type: "cetak",
          status: "belum_dikerjakan",
          context: { qty_alokasi: 250 },
        }),
      ),
    ).toBeNull();
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
  it("does not hide tasks by assignment metadata (Owner contract)", () => {
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
      task({
        task_key: "d-empty",
        task_type: "pengiriman",
        status: "belum_dikerjakan",
        context: {},
      }),
    ];
    const filtered = filterTasksForActor(rows, {
      isSuperAdmin: false,
      actorEmail: "opa@eternal.local",
      operationalRole: "operator",
    });
    expect(filtered.map((t) => t.task_key)).toEqual(["t1", "t2", "d-empty"]);
  });

  it("passes through for operations.full", () => {
    const rows = [
      task({
        task_key: "x1",
        task_type: "pengiriman",
        status: "belum_dikerjakan",
        context: {},
      }),
    ];
    expect(
      filterTasksForActor(rows, {
        isSuperAdmin: false,
        actorEmail: "ops@eternal.local",
        operationalRole: "helper",
        operationsFull: true,
      }).map((t) => t.task_key),
    ).toEqual(["x1"]);
  });

  it("Holding view_all sees Loading SJ of other assignees (read parity)", () => {
    const rows = [
      task({
        task_key: "load-a",
        task_type: "loading-sj",
        status: "belum_dikerjakan",
        assigned_role: "helper",
        context: { assigned_user_email: "helper-a@eternal.local" },
      }),
      task({
        task_key: "load-b",
        task_type: "loading-sj",
        status: "belum_dikerjakan",
        assigned_role: "helper",
        context: { assigned_user_email: "helper-b@eternal.local" },
      }),
      task({
        task_key: "cetak-1",
        task_type: "cetak",
        status: "belum_dikerjakan",
        assigned_role: "operator",
      }),
    ];
    const holding = filterTasksForActor(rows, {
      isSuperAdmin: false,
      actorEmail: "holding@eternal.local",
      operationalRole: null,
      viewAllTasks: true,
    });
    expect(holding.map((t) => t.task_key)).toEqual([
      "load-a",
      "load-b",
      "cetak-1",
    ]);
  });

  it("Helper without view_all only sees own Loading SJ assignee", () => {
    const rows = [
      task({
        task_key: "load-a",
        task_type: "loading-sj",
        status: "belum_dikerjakan",
        assigned_role: "helper",
        context: { assigned_user_email: "helper-a@eternal.local" },
      }),
      task({
        task_key: "load-b",
        task_type: "loading-sj",
        status: "belum_dikerjakan",
        assigned_role: "helper",
        context: { assigned_user_email: "helper-b@eternal.local" },
      }),
    ];
    const filtered = filterTasksForActor(rows, {
      isSuperAdmin: false,
      actorEmail: "helper-a@eternal.local",
      operationalRole: "helper",
      viewAllTasks: false,
    });
    expect(filtered.map((t) => t.task_key)).toEqual(["load-a"]);
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
