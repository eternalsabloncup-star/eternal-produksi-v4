import {
  buildDemoLineId,
  buildDemoSourcePoId,
} from "../domain/poIdentity";
import { mapProcessFromSource } from "../domain/poProcessMapping";
import type { PoLineItem, PoRow } from "./types";

const customers = [
  "Es Teh Nusantara",
  "Kopi Cerita",
  "Fresco Coffee",
  "Titik Temu",
  "Kopi Kota",
  "Teh Manis Jaya",
  "Kedai Langit",
  "Susu Segar 88",
  "Kopi Bumi",
  "Es Teh Rakyat",
  "Minum Dulu",
  "Roastery 27",
];

const products = [
  "Cup PET 16oz + sablon",
  "Cup PET 12oz + sablon",
  "Cup PP 16oz + sablon",
  "Cup PET 22oz + sablon",
  "Cup PET 14oz + sablon",
  "Cup PET 12oz SA + sablon",
];

const paymentTerms = ["COD", "CBD", "Net 7", "Net 14", "Transfer"];
const expeditions = ["JNE", "SiCepat", "Anteraja", "Pick Up", "Internal"];
const zones = ["Dalam Kota", "Jawa", "Luar Jawa", "Bali", "Sumatera"];
const sellers = ["Fahdo", "Raka", "Tika", "Dewi"];
/**
 * Nilai proses sumber DEMO — meniru field proses Accurate yang sudah
 * terverifikasi di allowlist. Bukan ditebak dari nama barang.
 * "Polos" / "Sablon N warna" sengaja tidak dipakai sebagai sumber proses.
 */
/** Sumber proses meniru charField1 Accurate — bukan nama barang. */
const processSources = ["Cetak", "Cetak", "Cetak", "Tidak Cetak"] as const;
const colors = ["Hitam", "Putih", "Merah", "Hijau", "Custom"];
const unitsPcs = "PCS";
const unitsPack = "PACK";

export const DEMO_BRANCHES = ["Yogyakarta", "Semarang", "Surabaya"] as const;
export const DEMO_MACHINES = [
  "Kurva Otomatis",
  "Semi Auto 01",
  "Semi Auto 02",
  "Semi Auto 03",
  "Semi Auto 04",
] as const;

export const DEMO_STATUSES = [
  "PO Baru",
  "Belum Terjadwal",
  "Dijadwalkan",
  "Persiapan",
  "Menunggu Sampel",
  "Produksi",
  "QC",
  "Siap Kirim",
  "Terkirim",
] as const;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Kalender lokal → YYYY-MM-DD tanpa pergeseran timezone ISO. */
function datePlus(days: number): string {
  const d = new Date(2026, 10, 18);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function statusForIndex(i: number): string {
  if (i < 7) return "PO Baru";
  if (i < 14) return "Belum Terjadwal";
  if (i < 22) return "Dijadwalkan";
  if (i < 27) return "Menunggu Sampel";
  if (i < 37) return "Produksi";
  if (i < 41) return "QC";
  if (i < 45) return "Siap Kirim";
  return "Terkirim";
}

function buildLineItem(
  poNo: string,
  code: string,
  name: string,
  qty: number,
  unit: string,
  processRaw: string,
  color: string,
): PoLineItem {
  const mapped = mapProcessFromSource(processRaw);
  return {
    lineId: buildDemoLineId(poNo, code),
    code,
    name,
    qty,
    unit,
    processRaw: mapped.processRaw,
    processKind: mapped.processKind,
    process: mapped.processKind ?? mapped.processRaw,
    color,
  };
}

function buildItems(poIndex: number, poNo: string, primaryProduct: string): PoLineItem[] {
  const count = (poIndex % 5) + 1;
  const items: PoLineItem[] = [];
  for (let j = 0; j < count; j++) {
    const usePack = j === count - 1 && count > 1 && poIndex % 3 === 0;
    const unit = usePack ? unitsPack : unitsPcs;
    const qty = usePack
      ? [10, 20, 50][poIndex % 3]
      : [500, 1000, 2000, 3000, 5000][(poIndex + j) % 5];
    const code = `BRG-${2000 + poIndex * 10 + j}`;
    const processRaw = processSources[(poIndex + j) % processSources.length];
    items.push(
      buildLineItem(
        poNo,
        code,
        j === 0 ? primaryProduct : products[(poIndex + j) % products.length],
        qty,
        unit,
        processRaw,
        colors[(poIndex + j) % colors.length],
      ),
    );
  }
  return items;
}

/** Teks total qty dari “dokumen” DEMO — tidak menggabungkan PCS+PACK jadi satu satuan. */
function totalQtyDisplayFromItems(items: PoLineItem[]): string {
  const byUnit = new Map<string, number>();
  for (const it of items) {
    byUnit.set(it.unit, (byUnit.get(it.unit) || 0) + it.qty);
  }
  return [...byUnit.entries()]
    .map(([unit, qty]) => `${qty.toLocaleString("id-ID")} ${unit}`)
    .join(" + ");
}

/** Qty numerik untuk metrik DEMO (hanya baris PCS; PACK tidak dikonversi). */
function pcsQtyForMetrics(items: PoLineItem[]): number {
  return items
    .filter((it) => it.unit === unitsPcs)
    .reduce((s, it) => s + it.qty, 0);
}

/** Immutable seed — cloned by DemoPoSource. Labeled DEMO only. */
export function createDemoPoSeed(): PoRow[] {
  return Array.from({ length: 47 }, (_, i) => {
    const st = statusForIndex(i);
    const product = products[i % products.length];
    const poNo = `PO-${1101 + i}`;
    const items = buildItems(i, poNo, product);
    const qty = pcsQtyForMetrics(items) || items.reduce((s, it) => s + it.qty, 0);
    const pct =
      st === "Terkirim" || st === "Siap Kirim"
        ? 100
        : st === "QC"
          ? 93
          : st === "Produksi"
            ? [20, 45, 60, 75][i % 4]
            : 0;
    const printed = Math.round((qty * pct) / 100);
    const customer = customers[i % customers.length];
    return {
      id: poNo,
      poNo,
      sourcePoId: buildDemoSourcePoId(poNo),
      origin: "demo" as const,
      customer,
      product,
      qty,
      printed,
      qc:
        st === "QC"
          ? Math.round(qty * 0.75)
          : pct === 100
            ? qty
            : st === "Produksi"
              ? Math.round(printed * 0.85)
              : 0,
      branch: DEMO_BRANCHES[i % 3],
      status: st,
      machine: i < 14 ? "" : DEMO_MACHINES[i % 5],
      deadline: datePlus((i % 8) - 2),
      urgent: i % 11 === 0,
      owner: sellers[i % sellers.length],
      sample: i % 5 === 0,
      activity: [
        "PO diterima dari Accurate (DEMO)",
        "Menunggu validasi produksi (DEMO)",
      ],
      poDate: datePlus(-((i % 10) + 1)),
      paymentTerms: paymentTerms[i % paymentTerms.length],
      etaShip: datePlus((i % 8) + 3),
      seller: sellers[i % sellers.length],
      expedition: expeditions[i % expeditions.length],
      shipZone: zones[i % zones.length],
      recipient: `Penerima ${customer}`,
      address: `Jl. Demo No. ${10 + (i % 40)}, ${DEMO_BRANCHES[i % 3]}`,
      userName: ["admin.demo", "cs.demo", "sales.demo"][i % 3],
      bufferLabel: i % 4 === 0 ? "Buffer" : "Non-Buffer",
      notes: i % 6 === 0 ? "Catatan DEMO: prioritas outlet baru" : "",
      totalQtyDisplay: totalQtyDisplayFromItems(items),
      itemCount: items.length,
      items,
    };
  });
}
