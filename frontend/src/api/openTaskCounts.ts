/**
 * Agregasi badge Tugas Tim dari task engine (mode API).
 *
 * Source of truth: `GET /api/tasks/open-counts` — total actionable authoritative
 * (branch + Loading-SJ assignee + exclude selesai/cancelled), tanpa cap 1000.
 */

import type { TeamTaskRoleId } from "../app/nav";
import { TEAM_TASK_ROLES } from "../app/nav";
import { isActionableOpenTask } from "../components/teamTaskUi";
import type { OperationalRole, OperationalTask } from "./operational";

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

/** Pure — dedupe task_key lalu hitung actionable per assigned_role (unit test). */
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

function normalizeCounts(
  raw: Record<string, unknown> | null | undefined,
): Record<TeamTaskRoleId, number> {
  const counts = emptyOpenTaskCounts();
  if (!raw || typeof raw !== "object") return counts;
  for (const role of TEAM_TASK_ROLES) {
    const n = Number(raw[role.id]);
    counts[role.id] = Number.isFinite(n) && n > 0 ? Math.trunc(n) : 0;
  }
  return counts;
}

/**
 * Ambil total actionable open per role dari endpoint count backend.
 */
export async function fetchOpenTeamTaskCountsFromApi(): Promise<OpenTaskCountsResult> {
  try {
    let res: Response;
    try {
      res = await fetch("/api/tasks/open-counts", {
        credentials: "include",
      });
    } catch (err) {
      return {
        status: "error",
        counts: emptyOpenTaskCounts(),
        error: err instanceof Error ? err.message : "Koneksi gagal memuat badge.",
      };
    }
    if (!res.ok) {
      return {
        status: "error",
        counts: emptyOpenTaskCounts(),
        error: `Gagal memuat jumlah tugas (HTTP ${res.status}).`,
      };
    }
    const body = (await res.json()) as {
      counts?: Record<string, number>;
      truncated?: boolean;
    };
    const counts = normalizeCounts(body.counts);
    if (body.truncated) {
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
  if (status === "truncated") {
    return `${count}+`;
  }
  return String(count);
}
