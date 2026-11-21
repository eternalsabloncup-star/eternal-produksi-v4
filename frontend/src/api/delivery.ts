/** Klien API Surat Jalan, Loading, dan pengiriman Driver. */

import { getCsrfToken } from "../api";

export type DeliveryNoteItem = {
  sj_line_id: string;
  accurate_line_id: string;
  item_code: string;
  item_name: string;
  qty: number;
  unit: string;
  warehouse_name?: string;
  detail_notes?: string;
};

/** Snapshot header Surat Jalan Accurate (Delivery Order) apa adanya. */
export type DeliveryNote = {
  accurate_sj_id: string;
  sj_no: string;
  accurate_po_id: string;
  /** Nomor Sales Order Accurate = identitas PO di V4. */
  po_no: string;
  /** DO.poNumber = nomor PO milik pelanggan. */
  customer_po_no?: string;
  sj_date?: string;
  ship_date: string;
  customer_name?: string;
  customer_no?: string;
  destination: string;
  contact_info?: string;
  contact_phone?: string;
  salesman_name?: string;
  created_by_user_name?: string;
  shipment: string;
  ship_zone?: string;
  branch_name?: string;
  description?: string;
  printed_by_user?: string;
  printed_time?: string;
  packing_number?: string;
  accurate_status_name?: string;
  attachment_exist?: boolean | null;
  sj_version?: number;
  revision_status?: string;
  items: DeliveryNoteItem[];
  source_meta?: {
    absent_fields?: string[];
    char_field3_zone_unproven?: boolean;
    ship_date_source?: string;
  };
  superseded_by?: string | null;
};

export type DeliveryScheduleRow = DeliveryNote & {
  item_count: number;
  qty_by_unit: Record<string, number>;
  loaded: boolean;
  loaded_at?: string | null;
  loading_id?: string | null;
  needs_reloading: boolean;
  delivery_status: string;
  attempts: number;
  driver_email: string;
  recipient_name: string;
};

export type DeliverySchedule = {
  items: DeliveryScheduleRow[];
  summary: {
    sj_count: number;
    qty_by_unit: Record<string, number>;
    destination_count: number;
  };
  source: string;
};

export type LoadingValidation = {
  accurate_sj_id: string;
  sj_no: string;
  sj_version: number;
  revision_status: string;
  needs_reloading: boolean;
  delivered: boolean;
  active_loading_id: string | null;
  all_matched: boolean;
  already_loaded: boolean;
  items: {
    sj_line_id: string;
    accurate_line_id: string;
    item_code: string;
    item_name?: string;
    qty: number;
    unit: string;
    process_kind: string | null;
    qty_available: number;
    matched: boolean;
    reason: string | null;
  }[];
};

export class DeliveryApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "DeliveryApiError";
    this.status = status;
  }
}

async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // bukan JSON
  }
  return `Permintaan gagal (HTTP ${res.status}).`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  const csrf = getCsrfToken();
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  if (init?.body && typeof init.body === "string" && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path, { ...init, method, headers, credentials: "include" });
  } catch (err) {
    throw new DeliveryApiError(
      err instanceof Error ? err.message : "Koneksi API gagal.",
      0,
    );
  }
  if (!res.ok) throw new DeliveryApiError(await readError(res), res.status);
  return (await res.json()) as T;
}

export async function fetchDeliverySchedule(params: {
  from?: string;
  to?: string;
}): Promise<DeliverySchedule> {
  const search = new URLSearchParams();
  if (params.from) search.set("ship_date_from", params.from);
  if (params.to) search.set("ship_date_to", params.to);
  const qs = search.toString();
  return request(`/api/delivery/schedule${qs ? `?${qs}` : ""}`);
}

export async function fetchLoadingValidation(
  sjId: string,
): Promise<LoadingValidation> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/loading-validation`);
}

export async function confirmLoading(
  sjId: string,
  idempotencyKey: string,
  assignedDriverEmail?: string | null,
): Promise<{ duplicate: boolean }> {
  const body: Record<string, string> = { idempotency_key: idempotencyKey };
  if (assignedDriverEmail && assignedDriverEmail.trim()) {
    body.assigned_driver_email = assignedDriverEmail.trim();
  }
  return request(`/api/delivery/${encodeURIComponent(sjId)}/loading`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Sesuai — validasi + confirm Loading (T3). */
export async function confirmLoadingMatch(
  sjId: string,
  idempotencyKey: string,
): Promise<{ duplicate: boolean }> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/loading/match`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  });
}

/** Tidak Sesuai — discrepancy KP (T3). */
export async function rejectLoadingMismatch(
  sjId: string,
  reason?: string,
): Promise<{ discrepancy_task?: { task_key: string } }> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/loading/mismatch`, {
    method: "POST",
    body: JSON.stringify({ reason: reason || null }),
  });
}

export async function assignLoadingTask(
  sjId: string,
  assignedUserEmail: string,
): Promise<{ task_key?: string }> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/loading/assign`, {
    method: "POST",
    body: JSON.stringify({ assigned_user_email: assignedUserEmail }),
  });
}

export type LoadingEvent = {
  loading_id: string;
  sequence: number;
  sj_version: number;
  status: string;
  actor_email: string;
  actor_display_name?: string;
  created_at?: string;
  cancelled_by?: string | null;
  /** Enrichment Master display_name untuk pembatal — email tetap di storage. */
  cancelled_by_display_name?: string;
  cancel_reason?: string | null;
  items: { accurate_line_id: string; qty: number; process_kind: string | null }[];
};

export type NoteRevision = {
  version: number;
  changed_items: {
    sj_line_id: string;
    accurate_line_id: string;
    qty_before: number | null;
    qty_after: number | null;
    change: string;
  }[];
  changed_header_fields?: {
    field: string;
    before: unknown;
    after: unknown;
  }[];
  created_at: string;
};

export type DeliveryEvent = {
  attempt: number;
  status: string;
  courier_email: string;
  actor_display_name?: string;
  recipient_name: string | null;
  failure_reason: string | null;
  photo_evidence_ids: string[];
  signature_evidence_id?: string | null;
  created_at?: string;
};

export type DeliveryEvidence = {
  photos: { evidence_id: string; filename?: string }[];
  signatures: { evidence_id: string; filename?: string }[];
};

export type DeliveryDetail = {
  delivery_note: DeliveryNote;
  loading: LoadingEvent | null;
  loadings: LoadingEvent[];
  revisions: NoteRevision[];
  events: DeliveryEvent[];
  evidence: DeliveryEvidence;
};

export async function cancelLoading(
  sjId: string,
  reason: string,
): Promise<{ cancelled: boolean; duplicate: boolean }> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/loading/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function fetchRevisions(
  sjId: string,
): Promise<{ revisions: NoteRevision[]; loadings: LoadingEvent[] }> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/revisions`);
}

export async function raiseDiscrepancy(sjId: string): Promise<{ task_key: string }> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/discrepancy`, {
    method: "POST",
  });
}

export async function confirmCoordination(
  taskKey: string,
  idempotencyKey: string,
): Promise<{ duplicate?: boolean }> {
  return request("/api/delivery/coordination/confirm", {
    method: "POST",
    body: JSON.stringify({ task_key: taskKey, idempotency_key: idempotencyKey }),
  });
}

export async function confirmDelivered(
  sjId: string,
  recipientName: string,
  photos: File[],
  signature: Blob,
): Promise<{ status: string; photo_evidence_ids: string[] }> {
  const form = new FormData();
  form.append("recipient_name", recipientName);
  for (const photo of photos) form.append("photos", photo);
  form.append("signature", signature, "signature.png");
  return request(`/api/delivery/${encodeURIComponent(sjId)}/delivered`, {
    method: "POST",
    body: form,
  });
}

export async function confirmDeliveryFailed(
  sjId: string,
  reason: string,
): Promise<{ coordination_task_key: string }> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/failed`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function fetchDeliveryDetail(
  sjId: string,
): Promise<DeliveryDetail> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}`);
}

/** URL stream bukti foto/tanda tangan SJ — sesi cookie; soft-delete → 404. */
export function deliveryEvidenceContentUrl(
  sjId: string,
  evidenceId: string,
): string {
  return `/api/delivery/${encodeURIComponent(sjId)}/evidence/${encodeURIComponent(evidenceId)}/content`;
}

/** Alasan soft-delete wajib (≥3), selaras validasi backend. */
export const SOFT_DELETE_REASON_MIN = 3;

export function assertSoftDeleteReason(reason: string): string {
  const trimmed = reason.trim();
  if (trimmed.length < SOFT_DELETE_REASON_MIN) {
    throw new DeliveryApiError(
      `Alasan soft-delete wajib (min. ${SOFT_DELETE_REASON_MIN} karakter).`,
      422,
    );
  }
  return trimmed;
}

/** Pesan UI untuk status HTTP soft-delete yang umum. */
export function softDeleteEvidenceErrorMessage(err: unknown): string {
  if (err instanceof DeliveryApiError) {
    const detail = err.message?.trim();
    if (err.status === 403) {
      return detail
        ? `${detail} (403)`
        : "Izin manage_evidence ditolak (403).";
    }
    if (err.status === 404) {
      return detail
        ? `${detail} (404)`
        : "Bukti tidak ditemukan atau sudah di-soft-delete (404).";
    }
    if (err.status === 409) {
      return detail ? `${detail} (409)` : "Konflik soft-delete (409).";
    }
    if (err.status === 0) {
      return detail || "Koneksi gagal — soft-delete tidak dijalankan.";
    }
    return detail || `Soft-delete gagal (HTTP ${err.status}).`;
  }
  if (err instanceof Error) return err.message;
  return "Soft-delete bukti gagal.";
}

/**
 * Soft-delete bukti pengiriman — file/audit tetap; butuh manage_evidence.
 * POST /api/delivery/evidence/soft-delete
 * Sukses hanya setelah respons backend OK; bukan hard delete.
 */
export async function softDeleteDeliveryEvidence(
  evidenceId: string,
  reason: string,
): Promise<{ evidence_id: string; deleted?: boolean }> {
  const safeReason = assertSoftDeleteReason(reason);
  const id = evidenceId.trim();
  if (!id) {
    throw new DeliveryApiError("evidence_id wajib.", 422);
  }
  return request("/api/delivery/evidence/soft-delete", {
    method: "POST",
    body: JSON.stringify({ evidence_id: id, reason: safeReason }),
  });
}
