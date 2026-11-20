/**
 * Agregasi badge Tugas Tim dari task engine (mode API).
 *
 * Source of truth: per-role `GET /api/tasks?role=&done=false` (sama scope auth
 * branch seperti panel), lalu hitung actionable = Belum + Sebagian.
 * Satu fetch global tanpa role rentan terpotong limit lintas-role.
 */

import type { TeamTaskRoleId } from "../app/nav";
import { TEAM_TASK_ROLES } from "../app/nav";
import {
  actionableOpenCount,
  isActionableOpenTask,
} from "../components/teamTaskUi";
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

/** Pure — dedupe task_key lalu hitung actionable per assigned_role. */
export function aggregateOpenCountsByRole(
  items: OperationalTask[],
): Record<TeamTaskRoleId, number> {
  const counts = emptyOpenTaskCounts();
  const known = new Set<string>(TEAM_TASK_ROLES.map((r) => r.id));
  const seen = new Set<string>();
  for (const task of items) {
    if (!isActionableOpenTask(task)) continue;
    const key = task.task_key || "";
    if (key) {
      if (seen.has(key)) continue;
      seen.add(key);
    }
    const role = task.assigned_role as OperationalRole;
    if (known.has(role)) {
      counts[role as TeamTaskRoleId] += 1;
    }
  }
  return counts;
}

/**
 * Ambil tugas terbuka per role (done=false) lalu hitung Belum+Sebagian.
 * Truncated bila salah satu role mencapai limit baca.
 */
export async function fetchOpenTeamTaskCountsFromApi(): Promise<OpenTaskCountsResult> {
  try {
    const results = await Promise.all(
      TEAM_TASK_ROLES.map((role) =>
        fetchTasks({
          role: role.id as OperationalRole,
          done: false,
          limit: OPEN_TASK_FETCH_LIMIT,
        }),
      ),
    );

    const counts = emptyOpenTaskCounts();
    let truncated = false;
    for (let i = 0; i < TEAM_TASK_ROLES.length; i += 1) {
      const role = TEAM_TASK_ROLES[i];
      const res = results[i];
      if (res.items.length >= OPEN_TASK_FETCH_LIMIT) truncated = true;
      // done=false sudah exclude selesai; actionableOpenCount juga exclude cancelled.
      counts[role.id] = actionableOpenCount(res.items);
    }

    return {
      status: truncated ? "truncated" : "ready",
      counts,
    };
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
  // Truncated: per-role angka adalah batas bawah — selalu beri +.
  if (status === "truncated") {
    return `${count}+`;
  }
  return String(count);
}
