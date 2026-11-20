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

export async function fetchPoSchedule(
  accuratePoId: string,
): Promise<{ accurate_po_id: string; lines: LineSchedule[]; source: string }> {
  return request(`/api/scheduling/${encodeURIComponent(accuratePoId)}`);
}

export async function fetchSchedulingOperators(): Promise<{
  items: SchedulingOperator[];
}> {
  return request("/api/scheduling/operators");
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

export type PublicApprovalContext = {
  po_no: string;
  customer: string;
  design_version: number;
  design_filename: string;
  items: {
    item_name: string;
    item_code: string;
    qty: number;
    unit: string;
    color: string;
  }[];
  already_approved: boolean;
};

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
