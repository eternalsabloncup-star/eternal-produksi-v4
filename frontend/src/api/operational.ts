/**
 * Klien API operasional V4 (Master + task engine).
 * Mode API memakai backend nyata; tidak ada fallback fixture DEMO.
 */

import { getCsrfToken } from "../api";

export type OperationalRole =
  | "production-head"
  | "designer"
  | "helper"
  | "screener"
  | "operator"
  | "driver";

export type TaskAction =
  | "view"
  | "input"
  | "confirm"
  | "approve"
  | "edit"
  | "delete"
  | "manage_evidence";

export type MasterTaskType = {
  task_type: string;
  label: string;
  scope: string;
  assigned_role: OperationalRole;
  actions: TaskAction[];
  coordination: boolean;
  partial_qty: boolean;
  is_default_mapping: boolean;
};

export type RoleActionGrant = {
  role: OperationalRole;
  task_type: string;
  actions: TaskAction[];
  is_default_mapping: boolean;
};

export type OperationalTask = {
  task_key: string;
  task_type: string;
  label?: string;
  scope: string;
  assigned_role: OperationalRole;
  status: "belum_dikerjakan" | "sebagian" | "selesai";
  qty_target?: number;
  qty_confirmed: number;
  coordination?: boolean;
  ref: {
    accurate_po_id?: string;
    po_no?: string;
    accurate_line_id?: string;
    item_code?: string;
    process_kind?: string;
    allocation_id?: string;
    accurate_delivery_note_id?: string;
    sj_no?: string;
  };
  context?: Record<string, unknown>;
};

export type TaskConfirmation = {
  idempotency_key: string;
  qty: number;
  actor_email: string;
  actor_role: string;
  note?: string | null;
  created_at: string;
};

export class OperationalApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "OperationalApiError";
    this.status = status;
  }
}

async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // body bukan JSON
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
    throw new OperationalApiError(
      err instanceof Error ? err.message : "Koneksi API gagal.",
      0,
    );
  }
  if (!res.ok) throw new OperationalApiError(await readError(res), res.status);
  return (await res.json()) as T;
}

export async function fetchTaskTypes(): Promise<{
  items: MasterTaskType[];
  roles: OperationalRole[];
  actions: TaskAction[];
}> {
  return request("/api/master/task-types");
}

export async function fetchRoleActions(
  role?: OperationalRole,
): Promise<{ items: RoleActionGrant[] }> {
  const q = role ? `?role=${encodeURIComponent(role)}` : "";
  return request(`/api/master/role-actions${q}`);
}

export async function assignTaskTypeRole(
  taskType: string,
  role: OperationalRole,
): Promise<MasterTaskType> {
  return request(`/api/master/task-types/${encodeURIComponent(taskType)}/role`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

export async function setRoleActions(
  role: OperationalRole,
  taskType: string,
  actions: TaskAction[],
): Promise<RoleActionGrant> {
  return request("/api/master/role-actions", {
    method: "PUT",
    body: JSON.stringify({ role, task_type: taskType, actions }),
  });
}

export async function fetchTasks(params: {
  role?: OperationalRole;
  taskType?: string;
  done?: boolean;
  /** Backend default 200, max 1000 — wajib diisi bila menghitung total terbuka. */
  limit?: number;
}): Promise<{ items: OperationalTask[]; total: number; source: string }> {
  const search = new URLSearchParams();
  if (params.role) search.set("role", params.role);
  if (params.taskType) search.set("task_type", params.taskType);
  if (params.done !== undefined) search.set("done", String(params.done));
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  const qs = search.toString();
  return request(`/api/tasks${qs ? `?${qs}` : ""}`);
}

export async function fetchTaskConfirmations(
  taskKey: string,
): Promise<{ task_key: string; items: TaskConfirmation[] }> {
  return request(`/api/tasks/${encodeURIComponent(taskKey)}/confirmations`);
}

export async function confirmTaskQty(
  taskKey: string,
  qty: number,
  idempotencyKey: string,
  note?: string,
): Promise<{ task: OperationalTask; duplicate: boolean }> {
  return request(`/api/tasks/${encodeURIComponent(taskKey)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ qty, idempotency_key: idempotencyKey, note }),
  });
}

export type ProductionSample = {
  sample_id: string;
  allocation_id: string;
  version: number;
  filename: string;
  status: "menunggu_approval" | "disetujui" | "ditolak";
  submitted_by: string;
  submitted_at: string;
  reject_reason?: string | null;
};

/** Konfirmasi Qty untuk pekerjaan produksi yang punya endpoint khusus. */
export async function confirmProductionQty(
  task: OperationalTask,
  qty: number,
  idempotencyKey: string,
): Promise<{ task: OperationalTask; duplicate: boolean }> {
  const { accurate_po_id: po, accurate_line_id: line, allocation_id: alloc } = task.ref;
  const body = JSON.stringify({ qty, idempotency_key: idempotencyKey });
  if (task.task_type === "persiapan-cup") {
    return request(`/api/production/${po}/lines/${line}/preparation`, {
      method: "POST",
      body,
    });
  }
  if (task.task_type === "qc-packing") {
    return request(`/api/production/${po}/lines/${line}/qc-packing`, {
      method: "POST",
      body,
    });
  }
  if (task.task_type === "screen") {
    return request(`/api/production/allocations/${alloc}/screen`, {
      method: "POST",
      body,
    });
  }
  if (task.task_type === "cetak") {
    return request(`/api/production/allocations/${alloc}/print`, {
      method: "POST",
      body,
    });
  }
  return confirmTaskQty(task.task_key, qty, idempotencyKey);
}

export async function fetchSamples(
  allocationId: string,
): Promise<{ allocation_id: string; items: ProductionSample[] }> {
  return request(`/api/production/allocations/${encodeURIComponent(allocationId)}/samples`);
}

export async function uploadSample(
  allocationId: string,
  file: File,
): Promise<{ sample: ProductionSample }> {
  const form = new FormData();
  form.append("file", file);
  const csrf = getCsrfToken();
  const res = await fetch(
    `/api/production/allocations/${encodeURIComponent(allocationId)}/samples`,
    {
      method: "POST",
      credentials: "include",
      headers: csrf ? { "X-CSRF-Token": csrf } : {},
      body: form,
    },
  );
  if (!res.ok) throw new OperationalApiError(await readError(res), res.status);
  return (await res.json()) as { sample: ProductionSample };
}

export async function decideSample(
  allocationId: string,
  version: number,
  approved: boolean,
  reason?: string,
): Promise<ProductionSample> {
  return request(
    `/api/production/allocations/${encodeURIComponent(allocationId)}/samples/decision`,
    {
      method: "POST",
      body: JSON.stringify({ version, approved, reason }),
    },
  );
}

/** Kunci idempotency stabil per aksi pengguna (anti double-click/retry). */
export function newIdempotencyKey(prefix: string): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${rand}`;
}
