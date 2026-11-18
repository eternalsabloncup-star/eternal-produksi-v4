/**
 * Fixture Buffer Stock (DEMO frontend).
 * Nomor PO = No. PO Masuk dari seed Inbox (hanya Buffer).
 * Bukan sinkronisasi Accurate; bukan mutasi stok.
 */

import { createDemoPoSeed } from "./fixtures";
import type { PoRow } from "./types";

export type BufferStatus =
  | "ANTREAN MESIN"
  | "READY GUDANG"
  | "SELESAI / DIAMBIL";

export type BufferStockRow = {
  id: string;
  poNo: string;
  outlet: string;
  itemCode: string;
  itemName: string;
  color: string;
  qtyOrdered: number;
  qtyPrinted: number;
  qtyRemaining: number;
  qtyReady: number;
  qtyTaken: number;
  status: BufferStatus;
  salesPic: string;
};

type QtySplit = {
  qtyPrinted: number;
  qtyReady: number;
  qtyTaken: number;
  status: BufferStatus;
};

/**
 * Split qty konsisten aritmetika DEMO:
 * remaining = ordered - printed
 * printed = ready + taken
 */
function splitQty(ordered: number, scenario: number): QtySplit {
  if (ordered <= 0) {
    return { qtyPrinted: 0, qtyReady: 0, qtyTaken: 0, status: "ANTREAN MESIN" };
  }
  const mode = scenario % 5;
  if (mode === 0) {
    // Belum selesai cetak — antrean mesin
    const printed = Math.floor(ordered * 0.35);
    const taken = Math.floor(printed * 0.2);
    const ready = printed - taken;
    return {
      qtyPrinted: printed,
      qtyReady: ready,
      qtyTaken: taken,
      status: "ANTREAN MESIN",
    };
  }
  if (mode === 1) {
    // Selesai cetak, masih di rak
    return {
      qtyPrinted: ordered,
      qtyReady: ordered,
      qtyTaken: 0,
      status: "READY GUDANG",
    };
  }
  if (mode === 2) {
    // Selesai cetak, sebagian diambil
    const taken = Math.floor(ordered * 0.4);
    return {
      qtyPrinted: ordered,
      qtyReady: ordered - taken,
      qtyTaken: taken,
      status: "READY GUDANG",
    };
  }
  if (mode === 3) {
    // Sudah diambil / terkirim
    return {
      qtyPrinted: ordered,
      qtyReady: 0,
      qtyTaken: ordered,
      status: "SELESAI / DIAMBIL",
    };
  }
  // Antrean mesin — belum ada cetak
  return {
    qtyPrinted: 0,
    qtyReady: 0,
    qtyTaken: 0,
    status: "ANTREAN MESIN",
  };
}

function outletForPo(po: PoRow): string {
  return `Gudang Demo · ${po.branch}`;
}

function buildRowsFromInbox(): BufferStockRow[] {
  const seed = createDemoPoSeed().filter((p) => p.bufferLabel === "Buffer");
  const rows: BufferStockRow[] = [];
  let scenario = 0;
  for (const po of seed) {
    po.items.forEach((item, idx) => {
      const ordered = item.qty;
      const split = splitQty(ordered, scenario + idx);
      rows.push({
        id: `buf-${po.id}-${item.code}`,
        poNo: po.id,
        outlet: outletForPo(po),
        itemCode: item.code,
        itemName: item.name,
        color: item.color,
        qtyOrdered: ordered,
        qtyPrinted: split.qtyPrinted,
        qtyRemaining: ordered - split.qtyPrinted,
        qtyReady: split.qtyReady,
        qtyTaken: split.qtyTaken,
        status: split.status,
        salesPic: po.seller,
      });
      scenario += 1;
    });
  }
  return rows;
}

const ROWS: BufferStockRow[] = buildRowsFromInbox();

export function listBufferStockRows(): BufferStockRow[] {
  return ROWS.map((r) => ({ ...r }));
}

export type BufferStockMetrics = {
  totalOrdered: number;
  inMachine: number;
  readyWarehouse: number;
  takenShipped: number;
  poCount: number;
  itemCount: number;
};

/** KPI dari agregasi baris fixture — bukan hardcode card. */
export function getBufferStockMetrics(): BufferStockMetrics {
  let totalOrdered = 0;
  let inMachine = 0;
  let readyWarehouse = 0;
  let takenShipped = 0;
  const poSet = new Set<string>();
  for (const r of ROWS) {
    poSet.add(r.poNo);
    totalOrdered += r.qtyOrdered;
    inMachine += r.qtyRemaining;
    readyWarehouse += r.qtyReady;
    takenShipped += r.qtyTaken;
  }
  return {
    totalOrdered,
    inMachine,
    readyWarehouse,
    takenShipped,
    poCount: poSet.size,
    itemCount: ROWS.length,
  };
}
