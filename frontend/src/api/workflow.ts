/**
 * Klien Design / Payment / Gates — kontrak dari backend/app/api/workflow.py.
 * Tidak ada fallback DEMO.
 */

import { getCsrfToken } from "../api";

export class WorkflowApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "WorkflowApiError";
    this.status = status;
  }
}

export type GateState = "open" | "closed" | "review" | string;

export type GateSlice = {
  state: GateState;
  reason?: string | null;
  detail?: Record<string, unknown> | null;
  updated_at?: string | null;
  actor_email?: string | null;
};

export type DesignSubmission = {
  accurate_po_id: string;
  po_no?: string;
  version: number;
  evidence_id?: string;
  filename?: string;
  submitted_by?: string;
  submitted_at?: string;
  confirmed_at?: string | null;
  confirmed_by?: string | null;
};

export type DesignApproval = {
  accurate_po_id: string;
  design_version: number;
  signer_name?: string;
  approved_at?: string;
};

export type DesignStateResponse = {
  accurate_po_id: string;
  submissions: DesignSubmission[];
  latest: DesignSubmission | null;
  approval: DesignApproval | null;
  gates: Record<string, GateSlice>;
};

export type DesignSubmitResponse = {
  submission: DesignSubmission;
  evidence: { evidence_id: string; filename?: string };
};

export type DesignApprovalLinkResponse = {
  token: string;
  link: {
    accurate_po_id: string;
    design_version: number;
    expires_at?: string;
    created_by?: string;
    created_at?: string;
  };
};

export type PaymentReceipt = {
  accurate_receipt_id: string;
  accurate_po_id: string;
  po_no?: string;
  invoice_no?: string | null;
  amount?: number;
  receipt_date?: string | null;
};

export type PaymentReconciliation = {
  decision: "open" | "review" | "closed" | string;
  reason?: string;
  total_amount?: number;
  receipt_ids?: string[];
};

export type PaymentStateResponse = {
  accurate_po_id: string;
  receipts: PaymentReceipt[];
  reconciliation: PaymentReconciliation;
  gates: Record<string, GateSlice>;
};

export type PaymentReceiptInput = {
  id: string;
  invoice_no?: string | null;
  amount: number;
  receipt_date?: string | null;
};

async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // ignore
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
  if (init?.body && !(init.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  let res: Response;
  try {
    res = await fetch(path, { ...init, method, headers, credentials: "include" });
  } catch (err) {
    throw new WorkflowApiError(
      err instanceof Error ? err.message : "Koneksi API gagal.",
      0,
    );
  }
  if (!res.ok) throw new WorkflowApiError(await readError(res), res.status);
  return (await res.json()) as T;
}

/** GET /api/design/{accurate_po_id} */
export async function fetchDesignState(
  accuratePoId: string,
): Promise<DesignStateResponse> {
  return request(`/api/design/${encodeURIComponent(accuratePoId)}`);
}

/** URL stream bukti desain (sesi cookie) — jangan spekulasi path lain. */
export function designEvidenceContentUrl(
  accuratePoId: string,
  evidenceId: string,
): string {
  return `/api/design/${encodeURIComponent(accuratePoId)}/evidence/${encodeURIComponent(
    evidenceId,
  )}/content`;
}

/** POST /api/design/{accurate_po_id}/submission — multipart file */
export async function submitDesignFile(
  accuratePoId: string,
  file: File,
): Promise<DesignSubmitResponse> {
  const form = new FormData();
  form.append("file", file);
  return request(`/api/design/${encodeURIComponent(accuratePoId)}/submission`, {
    method: "POST",
    body: form,
  });
}

/** POST /api/design/{accurate_po_id}/confirm */
export async function confirmDesignVersion(
  accuratePoId: string,
  version: number,
): Promise<DesignSubmission> {
  return request(`/api/design/${encodeURIComponent(accuratePoId)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ version }),
  });
}

/**
 * POST /api/design/{accurate_po_id}/approval-link
 * Token mentah hanya sekali — tampilkan ke user untuk disalin.
 */
export async function createDesignApprovalLink(
  accuratePoId: string,
): Promise<DesignApprovalLinkResponse> {
  return request(`/api/design/${encodeURIComponent(accuratePoId)}/approval-link`, {
    method: "POST",
  });
}

/** GET /api/payment/{accurate_po_id} */
export async function fetchPaymentState(
  accuratePoId: string,
): Promise<PaymentStateResponse> {
  return request(`/api/payment/${encodeURIComponent(accuratePoId)}`);
}

/**
 * POST /api/payment/{accurate_po_id}/receipts
 * Memerlukan PERM_OPERATIONS_FULL; idempotent per receipt id.
 */
export async function ingestPaymentReceipts(
  accuratePoId: string,
  receipts: PaymentReceiptInput[],
): Promise<{ receipts: PaymentReceipt[]; gates: Record<string, GateSlice> }> {
  return request(`/api/payment/${encodeURIComponent(accuratePoId)}/receipts`, {
    method: "POST",
    body: JSON.stringify({ receipts }),
  });
}

/** Respons POST /api/payment/{id}/bypass — dokumen gate + flag idempotency. */
export type BypassPaymentResponse = Record<string, GateSlice | unknown> & {
  payment?: GateSlice;
  design?: GateSlice;
  accurate_po_id?: string;
  duplicate?: boolean;
  workflow?: {
    created_task_keys?: string[];
    scheduling_unlocked?: boolean;
  } | null;
};

/**
 * POST /api/payment/{accurate_po_id}/bypass
 * Memerlukan grant Master payment-bypass/approve; alasan wajib; teraudit.
 */
export async function bypassPaymentGate(
  accuratePoId: string,
  reason: string,
): Promise<BypassPaymentResponse> {
  return request(`/api/payment/${encodeURIComponent(accuratePoId)}/bypass`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export function customerApprovalPath(token: string): string {
  return `/approval/${encodeURIComponent(token)}`;
}
