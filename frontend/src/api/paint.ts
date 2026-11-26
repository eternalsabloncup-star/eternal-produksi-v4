/** Klien Manajemen Cat — stok, transaksi, stocktake, Accurate dark status. */

import { getCsrfToken } from "../api";

export class PaintApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "PaintApiError";
    this.status = status;
  }
}

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string | { msg?: string }[] };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      return String(body.detail[0].msg);
    }
  } catch {
    /* ignore */
  }
  return `Permintaan gagal (HTTP ${res.status}).`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const csrf = getCsrfToken();
  const method = (init?.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  if (init?.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path, { ...init, method, headers, credentials: "include" });
  } catch (err) {
    throw new PaintApiError(
      err instanceof Error ? err.message : "Koneksi API gagal.",
      0,
    );
  }
  if (!res.ok) throw new PaintApiError(await readError(res), res.status);
  return (await res.json()) as T;
}

export function newPaintIdempotencyKey(prefix: string): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${rand}`;
}

export type PaintReady = {
  module: string;
  accurate: {
    status: string;
    detail: string;
    adapter_enabled: boolean;
    ui_banner: string | null;
  };
  permissions: string[];
};

export type PaintCatalogItem = {
  paint_id: string;
  brand: string;
  kind: string;
  color: string;
  is_black?: boolean;
  is_active?: boolean;
  unit?: string;
};

export type PaintPurpose = {
  purpose_id: string;
  name: string;
  requires_po?: boolean;
  is_active?: boolean;
  sort_order?: number;
};

export type PaintStockRow = {
  branch_id: string;
  paint_id: string;
  qty_mg: number;
  qty_g?: number;
  version?: number;
  brand?: string;
  kind?: string;
  color?: string;
  updated_at?: string;
};

export type PaintDocument = {
  document_id: string;
  doc_type: string;
  status: string;
  branch_id: string;
  recorded_by?: string;
  used_by?: string;
  purpose_name?: string;
  created_at?: string;
  lines?: Array<{
    line_id: string;
    paint_id: string;
    qty_mg: number;
    qty_g?: number;
  }>;
};

export async function fetchPaintReady(): Promise<PaintReady> {
  return request("/api/paint/ready");
}

export async function fetchPaintCatalog(activeOnly = false) {
  const q = activeOnly ? "?active_only=true" : "";
  return request<{ items: PaintCatalogItem[]; total: number }>(
    `/api/paint/catalog${q}`,
  );
}

export async function upsertPaintCatalog(body: {
  brand: string;
  kind: string;
  color: string;
  is_black?: boolean;
  is_active?: boolean;
  paint_id?: string;
}) {
  return request<{ item: PaintCatalogItem }>("/api/paint/catalog", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function fetchPaintPurposes(activeOnly = false) {
  const q = activeOnly ? "?active_only=true" : "";
  return request<{ items: PaintPurpose[]; total: number }>(
    `/api/paint/purposes${q}`,
  );
}

export async function upsertPaintPurpose(body: {
  name: string;
  requires_po?: boolean;
  is_active?: boolean;
  sort_order?: number;
  purpose_id?: string;
}) {
  return request<{ item: PaintPurpose }>("/api/paint/purposes", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function fetchPaintStock(branchId?: string) {
  const q = branchId ? `?branch_id=${encodeURIComponent(branchId)}` : "";
  return request<{ items: PaintStockRow[]; total: number }>(
    `/api/paint/stock${q}`,
  );
}

export async function fetchPaintDocuments(opts?: {
  branch_id?: string;
  doc_type?: string;
  limit?: number;
}) {
  const params = new URLSearchParams();
  if (opts?.branch_id) params.set("branch_id", opts.branch_id);
  if (opts?.doc_type) params.set("doc_type", opts.doc_type);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return request<{ items: PaintDocument[]; total: number }>(
    `/api/paint/documents${qs ? `?${qs}` : ""}`,
  );
}

export async function confirmPaintInbound(body: {
  branch_id: string;
  paint_id: string;
  qty_g: number;
  idempotency_key: string;
  supplier?: string;
  accurate_ref?: string;
  received_by?: string;
  note?: string;
  physical_confirmed: boolean;
}) {
  return request<{ document: PaintDocument; duplicate: boolean }>(
    "/api/paint/inbound",
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function createPaintWithdrawal(body: {
  branch_id: string;
  used_by_email: string;
  purpose_id: string;
  items: Array<{ paint_id: string; qty_g: number }>;
  idempotency_key: string;
  accurate_po_id?: string;
  note?: string;
}) {
  return request<{ document: PaintDocument; duplicate: boolean }>(
    "/api/paint/withdrawals",
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function confirmPaintReturn(body: {
  withdrawal_id: string;
  withdrawal_line_id: string;
  qty_g: number;
  idempotency_key: string;
  condition?: string;
  note?: string;
}) {
  return request<{ document: PaintDocument; duplicate: boolean }>(
    "/api/paint/returns",
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function confirmPaintConversion(body: {
  branch_id: string;
  destination_paint_id: string;
  source_lines: Array<{
    withdrawal_id: string;
    withdrawal_line_id: string;
    qty_g: number;
  }>;
  verified_return_g: number;
  waste_g?: number;
  idempotency_key: string;
  note?: string;
}) {
  return request<{ document: PaintDocument; duplicate: boolean }>(
    "/api/paint/conversions",
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function recordPaintWaste(body: {
  withdrawal_id: string;
  withdrawal_line_id: string;
  qty_g: number;
  reason: string;
  idempotency_key: string;
}) {
  return request<{ document: PaintDocument; duplicate: boolean }>(
    "/api/paint/waste",
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function softDeletePaintDocument(
  documentId: string,
  body: { reason: string; idempotency_key: string },
) {
  return request<{ document: PaintDocument; duplicate?: boolean }>(
    `/api/paint/documents/${encodeURIComponent(documentId)}/soft-delete`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function correctPaintDocument(
  documentId: string,
  body: { reason: string; idempotency_key: string },
) {
  return request<{ document: PaintDocument; duplicate?: boolean }>(
    `/api/paint/documents/${encodeURIComponent(documentId)}/correct`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function fetchPaintReport(
  kind: string,
  opts?: { branch_id?: string; used_by?: string },
) {
  const params = new URLSearchParams();
  if (opts?.branch_id) params.set("branch_id", opts.branch_id);
  if (opts?.used_by) params.set("used_by", opts.used_by);
  const qs = params.toString();
  return request<{
    kind: string;
    items: Array<Record<string, unknown>>;
    total: number;
  }>(`/api/paint/reports/${encodeURIComponent(kind)}${qs ? `?${qs}` : ""}`);
}

export async function startPaintStocktake(body: {
  branch_id: string;
  counts?: Array<{ paint_id: string; counted_g: number }>;
}) {
  return request<{ stocktake: Record<string, unknown> }>("/api/paint/stocktakes", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function finalizePaintStocktake(
  stocktakeId: string,
  body: {
    idempotency_key: string;
    counts: Array<{ paint_id: string; counted_g: number }>;
  },
) {
  return request<Record<string, unknown>>(
    `/api/paint/stocktakes/${encodeURIComponent(stocktakeId)}/finalize`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function fetchPaintAccurateStatus() {
  return request<{
    status: string;
    detail: string;
    adapter_enabled: boolean;
    banner: string;
  }>("/api/paint/accurate/status");
}
