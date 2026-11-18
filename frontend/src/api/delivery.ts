/** Klien API Surat Jalan, Loading, dan pengiriman Driver. */

import { getCsrfToken } from "../api";

export type DeliveryNoteItem = {
  sj_line_id: string;
  accurate_line_id: string;
  item_code: string;
  item_name: string;
  qty: number;
  unit: string;
};

export type DeliveryScheduleRow = {
  accurate_sj_id: string;
  sj_no: string;
  accurate_po_id: string;
  po_no: string;
  ship_date: string;
  destination: string;
  shipment: string;
  items: DeliveryNoteItem[];
  loaded: boolean;
  delivery_status: string;
  attempts: number;
  superseded_by?: string | null;
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
): Promise<{ duplicate: boolean }> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}/loading`, {
    method: "POST",
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  });
}

export type LoadingEvent = {
  loading_id: string;
  sequence: number;
  sj_version: number;
  status: string;
  actor_email: string;
  cancelled_by?: string | null;
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
  created_at: string;
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

export async function fetchDeliveryDetail(sjId: string): Promise<{
  delivery_note: DeliveryScheduleRow;
  loading: { actor_email: string; created_at: string } | null;
  events: {
    attempt: number;
    status: string;
    courier_email: string;
    recipient_name: string | null;
    failure_reason: string | null;
    photo_evidence_ids: string[];
  }[];
  evidence: { photos: { evidence_id: string }[]; signatures: { evidence_id: string }[] };
}> {
  return request(`/api/delivery/${encodeURIComponent(sjId)}`);
}
