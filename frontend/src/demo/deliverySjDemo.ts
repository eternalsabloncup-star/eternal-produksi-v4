/**
 * Fixture Jadwal Pengiriman / Surat Jalan (DEMO frontend).
 * Satu sumber untuk tabel utama + popup Rincian Pengiriman.
 * Bukan integrasi Accurate; bukan mutasi stok/pengiriman.
 */

export type SjLineItem = {
  code: string;
  name: string;
  qty: number;
  unit: string;
  outlet: string;
};

export type DeliverySjRow = {
  id: string;
  sjNo: string;
  poNo: string;
  customer: string;
  address: string;
  seller: string;
  expedition: string;
  createdBy: string;
  shipZone: string;
  /** Identitas tujuan pengiriman eksplisit (untuk KPI Jumlah Tujuan). */
  destinationId: string;
  printedAt: string;
  shipDate: string;
  notes: string;
  items: SjLineItem[];
};

export type ShipDateFilterValue =
  | { kind: "all" }
  | { kind: "single"; date: string }
  | { kind: "range"; start: string; end: string };

const ROWS: DeliverySjRow[] = [
  {
    id: "sj-demo-01",
    sjNo: "DEMO-SJ-2401",
    poNo: "PO-1101",
    customer: "Demo Mitra Produksi (SIM)",
    address: "Jl. Demo Industri No. 12, Yogyakarta",
    seller: "Fahdo",
    expedition: "Internal",
    createdBy: "admin.demo",
    shipZone: "Dalam Kota",
    destinationId: "dest-yogya-mitra",
    printedAt: "2026-03-18",
    shipDate: "2026-03-20",
    notes: "Kirim pagi (DEMO)",
    items: [
      {
        code: "BRG-2000",
        name: "Cup PET 16oz Demo Motif A",
        qty: 2000,
        unit: "PCS",
        outlet: "Gudang Demo · Yogyakarta",
      },
      {
        code: "BRG-2001",
        name: "Cup PET 16oz Demo Motif B",
        qty: 500,
        unit: "PCS",
        outlet: "Gudang Demo · Yogyakarta",
      },
    ],
  },
  {
    id: "sj-demo-02",
    sjNo: "DEMO-SJ-2402",
    poNo: "PO-1101",
    customer: "Demo Mitra Produksi (SIM)",
    address: "Jl. Demo Industri No. 12, Yogyakarta",
    seller: "Fahdo",
    expedition: "Internal",
    createdBy: "cs.demo",
    shipZone: "Dalam Kota",
    destinationId: "dest-yogya-mitra",
    printedAt: "2026-03-19",
    shipDate: "2026-03-20",
    notes: "SJ kedua PO yang sama (DEMO)",
    items: [
      {
        code: "BRG-2002",
        name: "Lid PET 16oz Demo",
        qty: 20,
        unit: "PACK",
        outlet: "Gudang Demo · Yogyakarta",
      },
    ],
  },
  {
    id: "sj-demo-03",
    sjNo: "DEMO-SJ-2403",
    poNo: "PO-1105",
    customer: "Demo Cahaya Packindo (SIM)",
    address: "Jl. Simulasi Pack No. 8, Semarang",
    seller: "Raka",
    expedition: "SiCepat",
    createdBy: "sales.demo",
    shipZone: "Jawa",
    destinationId: "dest-smg-cahaya",
    printedAt: "2026-03-19",
    shipDate: "2026-03-20",
    notes: "",
    items: [
      {
        code: "BRG-2040",
        name: "Cup PP 14oz Demo Logo",
        qty: 3600,
        unit: "PCS",
        outlet: "Gudang Demo · Semarang",
      },
      {
        code: "BRG-2041",
        name: "Cup PP 14oz Demo Soft",
        qty: 1200,
        unit: "PCS",
        outlet: "Gudang Demo · Semarang",
      },
      {
        code: "BRG-2042",
        name: "Karton Pack Demo",
        qty: 40,
        unit: "PACK",
        outlet: "Gudang Demo · Semarang",
      },
    ],
  },
  {
    id: "sj-demo-04",
    sjNo: "DEMO-SJ-2404",
    poNo: "PO-1109",
    customer: "Demo Prima Label Test (SIM)",
    address: "Komplek Demo Label Blok C, Surabaya",
    seller: "Tika",
    expedition: "JNE",
    createdBy: "admin.demo",
    shipZone: "Jawa",
    destinationId: "dest-sby-prima",
    printedAt: "2026-03-21",
    shipDate: "2026-03-22",
    notes: "Prioritas outlet (DEMO)",
    items: [
      {
        code: "BRG-2080",
        name: "Cup PET 12oz Demo Run",
        qty: 1800,
        unit: "PCS",
        outlet: "Gudang Demo · Surabaya",
      },
    ],
  },
  {
    id: "sj-demo-05",
    sjNo: "DEMO-SJ-2405",
    poNo: "PO-1113",
    customer: "Demo Angkasa Print Lab (SIM)",
    address: "Jl. Lab Cetak Demo 3, Yogyakarta",
    seller: "Dewi",
    expedition: "Pick Up",
    createdBy: "cs.demo",
    shipZone: "Dalam Kota",
    destinationId: "dest-yogya-angkasa",
    printedAt: "2026-03-21",
    shipDate: "2026-03-22",
    notes: "",
    items: [
      {
        code: "BRG-2120",
        name: "Cup PET 9oz Demo Pack",
        qty: 4200,
        unit: "PCS",
        outlet: "Gudang Demo · Yogyakarta",
      },
      {
        code: "BRG-2121",
        name: "Sleeve Demo Soft",
        qty: 15,
        unit: "PACK",
        outlet: "Gudang Demo · Yogyakarta",
      },
    ],
  },
  {
    id: "sj-demo-06",
    sjNo: "DEMO-SJ-2406",
    poNo: "PO-1117",
    customer: "Demo Biru Laut Sample (SIM)",
    address: "Jl. Simulasi Laut No. 21, Bali",
    seller: "Fahdo",
    expedition: "Anteraja",
    createdBy: "sales.demo",
    shipZone: "Bali",
    destinationId: "dest-bali-biru",
    printedAt: "2026-03-24",
    shipDate: "2026-03-25",
    notes: "Cek packing (DEMO)",
    items: [
      {
        code: "BRG-2160",
        name: "Cup PET 22oz Demo Wave",
        qty: 2500,
        unit: "PCS",
        outlet: "Gudang Demo · Surabaya",
      },
    ],
  },
  {
    id: "sj-demo-07",
    sjNo: "DEMO-SJ-2407",
    poNo: "PO-1121",
    customer: "Demo Nusantara Cup Lab (SIM)",
    address: "Jl. Demo Nusantara 45, Semarang",
    seller: "Raka",
    expedition: "Internal",
    createdBy: "admin.demo",
    shipZone: "Jawa",
    destinationId: "dest-smg-cahaya",
    printedAt: "2026-03-24",
    shipDate: "2026-03-25",
    notes: "Tujuan sama dengan SJ lain (DEMO)",
    items: [
      {
        code: "BRG-2200",
        name: "Cup PET 14oz Demo Lab",
        qty: 1000,
        unit: "PCS",
        outlet: "Gudang Demo · Semarang",
      },
      {
        code: "BRG-2201",
        name: "Cup PET 14oz Demo Lab B",
        qty: 800,
        unit: "PCS",
        outlet: "Gudang Demo · Semarang",
      },
    ],
  },
  {
    id: "sj-demo-08",
    sjNo: "DEMO-SJ-2408",
    poNo: "PO-1125",
    customer: "Demo Kopi Cerita Test (SIM)",
    address: "Jl. Cerita Demo 9, Sumatera",
    seller: "Tika",
    expedition: "JNE",
    createdBy: "cs.demo",
    shipZone: "Sumatera",
    destinationId: "dest-sumatera-cerita",
    printedAt: "2026-03-27",
    shipDate: "2026-03-28",
    notes: "",
    items: [
      {
        code: "BRG-2240",
        name: "Cup PET 12oz SA Demo",
        qty: 3000,
        unit: "PCS",
        outlet: "Gudang Demo · Yogyakarta",
      },
    ],
  },
];

/** Tanggal tanpa pengiriman — untuk uji empty state popup. */
export const DEMO_EMPTY_SHIP_DATE = "2026-04-01";

export function listDeliverySjRows(): DeliverySjRow[] {
  return ROWS.map((r) => ({
    ...r,
    items: r.items.map((it) => ({ ...it })),
  }));
}

export function matchesShipDate(
  shipDate: string,
  filter: ShipDateFilterValue,
): boolean {
  if (filter.kind === "all") return true;
  if (filter.kind === "single") return shipDate === filter.date;
  return shipDate >= filter.start && shipDate <= filter.end;
}

export function filterDeliverySjRows(
  filter: ShipDateFilterValue,
  q?: string,
): DeliverySjRow[] {
  let rows = listDeliverySjRows().filter((r) =>
    matchesShipDate(r.shipDate, filter),
  );
  const qq = q?.trim().toLowerCase();
  if (qq) {
    rows = rows.filter(
      (r) =>
        r.sjNo.toLowerCase().includes(qq) ||
        r.poNo.toLowerCase().includes(qq) ||
        r.customer.toLowerCase().includes(qq) ||
        r.address.toLowerCase().includes(qq) ||
        r.seller.toLowerCase().includes(qq) ||
        r.expedition.toLowerCase().includes(qq) ||
        r.shipZone.toLowerCase().includes(qq) ||
        r.createdBy.toLowerCase().includes(qq) ||
        r.notes.toLowerCase().includes(qq),
    );
  }
  return rows;
}

export function itemCount(row: DeliverySjRow): number {
  return row.items.length;
}

/** Total kuantitas per satuan — tidak mencampur PCS+PACK. */
export function qtyByUnit(row: DeliverySjRow): Record<string, number> {
  const map: Record<string, number> = {};
  for (const it of row.items) {
    map[it.unit] = (map[it.unit] || 0) + it.qty;
  }
  return map;
}

export function formatQtyByUnit(map: Record<string, number>): string {
  const parts = Object.entries(map).map(
    ([unit, qty]) => `${qty.toLocaleString("id-ID")} ${unit}`,
  );
  return parts.length ? parts.join(" + ") : "—";
}

export function rowTotalQtyDisplay(row: DeliverySjRow): string {
  return formatQtyByUnit(qtyByUnit(row));
}

export type DeliverySjMetrics = {
  sjCount: number;
  qtyByUnit: Record<string, number>;
  destinationCount: number;
};

/** KPI dari hasil filter yang sama dengan tabel — bukan hardcode. */
export function getDeliverySjMetrics(rows: DeliverySjRow[]): DeliverySjMetrics {
  const destinations = new Set<string>();
  const qty: Record<string, number> = {};
  for (const r of rows) {
    destinations.add(r.destinationId);
    for (const it of r.items) {
      qty[it.unit] = (qty[it.unit] || 0) + it.qty;
    }
  }
  return {
    sjCount: rows.length,
    qtyByUnit: qty,
    destinationCount: destinations.size,
  };
}

export function formatShipDateFilterLabel(
  filter: ShipDateFilterValue,
  formatDate: (iso: string) => string,
): string {
  if (filter.kind === "all") return "Semua Tanggal";
  if (filter.kind === "single") return formatDate(filter.date);
  return `${formatDate(filter.start)} – ${formatDate(filter.end)}`;
}
