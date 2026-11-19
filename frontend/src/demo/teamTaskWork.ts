/**
 * Fixture pekerjaan Tugas Tim per role (DEMO frontend).
 * Identitas PO / item / alokasi / SJ konsisten — bukan workflow backend.
 */

import type { TeamTaskRoleId } from "../app/nav";
import {
  listAllocations,
  listPrintItems,
  remainingQtyForItem,
  subscribeProductionAlloc,
  type DemoAllocation,
  type DemoPrintItem,
} from "./productionHeadAlloc";

export type DoneFilter = "all" | "done" | "open";

export type DesignerTask = {
  id: string;
  poNo: string;
  customer: string;
  designStatus: string;
  target: string;
  pdfUrl: string | null;
  done: boolean;
};

export type ScreenerTask = {
  id: string;
  poNo: string;
  customer: string;
  itemId: string;
  itemName: string;
  printColor: string;
  done: boolean;
};

export type HelperCategory = "prep-cup" | "qc-packing";

export type HelperTask = {
  id: string;
  category: HelperCategory;
  poNo: string;
  customer: string;
  itemId: string;
  itemName: string;
  done: boolean;
};

export type DriverTask = {
  id: string;
  sjNo: string;
  poNo: string;
  customer: string;
  qtyShip: number;
  done: boolean;
};

const designers: DesignerTask[] = [
  {
    id: "des-DEMO-TASK-PO-01",
    poNo: "DEMO-TASK-PO-01",
    customer: "Demo Mitra Produksi (SIM)",
    designStatus: "Revisi",
    target: "2026-03-12",
    pdfUrl: "/demo/design-sample.pdf",
    done: false,
  },
  {
    id: "des-DEMO-TASK-PO-02",
    poNo: "DEMO-TASK-PO-02",
    customer: "Demo Cahaya Packindo (SIM)",
    designStatus: "Draft",
    target: "2026-03-14",
    pdfUrl: "/demo/design-sample.pdf",
    done: false,
  },
  {
    id: "des-DEMO-TASK-PO-12",
    poNo: "DEMO-TASK-PO-12",
    customer: "Demo Prima Label Test (SIM)",
    designStatus: "Final (SIM)",
    target: "2026-03-05",
    pdfUrl: "/demo/design-sample.pdf",
    done: true,
  },
];

/** Screener: satu baris per item Cetak. */
function buildScreenerSeed(): ScreenerTask[] {
  const colors = ["Hijau Tua", "Emas Soft", "Hitam", "Biru Soft", "Merah"];
  return listPrintItems().map((it, i) => ({
    id: `scr-${it.id}`,
    poNo: it.poNo,
    customer: it.customer,
    itemId: it.id,
    itemName: it.itemName,
    printColor: colors[i % colors.length],
    done: i === 3, // satu selesai untuk badge
  }));
}

let screenerTasks = buildScreenerSeed();

function buildHelperSeed(): HelperTask[] {
  const items = listPrintItems();
  const rows: HelperTask[] = [];
  for (const it of items) {
    rows.push({
      id: `hlp-prep-${it.id}`,
      category: "prep-cup",
      poNo: it.poNo,
      customer: it.customer,
      itemId: it.id,
      itemName: it.itemName,
      done: it.id === "item-demo-12a",
    });
    rows.push({
      id: `hlp-qc-${it.id}`,
      category: "qc-packing",
      poNo: it.poNo,
      customer: it.customer,
      itemId: it.id,
      itemName: it.itemName,
      done: it.id === "item-demo-01b",
    });
  }
  return rows;
}

let helperTasks = buildHelperSeed();

/** Operator done flags keyed by allocation id. */
const operatorDone = new Map<string, boolean>([
  ["alloc-seed-1", false],
  ["alloc-seed-2", true],
  ["alloc-seed-3", false],
  ["alloc-seed-4", false],
]);

let drivers: DriverTask[] = [
  {
    id: "drv-DEMO-TASK-SJ-01",
    sjNo: "DEMO-TASK-SJ-01",
    poNo: "DEMO-TASK-PO-15",
    customer: "Demo Biru Laut Sample (SIM)",
    qtyShip: 2000,
    done: false,
  },
  {
    id: "drv-DEMO-TASK-SJ-02",
    sjNo: "DEMO-TASK-SJ-02",
    poNo: "DEMO-TASK-PO-15",
    customer: "Demo Biru Laut Sample (SIM)",
    qtyShip: 1500,
    done: false,
  },
  {
    id: "drv-DEMO-TASK-SJ-03",
    sjNo: "DEMO-TASK-SJ-03",
    poNo: "DEMO-TASK-PO-17",
    customer: "Demo Angkasa Print Lab (SIM)",
    qtyShip: 3200,
    done: true,
  },
];

const listeners = new Set<() => void>();

function notify() {
  for (const l of listeners) l();
}

export function subscribeTeamTaskWork(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Juga dengarkan alokasi KP agar badge Operator/KP ikut berubah. */
subscribeProductionAlloc(() => notify());

export function listDesignerTasks(): DesignerTask[] {
  return designers.map((d) => ({ ...d }));
}

export function listScreenerTasks(): ScreenerTask[] {
  return screenerTasks.map((t) => ({ ...t }));
}

export function listHelperTasks(category: HelperCategory): HelperTask[] {
  return helperTasks.filter((t) => t.category === category).map((t) => ({ ...t }));
}

export type OperatorTaskRow = DemoAllocation & { done: boolean };

export function listOperatorTasks(): OperatorTaskRow[] {
  return listAllocations().map((a) => ({
    ...a,
    done: operatorDone.get(a.id) ?? false,
  }));
}

export function listDriverTasks(): DriverTask[] {
  return drivers.map((d) => ({ ...d }));
}

/** Kepala Produksi: selesai bila sisa Qty item = 0. */
export function productionHeadDoneCounts(): { done: number; open: number; total: number } {
  const rows = listPrintItems();
  let done = 0;
  let open = 0;
  for (const it of rows) {
    if (remainingQtyForItem(it.id) <= 0) done += 1;
    else open += 1;
  }
  return { done, open, total: rows.length };
}

function countDoneOpen(rows: { done: boolean }[]): {
  done: number;
  open: number;
  total: number;
} {
  let done = 0;
  let open = 0;
  for (const r of rows) {
    if (r.done) done += 1;
    else open += 1;
  }
  return { done, open, total: rows.length };
}

export function getRoleDoneCounts(
  roleId: TeamTaskRoleId,
  helperCategory?: HelperCategory,
): { done: number; open: number; total: number } {
  switch (roleId) {
    case "production-head":
      return productionHeadDoneCounts();
    case "designer":
      return countDoneOpen(designers);
    case "screener":
      return countDoneOpen(screenerTasks);
    case "helper":
      return countDoneOpen(
        helperTasks.filter((t) =>
          helperCategory ? t.category === helperCategory : true,
        ),
      );
    case "operator":
      return countDoneOpen(listOperatorTasks());
    case "driver":
      return countDoneOpen(drivers);
    default:
      return { done: 0, open: 0, total: 0 };
  }
}

export function markDesignerDone(id: string): void {
  const i = designers.findIndex((d) => d.id === id);
  if (i < 0) return;
  designers[i] = { ...designers[i], done: true };
  notify();
}

export function markScreenerDone(id: string): void {
  screenerTasks = screenerTasks.map((t) =>
    t.id === id ? { ...t, done: true } : t,
  );
  notify();
}

export function markHelperDone(id: string): void {
  helperTasks = helperTasks.map((t) =>
    t.id === id ? { ...t, done: true } : t,
  );
  notify();
}

export function markOperatorDone(allocId: string): void {
  operatorDone.set(allocId, true);
  notify();
}

export function markDriverDone(id: string): void {
  drivers = drivers.map((d) => (d.id === id ? { ...d, done: true } : d));
  notify();
}

/** Item Non-Cetak hanya dokumentasi DEMO — tidak masuk daftar KP. */
export const DEMO_NON_PRINT_ITEMS: Pick<
  DemoPrintItem,
  "id" | "poNo" | "customer" | "itemName"
>[] = [
  {
    id: "item-demo-01-nonprint",
    poNo: "DEMO-TASK-PO-01",
    customer: "Demo Mitra Produksi (SIM)",
    itemName: "Sticker Bonus (Tidak Cetak) DEMO",
  },
];

export function filterByDone<T extends { done: boolean }>(
  rows: T[],
  filter: DoneFilter,
): T[] {
  if (filter === "done") return rows.filter((r) => r.done);
  if (filter === "open") return rows.filter((r) => !r.done);
  return rows;
}
