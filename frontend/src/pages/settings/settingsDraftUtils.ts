import type {
  MasterTaskType,
  OperationalRole,
  RoleActionGrant,
  TaskAction,
} from "../../api/operational";

export type PolicyGrant = {
  permission: string;
  source: string;
  revocable: boolean;
  note?: string;
};

export function grantKey(role: string, taskType: string): string {
  return `${role}::${taskType}`;
}

export function serializeGrants(items: RoleActionGrant[]): string {
  return JSON.stringify(
    [...items]
      .map((g) => ({
        role: g.role,
        task_type: g.task_type,
        actions: [...g.actions].sort(),
      }))
      .sort((a, b) =>
        grantKey(a.role, a.task_type).localeCompare(grantKey(b.role, b.task_type)),
      ),
  );
}

export function serializePerms(perms: string[]): string {
  return [...perms].sort().join("|");
}

export function assignmentsFromTaskTypes(
  items: MasterTaskType[],
): Record<string, OperationalRole> {
  const map: Record<string, OperationalRole> = {};
  for (const t of items) map[t.task_type] = t.assigned_role;
  return map;
}

/**
 * Baseline hak modul efektif untuk draft: role perms + policy non-revocable.
 * Harus dipakai saat load role DAN saat buang draft (Phase 0 fix).
 */
export function baselineModulePerms(
  rolePerms: string[],
  policyGrants: PolicyGrant[] | undefined,
): string[] {
  const base = [...rolePerms];
  for (const g of policyGrants || []) {
    if (!g.revocable && !base.includes(g.permission)) {
      base.push(g.permission);
    }
  }
  return base;
}

export function modulePermsAreDirty(
  draftPerms: string[],
  rolePerms: string[],
  policyGrants: PolicyGrant[] | undefined,
): boolean {
  return (
    serializePerms(draftPerms) !==
    serializePerms(baselineModulePerms(rolePerms, policyGrants))
  );
}

/** Baris matriks yang boleh ditampilkan (sama aturan UI existing). */
export function isMatrixRowVisible(
  role: OperationalRole,
  task: MasterTaskType,
  grantedActions: TaskAction[],
): boolean {
  return grantedActions.length > 0 || task.assigned_role === role;
}

export type MatrixRowKey = { role: OperationalRole; task_type: string };

export type ColumnSelectState = "none" | "all" | "partial";

export function columnSelectState(
  rows: Array<{ actions: TaskAction[]; locked?: boolean }>,
  action: TaskAction,
): ColumnSelectState {
  const eligible = rows.filter((r) => !r.locked);
  if (eligible.length === 0) return "none";
  let on = 0;
  for (const r of eligible) {
    if (r.actions.includes(action)) on += 1;
  }
  if (on === 0) return "none";
  if (on === eligible.length) return "all";
  return "partial";
}

/**
 * Centang semua / hapus centang untuk satu aksi pada baris visible+eligible.
 * Tidak membuat kombinasi role×task di luar daftar rows.
 * Hanya mengubah draft lokal.
 */
export function applyColumnSelectAll(
  draftGrants: RoleActionGrant[],
  rows: MatrixRowKey[],
  action: TaskAction,
  enabled: boolean,
): RoleActionGrant[] {
  const rowKeys = new Set(rows.map((r) => grantKey(r.role, r.task_type)));
  const byKey = new Map(
    draftGrants.map((g) => [grantKey(g.role, g.task_type), { ...g, actions: [...g.actions] }]),
  );

  for (const row of rows) {
    const key = grantKey(row.role, row.task_type);
    if (!rowKeys.has(key)) continue;
    const existing = byKey.get(key);
    const current = existing?.actions || [];
    const next = enabled
      ? Array.from(new Set([...current, action]))
      : current.filter((a) => a !== action);
    byKey.set(key, {
      role: row.role,
      task_type: row.task_type,
      actions: next,
      is_default_mapping: existing?.is_default_mapping ?? false,
    });
  }

  return Array.from(byKey.values());
}

export function toggleDraftAction(
  prev: RoleActionGrant[],
  role: OperationalRole,
  taskType: string,
  action: TaskAction,
  enabled: boolean,
): RoleActionGrant[] {
  const existing = prev.find((g) => g.role === role && g.task_type === taskType);
  const current = existing?.actions || [];
  const next = enabled
    ? Array.from(new Set([...current, action]))
    : current.filter((a) => a !== action);
  const rest = prev.filter((g) => !(g.role === role && g.task_type === taskType));
  return [
    ...rest,
    {
      role,
      task_type: taskType,
      actions: next,
      is_default_mapping: existing?.is_default_mapping ?? false,
    },
  ];
}

export function filterMatrixRows<T extends { label: string; task_type: string }>(
  rows: T[],
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter(
    (r) =>
      r.label.toLowerCase().includes(q) ||
      r.task_type.toLowerCase().includes(q),
  );
}

export function countChangedGrants(
  draft: RoleActionGrant[],
  server: RoleActionGrant[],
): number {
  const baseline = new Map(
    server.map((g) => [grantKey(g.role, g.task_type), [...g.actions].sort().join("|")]),
  );
  const seen = new Set<string>();
  let n = 0;
  for (const g of draft) {
    const key = grantKey(g.role, g.task_type);
    seen.add(key);
    const next = [...g.actions].sort().join("|");
    if ((baseline.get(key) || "") !== next) n += 1;
  }
  for (const g of server) {
    const key = grantKey(g.role, g.task_type);
    if (!seen.has(key) && (g.actions?.length || 0) > 0) n += 1;
  }
  return n;
}
