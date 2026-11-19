import type { ColumnFilterKind } from "../components/DataTable/types";
import { getProductionFlowDemoRows } from "../demo/productionFlowDemo";
import { PRODUCTION_FLOW_LABELS } from "../components/ProductionFlowTrack";

/** Baris generik tahap — nilai dari fixture DEMO / sumber nyata. */
export type StageTableRow = {
  key: string;
  values: Record<string, string>;
};

export type StageColumnSpec = {
  id: string;
  label: string;
  filterType: ColumnFilterKind;
  defaultWidth: number;
  minWidth?: number;
  sticky?: "po" | "customer";
  /** Kolom aksi khusus UI (bukan field data biasa). */
  kind?: "action-open-pdf" | "action-approval-link" | "accurate-placeholder";
};

export type StageTableConfig = {
  stageId: string;
  label: string;
  columns: StageColumnSpec[];
  /** Prefs & data key — bisa berbeda dari stageId untuk workspace gabungan. */
  workspaceId: string;
  /** Sumber baris demo (bisa beberapa stageId). */
  sourceStageIds: string[];
};

const NEXT_STATUS: StageColumnSpec = {
  id: "nextStatus",
  label: "Status Selanjutnya",
  filterType: "text",
  defaultWidth: 140,
};

const STAGE_IDS = [
  "flow-order-masuk",
  "flow-design",
  "flow-approval-design",
  "flow-payment",
  "flow-approval-payment",
  "flow-penjadwalan",
  "flow-screener",
  "flow-persiapan",
  "flow-operator",
  "flow-konfirmasi-sample",
  "flow-cetak",
  "flow-packing",
  "flow-surat-jalan",
  "flow-loading",
  "flow-pengiriman",
] as const;

/** Nama tahap berikutnya (tampilan saja) — bukan aturan perpindahan. */
export function nextStageLabelFor(stageId: string): string {
  const i = STAGE_IDS.indexOf(stageId as (typeof STAGE_IDS)[number]);
  if (i < 0 || i >= STAGE_IDS.length - 1) return "—";
  return PRODUCTION_FLOW_LABELS[i + 1];
}

function withNext(cols: StageColumnSpec[]): StageColumnSpec[] {
  return [...cols, NEXT_STATUS];
}

/**
 * Konfigurasi kolom deterministik per tahap / workspace (Owner).
 * Hanya spesifikasi tampilan — bukan aturan status/Qty/workflow.
 */
export const PRODUCTION_FLOW_STAGE_TABLES: StageTableConfig[] = [
  {
    stageId: "flow-order-masuk",
    workspaceId: "flow-order-masuk",
    sourceStageIds: ["flow-order-masuk"],
    label: "Order Masuk",
    columns: withNext([
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "poDate", label: "Tanggal PO", filterType: "date", defaultWidth: 104 },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 160, minWidth: 110, sticky: "customer" },
      { id: "orderStatus", label: "Status Pesanan", filterType: "text", defaultWidth: 120 },
      { id: "itemCount", label: "Jumlah Item", filterType: "number", defaultWidth: 96 },
      { id: "totalQty", label: "Total Kuantitas", filterType: "text", defaultWidth: 140 },
      { id: "etaShip", label: "Estimasi Pengiriman", filterType: "date", defaultWidth: 128 },
      { id: "seller", label: "Penjual", filterType: "text", defaultWidth: 120 },
    ]),
  },
  {
    stageId: "flow-design",
    workspaceId: "flow-ws-design-approval",
    sourceStageIds: ["flow-design", "flow-approval-design"],
    label: "Design & Approval Design",
    columns: [
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "itemName", label: "Nama Barang", filterType: "text", defaultWidth: 160 },
      { id: "printColor", label: "Warna Cetak", filterType: "text", defaultWidth: 120 },
      { id: "designer", label: "Designer", filterType: "text", defaultWidth: 120 },
      { id: "designStatus", label: "Status Desain", filterType: "text", defaultWidth: 120 },
      { id: "approvalStatus", label: "Status Approval", filterType: "text", defaultWidth: 130 },
      { id: "target", label: "Target", filterType: "text", defaultWidth: 110 },
      NEXT_STATUS,
      { id: "openDesign", label: "Buka Design", filterType: "text", defaultWidth: 120, kind: "action-open-pdf" },
      { id: "approvalLink", label: "Link Approval", filterType: "text", defaultWidth: 150, kind: "action-approval-link" },
    ],
  },
  {
    stageId: "flow-approval-design",
    workspaceId: "flow-ws-design-approval",
    sourceStageIds: ["flow-design", "flow-approval-design"],
    label: "Design & Approval Design",
    columns: [], // diisi lewat alias
  },
  {
    stageId: "flow-payment",
    workspaceId: "flow-ws-payment-approval",
    sourceStageIds: ["flow-payment", "flow-approval-payment"],
    label: "Payment & Approval Payment",
    columns: [
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "paymentTerms", label: "Syarat Pembayaran", filterType: "text", defaultWidth: 140 },
      { id: "paymentStatus", label: "Status Pembayaran", filterType: "text", defaultWidth: 140 },
      { id: "paymentDate", label: "Tanggal Pembayaran", filterType: "date", defaultWidth: 128 },
      { id: "verifyStatus", label: "Status Verifikasi", filterType: "text", defaultWidth: 130 },
      { id: "submittedAt", label: "Tanggal Pengajuan", filterType: "date", defaultWidth: 128 },
      { id: "confirmedAt", label: "Tanggal Konfirmasi", filterType: "date", defaultWidth: 128 },
      NEXT_STATUS,
      { id: "openPaymentFile", label: "Buka File", filterType: "text", defaultWidth: 110, kind: "action-open-pdf" },
      {
        id: "accurateReceipt",
        label: "Penerimaan Pembayaran Accurate",
        filterType: "text",
        defaultWidth: 200,
        kind: "accurate-placeholder",
      },
    ],
  },
  {
    stageId: "flow-approval-payment",
    workspaceId: "flow-ws-payment-approval",
    sourceStageIds: ["flow-payment", "flow-approval-payment"],
    label: "Payment & Approval Payment",
    columns: [],
  },
  {
    stageId: "flow-penjadwalan",
    workspaceId: "flow-penjadwalan",
    sourceStageIds: ["flow-penjadwalan"],
    label: "Penjadwalan",
    columns: withNext([
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "itemName", label: "Nama Barang", filterType: "text", defaultWidth: 160 },
      { id: "qtyPo", label: "Qty PO", filterType: "number", defaultWidth: 88 },
      { id: "qtyScheduled", label: "Qty Terjadwal", filterType: "number", defaultWidth: 110 },
      { id: "machine", label: "Mesin", filterType: "text", defaultWidth: 110 },
      { id: "scheduleDate", label: "Tanggal Jadwal", filterType: "date", defaultWidth: 120 },
    ]),
  },
  {
    stageId: "flow-screener",
    workspaceId: "flow-screener",
    sourceStageIds: ["flow-screener"],
    label: "Screener",
    columns: withNext([
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "itemName", label: "Nama Barang", filterType: "text", defaultWidth: 160 },
      { id: "printColor", label: "Warna Cetak", filterType: "text", defaultWidth: 120 },
      { id: "screener", label: "Screener", filterType: "text", defaultWidth: 120 },
      { id: "screenStatus", label: "Status Screen", filterType: "text", defaultWidth: 120 },
    ]),
  },
  {
    stageId: "flow-persiapan",
    workspaceId: "flow-persiapan",
    sourceStageIds: ["flow-persiapan"],
    label: "Persiapan",
    columns: withNext([
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "itemName", label: "Nama Barang", filterType: "text", defaultWidth: 160 },
      { id: "qtyNeeded", label: "Qty Kebutuhan", filterType: "number", defaultWidth: 110 },
      { id: "qtyPrepared", label: "Qty Disiapkan", filterType: "number", defaultWidth: 110 },
      { id: "prepStatus", label: "Status Persiapan", filterType: "text", defaultWidth: 130 },
    ]),
  },
  {
    stageId: "flow-operator",
    workspaceId: "flow-operator",
    sourceStageIds: ["flow-operator"],
    label: "Operator",
    columns: withNext([
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "itemName", label: "Nama Barang", filterType: "text", defaultWidth: 160 },
      { id: "machine", label: "Mesin", filterType: "text", defaultWidth: 110 },
      { id: "operator", label: "Operator", filterType: "text", defaultWidth: 120 },
      { id: "qtyTarget", label: "Qty Target", filterType: "number", defaultWidth: 96 },
      { id: "qtyPrinted", label: "Qty Cetak", filterType: "number", defaultWidth: 96 },
    ]),
  },
  {
    stageId: "flow-konfirmasi-sample",
    workspaceId: "flow-konfirmasi-sample",
    sourceStageIds: ["flow-konfirmasi-sample"],
    label: "Konfirmasi Sample",
    columns: withNext([
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "itemName", label: "Nama Barang", filterType: "text", defaultWidth: 160 },
      { id: "sampleDate", label: "Tanggal Sampel", filterType: "date", defaultWidth: 120 },
      { id: "confirmStatus", label: "Status Konfirmasi", filterType: "text", defaultWidth: 140 },
    ]),
  },
  {
    stageId: "flow-cetak",
    workspaceId: "flow-cetak",
    sourceStageIds: ["flow-cetak"],
    label: "Cetak",
    columns: withNext([
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "itemName", label: "Nama Barang", filterType: "text", defaultWidth: 160 },
      { id: "machine", label: "Mesin", filterType: "text", defaultWidth: 110 },
      { id: "qtyPo", label: "Qty PO", filterType: "number", defaultWidth: 88 },
      { id: "qtyPrinted", label: "Qty Dicetak", filterType: "number", defaultWidth: 100 },
      { id: "qtyRemaining", label: "Sisa Qty", filterType: "number", defaultWidth: 88 },
    ]),
  },
  {
    stageId: "flow-packing",
    workspaceId: "flow-packing",
    sourceStageIds: ["flow-packing"],
    label: "Packing",
    columns: withNext([
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108, minWidth: 88, sticky: "po" },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "itemName", label: "Nama Barang", filterType: "text", defaultWidth: 160 },
      { id: "qtyPrinted", label: "Qty Dicetak", filterType: "number", defaultWidth: 100 },
      { id: "qtyPacked", label: "Qty Packing", filterType: "number", defaultWidth: 100 },
    ]),
  },
  {
    stageId: "flow-surat-jalan",
    workspaceId: "flow-surat-jalan",
    sourceStageIds: ["flow-surat-jalan"],
    label: "Surat Jalan",
    columns: withNext([
      { id: "sjNo", label: "No. SJ", filterType: "text", defaultWidth: 110, minWidth: 88, sticky: "po" },
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108 },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "qtyShip", label: "Qty Pengiriman", filterType: "number", defaultWidth: 120 },
      { id: "sjDate", label: "Tanggal SJ", filterType: "date", defaultWidth: 110 },
    ]),
  },
  {
    stageId: "flow-loading",
    workspaceId: "flow-loading",
    sourceStageIds: ["flow-loading"],
    label: "Loading",
    columns: withNext([
      { id: "sjNo", label: "No. SJ", filterType: "text", defaultWidth: 110, minWidth: 88, sticky: "po" },
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108 },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "qtyLoading", label: "Qty Loading", filterType: "number", defaultWidth: 110 },
      { id: "loadingStatus", label: "Status Loading", filterType: "text", defaultWidth: 120 },
    ]),
  },
  {
    stageId: "flow-pengiriman",
    workspaceId: "flow-pengiriman",
    sourceStageIds: ["flow-pengiriman"],
    label: "Pengiriman",
    columns: withNext([
      { id: "sjNo", label: "No. SJ", filterType: "text", defaultWidth: 110, minWidth: 88, sticky: "po" },
      { id: "poNo", label: "No. PO", filterType: "text", defaultWidth: 108 },
      { id: "customer", label: "Pelanggan", filterType: "text", defaultWidth: 150, sticky: "customer" },
      { id: "driver", label: "Driver", filterType: "text", defaultWidth: 120 },
      { id: "shipDate", label: "Tanggal Kirim", filterType: "date", defaultWidth: 120 },
      { id: "shipStatus", label: "Status Pengiriman", filterType: "text", defaultWidth: 140 },
    ]),
  },
];

const BY_ID = new Map(
  PRODUCTION_FLOW_STAGE_TABLES.map((c) => [c.stageId, c] as const),
);

const designWs = BY_ID.get("flow-design")!;
const paymentWs = BY_ID.get("flow-payment")!;
BY_ID.set("flow-approval-design", {
  ...designWs,
  stageId: "flow-approval-design",
});
BY_ID.set("flow-approval-payment", {
  ...paymentWs,
  stageId: "flow-approval-payment",
});

export function getStageTableConfig(stageId: string): StageTableConfig | null {
  return BY_ID.get(stageId) ?? null;
}

/** Muat baris workspace; isi Status Selanjutnya dari stage sumber (bukan mutasi PO). */
export function loadStageTableRows(stageId: string): StageTableRow[] {
  const cfg = getStageTableConfig(stageId);
  if (!cfg) return [];
  const rows: StageTableRow[] = [];
  for (const src of cfg.sourceStageIds) {
    const next = nextStageLabelFor(src);
    for (const row of getProductionFlowDemoRows(src)) {
      rows.push({
        key: `${src}:${row.key}`,
        values: {
          ...row.values,
          nextStatus: next,
          _sourceStageId: src,
        },
      });
    }
  }
  return rows;
}
