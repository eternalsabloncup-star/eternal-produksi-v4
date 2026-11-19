/**
 * Agregasi badge Tugas Tim dari task engine (mode API).
 * Satu request done=false; hitung per assigned_role.
 * total respons = len(items) — jika items mencapai limit, angka tidak dijamin lengkap.
 */

import type { TeamTaskRoleId } from "../app/nav";
import { TEAM_TASK_ROLES } from "../app/nav";
import {
  fetchTasks,
  type OperationalRole,
  type OperationalTask,
} from "./operational";

/** Limit maksimum backend list_tasks. */
export const OPEN_TASK_FETCH_LIMIT = 1000;

export type OpenTaskCountsStatus = "loading" | "ready" | "error" | "truncated";

export type OpenTaskCountsResult = {
  status: OpenTaskCountsStatus;
  counts: Record<TeamTaskRoleId, number>;
  error?: string;
};

export function emptyOpenTaskCounts(): Record<TeamTaskRoleId, number> {
  const out = {} as Record<TeamTaskRoleId, number>;
  for (const role of TEAM_TASK_ROLES) out[role.id] = 0;
  return out;
}

/** Pure — dipakai unit test dan fetch. */
export function aggregateOpenCountsByRole(
  items: OperationalTask[],
): Record<TeamTaskRoleId, number> {
  const counts = emptyOpenTaskCounts();
  const known = new Set<string>(TEAM_TASK_ROLES.map((r) => r.id));
  for (const task of items) {
    const role = task.assigned_role as OperationalRole;
    if (known.has(role)) {
      counts[role as TeamTaskRoleId] += 1;
    }
  }
  return counts;
}

/**
 * Ambil seluruh tugas belum selesai (status ≠ selesai) hingga limit.
 * Truncated → status "truncated" (tampilkan indikator, bukan nol palsu).
 */
export async function fetchOpenTeamTaskCountsFromApi(): Promise<OpenTaskCountsResult> {
  try {
    const res = await fetchTasks({
      done: false,
      limit: OPEN_TASK_FETCH_LIMIT,
    });
    const counts = aggregateOpenCountsByRole(res.items);
    if (res.items.length >= OPEN_TASK_FETCH_LIMIT) {
      return { status: "truncated", counts };
    }
    return { status: "ready", counts };
  } catch (err) {
    return {
      status: "error",
      counts: emptyOpenTaskCounts(),
      error: err instanceof Error ? err.message : "Gagal memuat jumlah tugas.",
    };
  }
}

export function formatOpenTaskBadge(
  count: number,
  status: OpenTaskCountsStatus,
): string | null {
  if (status === "loading") return "…";
  if (status === "error") return "?";
  if (count <= 0) return null;
  if (status === "truncated" && count >= OPEN_TASK_FETCH_LIMIT) {
    return `${OPEN_TASK_FETCH_LIMIT}+`;
  }
  return String(count);
}
