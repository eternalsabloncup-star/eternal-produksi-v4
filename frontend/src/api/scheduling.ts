/** Klien API penjadwalan + approval desain publik. */

import { getCsrfToken } from "../api";

export type Allocation = {
  allocation_id: string;
  accurate_po_id: string;
  po_no: string;
  accurate_line_id: string;
  item_code: string;
  /** Identifier backend — email operator. */
  operator_email: string;
  /** Nama Master Pengguna bila tersedia; null bila tidak ada. */
  operator_display_name?: string | null;
  qty: number;
  status: string;
  schedule_date: string;
  qty_printed?: number;
  /** Floor revisi dari backend (cetak/screen allocation ini). */
  qty_revise_min?: number;
  /** Ceiling revisi = sisa kapasitas PO setelah alokasi aktif lain. */
  qty_revise_max?: number;
  /** Eligibility revisi dari backend — jangan infer hanya dari status tampilan. */
  can_revise?: boolean;
  /** Warna cetak operasional per alokasi; null pada record lama. */
  print_color?: string | null;
  /** Jenis screen operasional Besar/Kecil; terpisah dari Master Produk screen_size. */
  screen_kind?: string | null;
};

export type SchedulingOperator = {
  id: string;
  email: string;
  display_name: string;
};

export type ScreenKind = "Besar" | "Kecil";

export type AllocationCreateInput = {
  operator_email: string;
  qty: number;
  print_color?: string;
  screen_kind?: ScreenKind;
};

export type GateState = { state: "open" | "closed" | "review"; reason?: string };

export type LineSchedule = {
  accurate_po_id: string;
  po_no: string;
  accurate_line_id: string;
  item_code: string;
  item_name: string;
  /** D1: nama customer/pelanggan PO. */
  outlet_name?: string;
  /** D2: warna item Accurate (charField2) — bukan print_color alokasi. */
  paint_color?: string;
  /** D3: tanggal kirim SJ aktif unik untuk line ini (kalender). */
  ship_dates?: string[];
  qty_target: number;
  qty_allocated: number;
  qty_unallocated: number;
  qty_printed: number;
  /** Dari Master Produk; kosong → "Belum diatur". */
  screen_size?: string;
  allocations: Allocation[];
  gates: { design: GateState; payment: GateState };
  scheduling_unlocked: boolean;
};

export class SchedulingApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status = 0, code: string | null = null) {
    super(message);
    this.name = "SchedulingApiError";
    this.status = status;
    this.code = code;
  }
}

async function readError(
  res: Response,
): Promise<{ message: string; code: string | null }> {
  try {
    const data = (await res.json()) as {
      detail?: string | { code?: string; detail?: string };
    };
    if (typeof data.detail === "string") {
      return { message: data.detail, code: null };
    }
    if (data.detail && typeof data.detail === "object") {
      const code =
        typeof data.detail.code === "string" ? data.detail.code : null;
      const message =
        typeof data.detail.detail === "string"
          ? data.detail.detail
          : `Permintaan gagal (HTTP ${res.status}).`;
      return { message, code };
    }
  } catch {
    // bukan JSON
  }
  return { message: `Permintaan gagal (HTTP ${res.status}).`, code: null };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  const csrf = getCsrfToken();
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  if (init?.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(path, { ...init, method, headers, credentials: "include" });
  } catch (err) {
    throw new SchedulingApiError(
      err instanceof Error ? err.message : "Koneksi API gagal.",
      0,
    );
  }
  if (!res.ok) {
    const err = await readError(res);
    throw new SchedulingApiError(err.message, res.status, err.code);
  }
  return (await res.json()) as T;
}

export type ScheduleBoardKpi = {
  total_qty_so: number;
  qty_allocated: number;
  qty_printed: number;
  rows_unscheduled: number;
  rows_unlocked: number;
  print_line_count: number;
};

export type ScheduleBoardResponse = {
  items: LineSchedule[];
  total: number;
  total_rows?: number;
  page: number;
  pageSize: number;
  total_pages: number;
  po_count?: number;
  accepted_only: boolean;
  source: string;
  kpi: ScheduleBoardKpi;
  filter_facets?: Record<string, string[]>;
  sort_by?: string;
  sort_dir?: string;
};

/** Kolom yang difilter di server (sebelum pagination). */
export type ScheduleBoardColumnFilters = {
  outletName?: string[] | null;
  paintColor?: string[] | null;
  poNo?: string[] | null;
  itemName?: string[] | null;
  lineId?: string[] | null;
  shipDates?: string[] | null;
};

export async function fetchScheduleBoard(opts?: {
  q?: string;
  page?: number;
  pageSize?: number;
  columnFilters?: ScheduleBoardColumnFilters;
  sortBy?: string;
  sortDir?: "asc" | "desc";
}): Promise<ScheduleBoardResponse> {
  const params = new URLSearchParams();
  if (opts?.q?.trim()) params.set("q", opts.q.trim());
  if (opts?.page != null) params.set("page", String(opts.page));
  if (opts?.pageSize != null) params.set("pageSize", String(opts.pageSize));
  if (opts?.columnFilters && Object.keys(opts.columnFilters).length > 0) {
    params.set("columnFilters", JSON.stringify(opts.columnFilters));
  }
  if (opts?.sortBy) params.set("sortBy", opts.sortBy);
  if (opts?.sortDir) params.set("sortDir", opts.sortDir);
  const qs = params.toString();
  return request(`/api/scheduling/board${qs ? `?${qs}` : ""}`);
}

export async function fetchPoSchedule(
  accuratePoId: string,
): Promise<{
  accurate_po_id: string;
  lines: LineSchedule[];
  source: string;
  accepted_only?: boolean;
}> {
  return request(`/api/scheduling/${encodeURIComponent(accuratePoId)}`);
}

export async function fetchSchedulingOperators(): Promise<{
  items: SchedulingOperator[];
}> {
  return request("/api/scheduling/operators");
}

/** M8.3 read-only capacity / urgent advisory — informational; no auto-enforcement. */
export type CapacityAdvisory = {
  milestone: string;
  advisory_only: boolean;
  mode?: "INFORMATIONAL";
  enforcement: {
    line_qty_reservation: boolean;
    machine_shift_capacity: boolean;
    urgent_quota: boolean;
    urgent_margin_restriction: boolean;
    auto_reschedule: boolean;
    auto_urgent_override: boolean;
  };
  line_reservation: {
    status: string;
    unit: string;
    qty_target: number;
    reserved_capacity: number;
    remaining_capacity: number;
    projected_utilization: number | null;
    enforcement_active: boolean;
  } | null;
  machine_capacity: {
    status: string;
    unit: string;
    available_capacity: number | null;
    reserved_capacity: number | null;
    remaining_capacity: number | null;
    active_machine_count: number;
    active_shift_count: number;
    enforcement_active: boolean;
    gaps?: string[];
  };
  urgent_quota: {
    status: string;
    urgent_daily_quota: number | null;
    urgent_remaining: number | null;
    sales_owner: string | null;
    enforcement_active: boolean;
    gaps?: string[];
  };
  urgent_eligibility: {
    urgent_flag: boolean;
    sales_owner: string | null;
    margin_status: string;
    eligible_for_urgent_priority: boolean | null;
    enforcement_active: boolean;
  } | null;
  /** Display gaps only — not pending enforcement activation. */
  data_limitations: string[];
};

export async function fetchCapacityAdvisory(opts?: {
  accuratePoId?: string;
  accurateLineId?: string;
  branchCode?: string;
  scheduleDate?: string;
  shiftCode?: string;
  requestedQty?: number;
}): Promise<CapacityAdvisory> {
  const params = new URLSearchParams();
  if (opts?.accuratePoId) params.set("accuratePoId", opts.accuratePoId);
  if (opts?.accurateLineId) params.set("accurateLineId", opts.accurateLineId);
  if (opts?.branchCode) params.set("branchCode", opts.branchCode);
  if (opts?.scheduleDate) params.set("scheduleDate", opts.scheduleDate);
  if (opts?.shiftCode) params.set("shiftCode", opts.shiftCode);
  if (opts?.requestedQty != null) {
    params.set("requestedQty", String(opts.requestedQty));
  }
  const qs = params.toString();
  return request(`/api/scheduling/capacity-advisory${qs ? `?${qs}` : ""}`);
}

export async function createAllocations(
  accuratePoId: string,
  lineId: string,
  allocations: AllocationCreateInput[],
  idempotencyKey: string,
  scheduleDate?: string,
): Promise<{ allocations: Allocation[]; duplicate: boolean }> {
  return request(
    `/api/scheduling/${encodeURIComponent(accuratePoId)}/lines/${encodeURIComponent(
      lineId,
    )}/allocations`,
    {
      method: "POST",
      body: JSON.stringify({
        allocations,
        idempotency_key: idempotencyKey,
        ...(scheduleDate ? { schedule_date: scheduleDate } : {}),
      }),
    },
  );
}

export async function reviseAllocationQty(
  allocationId: string,
  qty: number,
  reason: string,
): Promise<Allocation> {
  return request(
    `/api/scheduling/allocations/${encodeURIComponent(allocationId)}/qty`,
    { method: "PUT", body: JSON.stringify({ qty, reason }) },
  );
}

export type PublicApprovalDecision = "pending" | "approved" | "rejected";

export type PublicApprovalContext = {
  po_no: string;
  customer: string;
  design_version: number;
  design_filename: string | null;
  design_evidence_id?: string | null;
  design_content_type?: string | null;
  preview_supported?: boolean;
  items: {
    item_name: string;
    item_code: string;
    qty: number;
    unit: string;
    color: string;
  }[];
  already_approved: boolean;
  approved_at?: string | null;
  decision?: PublicApprovalDecision;
  rejected_at?: string | null;
  reject_reason?: string | null;
};

export function publicDesignEvidenceUrl(token: string): string {
  return `/api/public/design-approval/${encodeURIComponent(token)}/evidence`;
}

export async function fetchPublicApproval(
  token: string,
): Promise<PublicApprovalContext> {
  const res = await fetch(`/api/public/design-approval/${encodeURIComponent(token)}`);
  if (!res.ok) {
    const err = await readError(res);
    throw new SchedulingApiError(err.message, res.status, err.code);
  }
  return (await res.json()) as PublicApprovalContext;
}

export async function submitPublicApproval(
  token: string,
  signerName: string,
  signaturePngBase64: string,
): Promise<{ duplicate: boolean }> {
  const res = await fetch(
    `/api/public/design-approval/${encodeURIComponent(token)}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        signer_name: signerName,
        signature_png_base64: signaturePngBase64,
      }),
    },
  );
  if (!res.ok) {
    const err = await readError(res);
    throw new SchedulingApiError(err.message, res.status, err.code);
  }
  return (await res.json()) as { duplicate: boolean };
}

export async function submitPublicRejection(
  token: string,
  rejectReason: string,
): Promise<{ rejected: boolean; duplicate: boolean; reject_reason?: string }> {
  const res = await fetch(
    `/api/public/design-approval/${encodeURIComponent(token)}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reject_reason: rejectReason }),
    },
  );
  if (!res.ok) {
    const err = await readError(res);
    throw new SchedulingApiError(err.message, res.status, err.code);
  }
  return (await res.json()) as {
    rejected: boolean;
    duplicate: boolean;
    reject_reason?: string;
  };
}
