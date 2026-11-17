import type { PoRow } from "./types";

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

function datePlus(days: number): string {
  const d = new Date(2026, 10, 18);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
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

/** Immutable seed — cloned by DemoPoSource. Labeled DEMO only. */
export function createDemoPoSeed(): PoRow[] {
  const qtys = [1000, 3000, 5000, 7500, 10000, 20000, 4000, 6000];
  return Array.from({ length: 47 }, (_, i) => {
    const st = statusForIndex(i);
    const qty = qtys[i % qtys.length];
    const pct =
      st === "Terkirim" || st === "Siap Kirim"
        ? 100
        : st === "QC"
          ? 93
          : st === "Produksi"
            ? [20, 45, 60, 75][i % 4]
            : 0;
    const printed = Math.round((qty * pct) / 100);
    return {
      id: `PO-${1101 + i}`,
      customer: customers[i % customers.length],
      product: products[i % products.length],
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
      owner: ["Fahdo", "Raka", "Tika"][i % 3],
      sample: i % 5 === 0,
      activity: ["PO diterima dari Accurate (DEMO)", "Menunggu validasi produksi (DEMO)"],
    };
  });
}
