/**
 * Ringkasan Monitoring read-only — hanya dari task engine yang sudah di-fetch.
 * Bukan jalur konfirmasi kedua; tidak menciptakan KPI spekulatif.
 */

import type { OperationalTask } from "../api/operational";
import {
  aggregateStatusCounts,
  isCoordinationTask,
  isCancelledOrInactiveTask,
  type StatusCounts,
} from "../components/teamTaskUi";

export type MonitoringSummary = StatusCounts & {
  coordination: number;
  /** Qty target − confirmed untuk tugas actionable (bila qty_target ada). */
  qtyShortfall: number | null;
};

export function buildMonitoringSummary(
  tasks: OperationalTask[],
): MonitoringSummary {
  const counts = aggregateStatusCounts(tasks);
  let coordination = 0;
  let shortfallSum = 0;
  let hasQty = false;

  for (const t of tasks) {
    if (isCancelledOrInactiveTask(t)) continue;
    if (t.coordination || isCoordinationTask(t.task_type)) {
      if (t.status !== "selesai") coordination += 1;
    }
    if (typeof t.qty_target === "number" && Number.isFinite(t.qty_target)) {
      hasQty = true;
      shortfallSum += Math.max(t.qty_target - (t.qty_confirmed || 0), 0);
    }
  }

  return {
    ...counts,
    coordination,
    qtyShortfall: hasQty ? shortfallSum : null,
  };
}
