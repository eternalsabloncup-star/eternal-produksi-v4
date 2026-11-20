/** Klien read model: Buffer Stock, Alur Produksi, Dashboard, Laporan. */

export const NOT_CONFIGURED = "Belum dikonfigurasi";

export type BufferStockRow = {
  accurate_po_id: string;
  po_no: string;
  accurate_line_id: string;
  outlet_warehouse: string;
  item_code: string;
  item_name: string;
  color: string;
  unit: string;
  qty_ordered: number;
  qty_printed: number;
  qty_remaining: number;
  qty_ready_warehouse: number | string;
  ready_mismatch?: boolean;
  qty_shipped: number | string;
  buffer_status: string;
  sales_pic: string;
};

export type BufferStock = {
  items: BufferStockRow[];
  kpi: {
    total_buffer_ordered: number;
    qty_printed: number;
    qty_remaining: number;
    qty_ready_warehouse: number | string;
    qty_shipped: number;
    ready_mismatch_rows?: number;
    ready_incomplete_rows?: number;
  };
  not_configured: string[];
  ready_formula?: string;
};

export type FlowNode = {
  id: string;
  label: string;
  status: "selesai" | "berjalan" | "menunggu" | "tidak_berlaku";
  detail: string;
};

export type ProductionFlow = {
  accurate_po_id: string;
  po_no: string;
  has_print_items: boolean;
  nodes: FlowNode[];
  active_stages?: string[];
};

export type ProductionFlowStageRow = {
  accurate_po_id: string;
  po_no: string;
  node_id: string;
  stage_label: string;
  stage_status: FlowNode["status"];
  stage_detail: string;
  /** Label monitoring tabel: Aktif / Dalam Proses / Selesai — bukan SoT node. */
  monitoring_label?: string;
  active_stages: string[];
  has_print_items: boolean;
  acceptance: {
    status: string;
    accepted_by?: string | null;
    accepted_at?: string | null;
    inbox_status?: string;
  };
  po: Record<string, unknown>;
};

export type ProductionFlowByStage = {
  node_id: string;
  stage_label: string;
  items: ProductionFlowStageRow[];
  total: number;
  page: number;
  pageSize: number;
  scanned?: number;
  scan_cap?: number;
  scan_capped?: boolean;
  batch_size?: number;
  accepted_only?: boolean;
  source: string;
};

export type DashboardMetrics = {
  po_total: number;
  po_waiting_design_gate: number;
  po_waiting_payment_gate: number;
  lines_ready_to_schedule: number;
  tasks_open: number;
  qty_printed_by_unit: Record<string, number>;
  qty_packed_by_unit: Record<string, number>;
  sj_total: number;
  sj_loaded: number;
  sj_delivered_success: number;
  sj_delivered_failed: number;
  limitations: string[];
};

export class AnalyticsApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "AnalyticsApiError";
    this.status = status;
  }
}

async function request<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { credentials: "include" });
  } catch (err) {
    throw new AnalyticsApiError(
      err instanceof Error ? err.message : "Koneksi API gagal.",
      0,
    );
  }
  if (!res.ok) {
    let detail = `Permintaan gagal (HTTP ${res.status}).`;
    try {
      const data = (await res.json()) as { detail?: string };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      // bukan JSON
    }
    throw new AnalyticsApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export async function fetchBufferStock(): Promise<BufferStock> {
  return request("/api/buffer-stock");
}

export async function fetchProductionFlow(
  accuratePoId?: string,
): Promise<{ items: ProductionFlow[]; accepted_only?: boolean }> {
  const qs = accuratePoId
    ? `?accurate_po_id=${encodeURIComponent(accuratePoId)}`
    : "";
  return request(`/api/production-flow${qs}`);
}

export async function fetchProductionFlowByStage(params: {
  nodeId: string;
  page?: number;
  pageSize?: number;
  q?: string;
  urgent?: boolean;
  accuratePoId?: string;
}): Promise<ProductionFlowByStage> {
  const search = new URLSearchParams();
  search.set("node_id", params.nodeId);
  if (params.page) search.set("page", String(params.page));
  if (params.pageSize) search.set("pageSize", String(params.pageSize));
  if (params.q?.trim()) search.set("q", params.q.trim());
  if (params.urgent === true) search.set("urgent", "true");
  if (params.urgent === false) search.set("urgent", "false");
  if (params.accuratePoId) {
    search.set("accurate_po_id", params.accuratePoId);
  }
  return request(`/api/production-flow/by-stage?${search.toString()}`);
}

export async function fetchDashboard(): Promise<DashboardMetrics> {
  return request("/api/dashboard");
}

export async function fetchReport(
  kind: string,
  filters: { from?: string; to?: string; actor?: string; po?: string; status?: string } = {},
): Promise<{ kind: string; items: Record<string, unknown>[]; total: number }> {
  const search = new URLSearchParams();
  if (filters.from) search.set("date_from", filters.from);
  if (filters.to) search.set("date_to", filters.to);
  if (filters.actor) search.set("actor", filters.actor);
  if (filters.po) search.set("accurate_po_id", filters.po);
  if (filters.status) search.set("status", filters.status);
  const qs = search.toString();
  return request(`/api/reports/${encodeURIComponent(kind)}${qs ? `?${qs}` : ""}`);
}

export const REPORT_KINDS: { id: string; label: string }[] = [
  { id: "po", label: "Laporan PO" },
  { id: "allocations", label: "Jadwal & Alokasi Operator" },
  { id: "preparation", label: "Helper Persiapan Cup" },
  { id: "screen", label: "Screen" },
  { id: "printing", label: "Cetak per Operator" },
  { id: "qc-packing", label: "QC & Packing" },
  { id: "delivery-notes", label: "Surat Jalan & Loading" },
  { id: "deliveries", label: "Pengiriman & Bukti" },
  { id: "tasks", label: "Tugas & Riwayat Konfirmasi" },
];
