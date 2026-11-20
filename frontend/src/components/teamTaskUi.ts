/**
 * Helper presentasional Tugas Tim — murni, mudah diuji.
 * Tidak mengubah status backend.
 */

import type { OperationalTask, TaskAction } from "../api/operational";

export type StatusFilter = "all" | "belum" | "sebagian" | "selesai";

export type StatusCounts = {
  all: number;
  belum: number;
  sebagian: number;
  selesai: number;
};

/** Limit baca list per role — selaras backend max. */
export const TASK_LIST_FETCH_LIMIT = 1000;

export function taskDisplayLabel(
  task: Pick<OperationalTask, "task_type" | "label" | "context">,
): string {
  if (task.task_type === "pengiriman-gagal-koordinasi") {
    return "Koordinasi Pengiriman Gagal";
  }
  if (task.task_type === "qty-loading-sj-berbeda") {
    return task.label || "Koordinasi Loading–SJ";
  }
  if (task.task_type === "konfirmasi-sample") {
    const ctx = task.context || {};
    if (
      ctx.needs_resubmit === true ||
      ctx.display_kind === "sampel-ulang" ||
      ctx.status === "ditolak"
    ) {
      return "Sampel Ulang";
    }
    if (ctx.display_kind === "menunggu-approval") {
      return "Menunggu Approval";
    }
    return task.label || "Konfirmasi Sampel";
  }
  return task.label || task.task_type;
}

export function statusLabel(status: OperationalTask["status"]): string {
  if (status === "selesai") return "Selesai";
  if (status === "sebagian") return "Sebagian";
  return "Belum Dikerjakan";
}

export function statusBadgeClass(status: OperationalTask["status"]): string {
  if (status === "selesai") return "ep-task-status is-selesai";
  if (status === "sebagian") return "ep-task-status is-sebagian";
  return "ep-task-status is-belum";
}

/** Label shortfall Qty per jenis pekerjaan (Owner). */
export function shortfallLabel(taskType: string): string {
  if (taskType === "persiapan-cup") return "Belum Disiapkan";
  if (taskType === "qc-packing") return "Belum Di-QC & Packing";
  return "Kekurangan Qty";
}

export function aggregateStatusCounts(tasks: OperationalTask[]): StatusCounts {
  let belum = 0;
  let sebagian = 0;
  let selesai = 0;
  for (const t of tasks) {
    if (t.status === "selesai") selesai += 1;
    else if (t.status === "sebagian") sebagian += 1;
    else belum += 1;
  }
  return { all: tasks.length, belum, sebagian, selesai };
}

export function filterTasksByStatus(
  tasks: OperationalTask[],
  filter: StatusFilter,
): OperationalTask[] {
  if (filter === "all") return tasks;
  if (filter === "selesai") return tasks.filter((t) => t.status === "selesai");
  if (filter === "sebagian") return tasks.filter((t) => t.status === "sebagian");
  return tasks.filter((t) => t.status === "belum_dikerjakan");
}

export function formatCountBadge(n: number, truncated: boolean): string {
  if (truncated) return `${n}+`;
  return String(n);
}

export function isSchedulingTask(taskType: string): boolean {
  return taskType === "penjadwalan" || taskType === "penjadwalan-ulang";
}

export function isSampleTask(taskType: string): boolean {
  return taskType === "konfirmasi-sample" || taskType === "approval-sampel";
}

export function isCoordinationTask(taskType: string): boolean {
  return (
    taskType === "qty-loading-sj-berbeda" ||
    taskType === "pengiriman-gagal-koordinasi"
  );
}

export function remainingQty(task: OperationalTask): number | null {
  if (task.qty_target === undefined) return null;
  return Math.max(task.qty_target - task.qty_confirmed, 0);
}

/**
 * Cek grant Master untuk aksi. Super Admin: semua diizinkan di UI.
 * Grant belum/gagal dimuat → fail-closed (jangan buka mutasi).
 */
export function allowTaskAction(
  opts: {
    isSuperAdmin: boolean;
    grantsByTaskType: Map<string, TaskAction[]> | null;
    taskType: string;
    action: TaskAction;
  },
): boolean {
  if (opts.isSuperAdmin) return true;
  if (!opts.grantsByTaskType) return false;
  const actions = opts.grantsByTaskType.get(opts.taskType);
  if (!actions) return false;
  return actions.includes(opts.action);
}

/**
 * Filter presentasional selaras backend (Owner: assignment bukan gate).
 * Backend tetap memfilter ulang by branch + Master action.
 * ``operationsFull`` / Super Admin melihat semua tugas yang sudah di-fetch.
 */
export function filterTasksForActor(
  tasks: OperationalTask[],
  opts: {
    isSuperAdmin: boolean;
    actorEmail: string | null | undefined;
    operationalRole: string | null | undefined;
    operationsFull?: boolean;
  },
): OperationalTask[] {
  if (opts.isSuperAdmin || opts.operationsFull) return tasks;
  // Assignment metadata does not hide permitted tasks (Owner contract).
  // Branch filtering is enforced by backend list_tasks.
  return tasks;
}

/** Eligibility tombol Ubah Qty — dari kontrak backend, bukan status tampilan. */
export function allocationCanReviseQty(a: {
  status?: string;
  can_revise?: boolean;
  qty_printed?: number;
}): boolean {
  if (typeof a.can_revise === "boolean") return a.can_revise;
  return a.status === "active";
}

export function allocationReviseBounds(a: {
  qty: number;
  qty_printed?: number;
  qty_revise_min?: number;
  qty_revise_max?: number;
  lineQtyTarget?: number;
  otherActiveQty?: number;
}): { min: number; max: number } {
  const printed = Math.max(0, Number(a.qty_printed) || 0);
  const min =
    typeof a.qty_revise_min === "number" && Number.isFinite(a.qty_revise_min)
      ? a.qty_revise_min
      : printed;
  let max: number;
  if (typeof a.qty_revise_max === "number" && Number.isFinite(a.qty_revise_max)) {
    max = a.qty_revise_max;
  } else if (
    typeof a.lineQtyTarget === "number" &&
    typeof a.otherActiveQty === "number"
  ) {
    max = Math.max(a.lineQtyTarget - a.otherActiveQty, min);
  } else {
    max = Math.max(a.qty, min);
  }
  return { min, max: Math.max(max, min) };
}

export function sampleStatusLabel(status: string): string {
  if (status === "draft") return "Draft";
  if (status === "menunggu_approval") return "Menunggu Approval";
  if (status === "disetujui") return "Disetujui";
  if (status === "ditolak") return "Ditolak / Revisi";
  return status;
}

export function sampleStatusClass(status: string): string {
  if (status === "draft") return "ep-task-status is-draft";
  if (status === "menunggu_approval") return "ep-task-status is-wait";
  if (status === "disetujui") return "ep-task-status is-selesai";
  if (status === "ditolak") return "ep-task-status is-reject";
  return "ep-task-status";
}

/** Draft aktif = versi draft tertinggi (yang diajukan saat Konfirmasi). */
export function latestDraftSample<T extends { status: string; version: number }>(
  rows: T[],
): T | null {
  let best: T | null = null;
  for (const row of rows) {
    if (row.status !== "draft") continue;
    if (!best || row.version > best.version) best = row;
  }
  return best;
}
