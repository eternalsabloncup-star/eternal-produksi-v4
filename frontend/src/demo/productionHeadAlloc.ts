/**
 * Alokasi operator Kepala Produksi + sumber Jadwal Produksi (DEMO frontend).
 * Single source of truth sesi UI — bukan assignment/backend/MongoDB.
 */

export type DemoOperator = {
  id: string;
  name: string;
};

export type DemoScreenOption = "Kecil" | "Besar";

/** Item line proses Cetak yang dapat dialokasikan. */
export type DemoPrintItem = {
  id: string;
  poNo: string;
  customer: string;
  itemName: string;
  process: "Cetak";
  qtyPo: number;
  /** YYYY-MM-DD bila tersedia di fixture; null = tidak dikarang. */
  designApprovalDate: string | null;
  pdfUrl: string | null;
};

/** Satu record alokasi — baris terpisah di Jadwal Produksi. */
export type DemoAllocation = {
  id: string;
  poNo: string;
  customer: string;
  itemId: string;
  itemName: string;
  operatorId: string;
  operatorName: string;
  qty: number;
  screen: DemoScreenOption;
  workDate: string;
};

export const DEMO_OPERATORS: DemoOperator[] = [
  { id: "op-demo-1", name: "Demo Operator 1" },
  { id: "op-demo-2", name: "Demo Operator 2" },
  { id: "op-demo-3", name: "Demo Operator 3" },
];

/** Pilihan screen DEMO terpusat — nanti diganti master. */
export const DEMO_SCREEN_OPTIONS: DemoScreenOption[] = ["Kecil", "Besar"];

const PRINT_ITEMS: DemoPrintItem[] = [
  {
    id: "item-demo-01a",
    poNo: "DEMO-TASK-PO-01",
    customer: "Demo Mitra Produksi (SIM)",
    itemName: "Cup PET 12oz Demo Motif A",
    process: "Cetak",
    qtyPo: 5000,
    designApprovalDate: "2026-03-08",
    pdfUrl: "/demo/design-sample.pdf",
  },
  {
    id: "item-demo-01b",
    poNo: "DEMO-TASK-PO-01",
    customer: "Demo Mitra Produksi (SIM)",
    itemName: "Cup PET 16oz Demo Motif B",
    process: "Cetak",
    qtyPo: 2400,
    designApprovalDate: null,
    pdfUrl: null,
  },
  {
    id: "item-demo-02a",
    poNo: "DEMO-TASK-PO-02",
    customer: "Demo Cahaya Packindo (SIM)",
    itemName: "Cup PP 14oz Demo Logo",
    process: "Cetak",
    qtyPo: 3600,
    designApprovalDate: "2026-03-10",
    pdfUrl: "/demo/design-sample.pdf",
  },
  {
    id: "item-demo-12a",
    poNo: "DEMO-TASK-PO-12",
    customer: "Demo Prima Label Test (SIM)",
    itemName: "Cup PET 12oz Demo Run",
    process: "Cetak",
    qtyPo: 1800,
    designApprovalDate: "2026-03-05",
    pdfUrl: "/demo/design-sample.pdf",
  },
  {
    id: "item-demo-13a",
    poNo: "DEMO-TASK-PO-13",
    customer: "Demo Angkasa Print Lab (SIM)",
    itemName: "Cup PET 9oz Demo Pack",
    process: "Cetak",
    qtyPo: 4200,
    designApprovalDate: null,
    pdfUrl: "/demo/design-sample.pdf",
  },
];

/** Seed alokasi awal — contoh split multi-operator (DEMO). */
let allocations: DemoAllocation[] = [
  {
    id: "alloc-seed-1",
    poNo: "DEMO-TASK-PO-01",
    customer: "Demo Mitra Produksi (SIM)",
    itemId: "item-demo-01b",
    itemName: "Cup PET 16oz Demo Motif B",
    operatorId: "op-demo-1",
    operatorName: "Demo Operator 1",
    qty: 800,
    screen: "Kecil",
    workDate: "2026-03-20",
  },
  {
    id: "alloc-seed-2",
    poNo: "DEMO-TASK-PO-12",
    customer: "Demo Prima Label Test (SIM)",
    itemId: "item-demo-12a",
    itemName: "Cup PET 12oz Demo Run",
    operatorId: "op-demo-2",
    operatorName: "Demo Operator 2",
    qty: 1000,
    screen: "Besar",
    workDate: "2026-03-21",
  },
  {
    id: "alloc-seed-3",
    poNo: "DEMO-TASK-PO-01",
    customer: "Demo Mitra Produksi (SIM)",
    itemId: "item-demo-01b",
    itemName: "Cup PET 16oz Demo Motif B",
    operatorId: "op-demo-2",
    operatorName: "Demo Operator 2",
    qty: 700,
    screen: "Besar",
    workDate: "2026-03-20",
  },
  {
    id: "alloc-seed-4",
    poNo: "DEMO-TASK-PO-01",
    customer: "Demo Mitra Produksi (SIM)",
    itemId: "item-demo-01b",
    itemName: "Cup PET 16oz Demo Motif B",
    operatorId: "op-demo-3",
    operatorName: "Demo Operator 3",
    qty: 900,
    screen: "Kecil",
    workDate: "2026-03-22",
  },
];

let allocSeq = 5;

const listeners = new Set<() => void>();

function notify() {
  for (const l of listeners) l();
}

export function subscribeProductionAlloc(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function listPrintItems(): DemoPrintItem[] {
  return PRINT_ITEMS.map((i) => ({ ...i }));
}

export function getPrintItem(itemId: string): DemoPrintItem | null {
  const hit = PRINT_ITEMS.find((i) => i.id === itemId);
  return hit ? { ...hit } : null;
}

export function listPrintItemsForPo(poNo: string): DemoPrintItem[] {
  return PRINT_ITEMS.filter((i) => i.poNo === poNo).map((i) => ({ ...i }));
}

export function listAllocations(): DemoAllocation[] {
  return allocations.map((a) => ({ ...a }));
}

export function listAllocationsForItem(itemId: string): DemoAllocation[] {
  return allocations.filter((a) => a.itemId === itemId).map((a) => ({ ...a }));
}

export function allocatedQtyForItem(itemId: string): number {
  return allocations
    .filter((a) => a.itemId === itemId)
    .reduce((s, a) => s + a.qty, 0);
}

export function remainingQtyForItem(itemId: string): number {
  const item = PRINT_ITEMS.find((i) => i.id === itemId);
  if (!item) return 0;
  return Math.max(0, item.qtyPo - allocatedQtyForItem(itemId));
}

/** Ringkasan baris tabel Kepala Produksi (per item Cetak). */
export type ProductionAllocRow = DemoPrintItem & {
  qtyAllocated: number;
  qtyRemaining: number;
  operatorSummary: string;
};

export function listProductionAllocRows(): ProductionAllocRow[] {
  return PRINT_ITEMS.map((item) => {
    const allocs = allocations.filter((a) => a.itemId === item.id);
    const qtyAllocated = allocs.reduce((s, a) => s + a.qty, 0);
    const names = [...new Set(allocs.map((a) => a.operatorName))];
    return {
      ...item,
      qtyAllocated,
      qtyRemaining: Math.max(0, item.qtyPo - qtyAllocated),
      operatorSummary: names.length ? names.join(", ") : "—",
    };
  });
}

export function allocatedQty(row: ProductionAllocRow): number {
  return row.qtyAllocated;
}

export function remainingQty(row: ProductionAllocRow): number {
  return row.qtyRemaining;
}

export function operatorSummary(row: ProductionAllocRow): string {
  return row.operatorSummary;
}

export type SaveAllocInput = {
  itemId: string;
  operatorId: string;
  qty: number;
  screen: DemoScreenOption;
  workDate: string;
};

/**
 * Tambah alokasi baru (tidak menimpa alokasi sebelumnya).
 * Setiap simpan = record DEMO terpisah untuk Jadwal Produksi.
 */
export function saveDemoAllocation(
  input: SaveAllocInput,
): { ok: true; allocation: DemoAllocation } | { ok: false; error: string } {
  const item = PRINT_ITEMS.find((i) => i.id === input.itemId);
  if (!item) return { ok: false, error: "Item line tidak ditemukan (DEMO)." };
  if (item.process !== "Cetak") {
    return { ok: false, error: "Hanya item proses Cetak yang dapat dialokasikan." };
  }
  if (!DEMO_SCREEN_OPTIONS.includes(input.screen)) {
    return { ok: false, error: "Pilihan screen tidak valid." };
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.workDate)) {
    return { ok: false, error: "Tanggal pengerjaan tidak valid." };
  }
  if (!Number.isInteger(input.qty) || input.qty <= 0) {
    return { ok: false, error: "Qty harus bilangan bulat positif." };
  }
  const op = DEMO_OPERATORS.find((o) => o.id === input.operatorId);
  if (!op) return { ok: false, error: "Pilih operator terlebih dahulu." };

  const sisa = remainingQtyForItem(item.id);
  if (input.qty > sisa) {
    return {
      ok: false,
      error: `Qty melebihi Sisa Qty (${sisa.toLocaleString("id-ID")}).`,
    };
  }

  const allocation: DemoAllocation = {
    id: `alloc-demo-${allocSeq++}`,
    poNo: item.poNo,
    customer: item.customer,
    itemId: item.id,
    itemName: item.itemName,
    operatorId: op.id,
    operatorName: op.name,
    qty: input.qty,
    screen: input.screen,
    workDate: input.workDate,
  };
  allocations = [...allocations, allocation];
  notify();
  return { ok: true, allocation };
}
