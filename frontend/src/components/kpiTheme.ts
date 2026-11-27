/**
 * Sistem visual KPI terpusat — warna per judul, dipakai seluruh halaman V4.
 * Tidak merepresentasikan aturan bisnis baru; hanya identitas tampilan.
 */

export type KpiVisualTheme = {
  /** Kunci stabil untuk data-attribute / debugging. */
  key: string;
  from: string;
  to: string;
  text: string;
  accent: string;
};

/** Mapping resmi Owner (teks disesuaikan bila perlu agar WCAG AA). */
export const KPI_THEME_BY_LABEL: Record<string, KpiVisualTheme> = {
  "Total PO": {
    key: "total-po",
    from: "#E2F0F6",
    to: "#C6E4EF",
    text: "#185C76",
    accent: "#247FA6",
  },
  "PO Baru": {
    key: "po-baru",
    from: "#E8F5EC",
    to: "#C8EAD4",
    text: "#206646",
    accent: "#2E9665",
  },
  "PO di Inbox": {
    key: "po-di-inbox",
    from: "#E8F5EC",
    to: "#C8EAD4",
    text: "#206646",
    accent: "#2E9665",
  },
  "Baris Item": {
    key: "baris-item",
    from: "#E9E5FA",
    to: "#D5CDF4",
    text: "#57468F",
    accent: "#8068C1",
  },
  "Belum Terjadwal": {
    key: "belum-terjadwal",
    from: "#FFF1D9",
    to: "#F9DCA6",
    // #8F5714 ≈ 4.47:1 vs ujung gelap → sedikit digelapkan untuk AA 4.5:1
    text: "#854F10",
    accent: "#BE7A20",
  },
  "Total Qty": {
    key: "total-qty",
    from: "#E9E5FA",
    to: "#D5CDF4",
    text: "#57468F",
    accent: "#8068C1",
  },
  "Sudah Dicetak": {
    key: "sudah-dicetak",
    from: "#DDF3ED",
    to: "#BCE6D9",
    text: "#176A5A",
    accent: "#278E79",
  },
  Urgent: {
    key: "urgent",
    from: "#FDE6E7",
    to: "#F4C4C9",
    text: "#A02F40",
    accent: "#C84B5C",
  },
  "Total Buffer Dipesan": {
    key: "buffer-ordered",
    from: "#E2F0F6",
    to: "#C6E4EF",
    text: "#185C76",
    accent: "#247FA6",
  },
  "Sedang Dikerjakan (Mesin)": {
    key: "buffer-in-machine",
    from: "#FFF1D9",
    to: "#F9DCA6",
    text: "#854F10",
    accent: "#BE7A20",
  },
  "Ready di Rak Gudang": {
    key: "buffer-ready-rack",
    from: "#DDF3ED",
    to: "#BCE6D9",
    text: "#176A5A",
    accent: "#278E79",
  },
  "Sudah Diambil / Terkirim": {
    key: "buffer-taken",
    from: "#E8F5EC",
    to: "#C8EAD4",
    text: "#206646",
    accent: "#2E9665",
  },
  "Jumlah SJ": {
    key: "jumlah-sj",
    from: "#E2F0F6",
    to: "#C6E4EF",
    text: "#185C76",
    accent: "#247FA6",
  },
  "Jumlah Tujuan": {
    key: "jumlah-tujuan",
    from: "#FFF1D9",
    to: "#F9DCA6",
    text: "#854F10",
    accent: "#BE7A20",
  },
  "Stok Tersedia": {
    key: "paint-available",
    from: "#E2F0F6",
    to: "#C6E4EF",
    text: "#185C76",
    accent: "#247FA6",
  },
  "Penggunaan Bersih": {
    key: "paint-net-usage",
    from: "#FFF1D9",
    to: "#F9DCA6",
    text: "#854F10",
    accent: "#BE7A20",
  },
  Pengembalian: {
    key: "paint-return",
    from: "#E8F5EC",
    to: "#C8EAD4",
    text: "#206646",
    accent: "#2E9665",
  },
  Waste: {
    key: "paint-waste",
    from: "#FDE6E7",
    to: "#F4C4C9",
    text: "#A02F40",
    accent: "#C84B5C",
  },
};

/** Alias judul existing agar identitas sama tanpa mengubah label data. */
const LABEL_ALIASES: Record<string, string> = {
  "Total Qty SO": "Total Qty",
};

/** Palet harmonis untuk KPI lain yang sudah ada (bukan inventaris bisnis baru). */
const FALLBACK_THEMES: KpiVisualTheme[] = [
  {
    key: "qty-terjadwal",
    from: "#E3EEF8",
    to: "#C5DCF0",
    text: "#1F5678",
    accent: "#2F7AA6",
  },
  {
    key: "dalam-produksi",
    from: "#E7F2E9",
    to: "#C9E3CF",
    text: "#2A5F3A",
    accent: "#3C8A52",
  },
  {
    key: "siap-kirim",
    from: "#E8F6F1",
    to: "#C7E8DC",
    text: "#1B6354",
    accent: "#2A8A74",
  },
  {
    key: "generic-a",
    from: "#EEF3F0",
    to: "#D5E3DB",
    text: "#1F4336",
    accent: "#3A6B57",
  },
  {
    key: "generic-b",
    from: "#F3F0EA",
    to: "#E2D8C8",
    text: "#5A4630",
    accent: "#8A6B42",
  },
];

const FALLBACK_BY_LABEL: Record<string, KpiVisualTheme> = {
  "Qty Terjadwal": FALLBACK_THEMES[0],
  "Dalam Produksi": FALLBACK_THEMES[1],
  "Siap Kirim": FALLBACK_THEMES[2],
};

function hashLabel(label: string): number {
  let h = 0;
  for (let i = 0; i < label.length; i++) {
    h = (h * 31 + label.charCodeAt(i)) >>> 0;
  }
  return h;
}

/**
 * Resolusi tema visual berdasarkan judul KPI.
 * Judul sama → tema sama lintas halaman.
 */
export function resolveKpiTheme(label: string): KpiVisualTheme {
  const canonical = LABEL_ALIASES[label] ?? label;
  if (KPI_THEME_BY_LABEL[canonical]) {
    return KPI_THEME_BY_LABEL[canonical];
  }
  if (FALLBACK_BY_LABEL[label]) {
    return FALLBACK_BY_LABEL[label];
  }
  return FALLBACK_THEMES[hashLabel(label) % FALLBACK_THEMES.length];
}

