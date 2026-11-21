import { getCsrfToken } from "../api";

export type SettingsUser = {
  id: string;
  email: string;
  display_name: string;
  role: string;
  operational_role?: string | null;
  branch_ids: string[];
  is_active: boolean;
  effective_module_permissions: string[];
};

export type SettingsRole = {
  role_key: string;
  label: string;
  kind: string;
  module_permissions: string[];
  is_active: boolean;
  version: number;
};

export type AuditItem = {
  action: string;
  actor_email?: string | null;
  target_email?: string | null;
  detail?: Record<string, unknown>;
  created_at?: string;
};

export class SettingsApiError extends Error {
  readonly status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.name = "SettingsApiError";
    this.status = status;
  }
}

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
  if (init?.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path, { ...init, method, headers, credentials: "include" });
  } catch (err) {
    throw new SettingsApiError(
      err instanceof Error ? err.message : "Koneksi API gagal.",
      0,
    );
  }
  if (!res.ok) throw new SettingsApiError(await readError(res), res.status);
  return (await res.json()) as T;
}

export type PolicyGrant = {
  permission: string;
  source: string;
  revocable: boolean;
  note?: string;
};

export async function fetchSettingsCatalog() {
  return request<{
    module_permissions: string[];
    sensitive_module_permissions: string[];
    operational_roles: string[];
    policy_grants?: Record<string, PolicyGrant[]>;
    workflow_policies: {
      items: Array<{ id: string; label: string; status: string; note: string }>;
    };
  }>("/api/settings/catalog");
}

export async function fetchSettingsUsers() {
  return request<{ items: SettingsUser[] }>("/api/settings/users");
}

export async function createSettingsUser(body: {
  email: string;
  display_name: string;
  password: string;
  role: string;
  operational_role?: string | null;
  branch_ids?: string[];
}) {
  return request<SettingsUser>("/api/settings/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateSettingsUser(
  userId: string,
  body: {
    display_name?: string;
    role?: string;
    operational_role?: string | null;
    branch_ids?: string[];
    reason?: string;
  },
) {
  return request<SettingsUser>(`/api/settings/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function setUserActive(userId: string, is_active: boolean, reason?: string) {
  return request<SettingsUser>(`/api/settings/users/${userId}/active`, {
    method: "POST",
    body: JSON.stringify({ is_active, reason }),
  });
}

export async function resetUserPassword(
  userId: string,
  new_password: string,
  reason?: string,
) {
  return request<{ ok: boolean }>(`/api/settings/users/${userId}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ new_password, reason }),
  });
}

export async function fetchSettingsRoles() {
  return request<{ items: SettingsRole[] }>("/api/settings/roles");
}

export async function createSettingsRole(role_key: string, label: string) {
  return request<SettingsRole>("/api/settings/roles", {
    method: "POST",
    body: JSON.stringify({ role_key, label }),
  });
}

export async function saveRolePermissions(
  roleKey: string,
  body: {
    module_permissions: string[];
    expected_version: number;
    reason?: string;
    confirm_sensitive?: boolean;
  },
) {
  return request<SettingsRole>(
    `/api/settings/roles/${encodeURIComponent(roleKey)}/permissions`,
    {
      method: "PUT",
      headers: { "If-Match": String(body.expected_version) },
      body: JSON.stringify(body),
    },
  );
}

export type BranchMappingStatus = "mapped" | "unmapped" | "conflict";

export type SettingsBranch = {
  code: string;
  name: string;
  is_active: boolean;
  accurate_branch_id?: string | null;
  mapping_status?: BranchMappingStatus;
  accurate_verified?: boolean;
};

export async function fetchBranches() {
  return request<{ items: SettingsBranch[] }>("/api/settings/branches");
}

export async function upsertBranch(code: string, name: string, is_active = true) {
  return request<SettingsBranch>("/api/settings/branches", {
    method: "PUT",
    body: JSON.stringify({ code, name, is_active }),
  });
}

export async function setBranchActive(
  code: string,
  is_active: boolean,
  reason?: string,
) {
  return request<SettingsBranch>(
    `/api/settings/branches/${encodeURIComponent(code)}/active`,
    {
      method: "POST",
      body: JSON.stringify({ is_active, reason }),
    },
  );
}

export async function setBranchAccurateMapping(
  code: string,
  accurate_branch_id: string | null,
  reason: string,
) {
  return request<SettingsBranch>(
    `/api/settings/branches/${encodeURIComponent(code)}/accurate-mapping`,
    {
      method: "PUT",
      body: JSON.stringify({ accurate_branch_id, reason }),
    },
  );
}

export async function fetchMachines() {
  return request<{
    items: Array<{
      code: string;
      label: string;
      branch_code?: string;
      is_active: boolean;
    }>;
  }>("/api/settings/machines");
}

export async function upsertMachine(body: {
  code: string;
  label: string;
  branch_code?: string;
}) {
  return request("/api/settings/machines", {
    method: "PUT",
    body: JSON.stringify({ ...body, is_active: true }),
  });
}

export async function fetchShifts() {
  return request<{
    items: Array<{
      code: string;
      label: string;
      start_time: string;
      end_time: string;
      is_active: boolean;
    }>;
  }>("/api/settings/shifts");
}

export async function upsertShift(body: {
  code: string;
  label: string;
  start_time: string;
  end_time: string;
}) {
  return request("/api/settings/shifts", {
    method: "PUT",
    body: JSON.stringify({ ...body, is_active: true }),
  });
}

export async function fetchAccurateStatus() {
  return request<{
    configured: Record<string, boolean>;
    sj_live_sync_enabled: boolean;
    note: string;
  }>("/api/settings/accurate");
}

export async function fetchAuditLog(params?: { module?: string; limit?: number }) {
  const q = new URLSearchParams();
  if (params?.module) q.set("module", params.module);
  if (params?.limit) q.set("limit", String(params.limit));
  const suffix = q.toString() ? `?${q}` : "";
  return request<{ items: AuditItem[]; total: number }>(`/api/settings/audit${suffix}`);
}

export async function createManualTaskType(body: {
  task_type: string;
  label: string;
  scope: string;
  assigned_role: string;
}) {
  return request("/api/settings/task-types/manual", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function fetchSettingsProducts() {
  return request<{
    items: Array<{ item_code: string; screen_size?: string }>;
    screen_unset_label: string;
    source: string;
  }>("/api/settings/products");
}

export async function saveProductScreenSize(itemCode: string, screenSize: string) {
  return request<{
    item: { item_code: string; screen_size?: string };
    display_screen_size: string;
  }>("/api/settings/products/screen-size", {
    method: "PUT",
    body: JSON.stringify({ item_code: itemCode, screen_size: screenSize }),
  });
}
