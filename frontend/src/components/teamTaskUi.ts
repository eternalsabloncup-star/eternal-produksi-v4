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
  if (task.task_type === "loading-sj") {
    return task.label || "Loading Surat Jalan";
  }
  if (task.task_type === "loading-menunggu-sj") {
    return task.label || "Loading — Menunggu Surat Jalan";
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

/** Loading dibatalkan / preliminary siap (loading-sj ada) — bukan pekerjaan aktif. */
export function isCancelledOrInactiveTask(
  task: Pick<OperationalTask, "task_type" | "context">,
): boolean {
  const ctx = task.context;
  if (!ctx || typeof ctx !== "object") return false;
  const c = ctx as {
    loading_cancelled?: unknown;
    loading_task_ready?: unknown;
  };
  if (c.loading_cancelled === true) return true;
  if (task.task_type === "loading-menunggu-sj" && c.loading_task_ready === true) {
    return true;
  }
  return false;
}

/**
 * Tugas actionable untuk badge / filter Belum+Sebagian:
 * status terbuka dan belum dibatalkan.
 */
export function isActionableOpenTask(
  task: Pick<OperationalTask, "status" | "task_type" | "context">,
): boolean {
  if (task.status === "selesai") return false;
  if (isCancelledOrInactiveTask(task)) return false;
  return true;
}

export function statusBadgeClass(status: OperationalTask["status"]): string {
  if (status === "selesai") return "ep-task-status is-selesai";
  if (status === "sebagian") return "ep-task-status is-sebagian";
  return "ep-task-status is-belum";
}

/** Label shortfall Qty per jenis pekerjaan (Owner). */
export function shortfallLabel(
  taskType: string,
  processKind?: string | null,
): string {
  if (taskType === "persiapan-cup") return "Belum Disiapkan";
  if (taskType === "qc-packing") {
    if (processKind === "Non-Cetak") return "Belum Di-Packing";
    return "Belum Di-QC & Packing";
  }
  return "Kekurangan Qty";
}

export function aggregateStatusCounts(tasks: OperationalTask[]): StatusCounts {
  let belum = 0;
  let sebagian = 0;
  let selesai = 0;
  for (const t of tasks) {
    if (t.status === "selesai") {
      selesai += 1;
      continue;
    }
    // Cancelled/inactive tidak masuk Belum/Sebagian (selaras badge).
    if (isCancelledOrInactiveTask(t)) continue;
    if (t.status === "sebagian") sebagian += 1;
    else belum += 1;
  }
  return { all: tasks.length, belum, sebagian, selesai };
}

/** Badge sidebar = Belum Dikerjakan + Sebagian (actionable). */
export function actionableOpenCount(tasks: OperationalTask[]): number {
  const c = aggregateStatusCounts(tasks);
  return c.belum + c.sebagian;
}

export function filterTasksByStatus(
  tasks: OperationalTask[],
  filter: StatusFilter,
): OperationalTask[] {
  if (filter === "all") return tasks;
  if (filter === "selesai") return tasks.filter((t) => t.status === "selesai");
  if (filter === "sebagian") {
    return tasks.filter(
      (t) => t.status === "sebagian" && !isCancelledOrInactiveTask(t),
    );
  }
  // Belum = terbuka selain sebagian (termasuk status non-selesai lain), bukan hanya
  // exact "belum_dikerjakan" — selaras aggregateStatusCounts.
  return tasks.filter(
    (t) =>
      t.status !== "selesai" &&
      t.status !== "sebagian" &&
      !isCancelledOrInactiveTask(t),
  );
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

export function isLoadingSjTask(taskType: string): boolean {
  return taskType === "loading-sj";
}

/** Tugas informatif setelah Cetak — bukan konfirmasi Loading SJ. */
export function isPreliminaryLoadingTask(taskType: string): boolean {
  return taskType === "loading-menunggu-sj";
}

export function preliminaryLoadingDisplay(task: {
  context?: Record<string, unknown> | null;
}): {
  waiting: boolean;
  loadingReady: boolean;
  pendingLoading: boolean;
  statusText: string;
  qtyPrinted: number | null;
  sjLabel: string;
} {
  const ctx = task.context || {};
  const linked = Array.isArray(ctx.linked_sj_ids) ? ctx.linked_sj_ids : [];
  const waiting =
    ctx.waiting_for_sj === true ||
    (ctx.waiting_for_sj !== false && linked.length === 0);
  const loadingReady = ctx.loading_task_ready === true;
  const pendingLoading = !waiting && !loadingReady;
  const qtyRaw = ctx.qty_printed;
  const qtyPrinted =
    typeof qtyRaw === "number"
      ? qtyRaw
      : qtyRaw != null && Number.isFinite(Number(qtyRaw))
        ? Number(qtyRaw)
        : null;
  const nos = Array.isArray(ctx.linked_sj_nos)
    ? ctx.linked_sj_nos.map((x) => String(x || "").trim()).filter(Boolean)
    : [];
  const ids = linked.map((x) => String(x || "").trim()).filter(Boolean);
  const sjLabel = waiting
    ? "Belum tersedia"
    : nos.length
      ? nos.join(", ")
      : ids.length
        ? ids.join(", ")
        : "Tersedia";
  let statusText: string;
  if (waiting) {
    statusText = "Menunggu Surat Jalan";
  } else if (pendingLoading) {
    statusText = String(
      ctx.display_status || "SJ tersedia — tugas Loading belum siap",
    );
  } else {
    statusText = String(
      ctx.display_status ||
        "SJ tersedia — konfirmasi pada tugas Loading Surat Jalan",
    );
  }
  return { waiting, loadingReady, pendingLoading, statusText, qtyPrinted, sjLabel };
}

export function remainingQty(task: OperationalTask): number | null {
  if (task.qty_target === undefined) return null;
  return Math.max(task.qty_target - task.qty_confirmed, 0);
}

export const ITEM_NAME_FALLBACK = "Nama barang belum tersedia";

export type TaskItemLineView = {
  accurateLineId: string;
  itemName: string;
  qty: number | null;
  unit: string;
};

function _scalarItemName(
  task: Pick<OperationalTask, "ref" | "item_name" | "context">,
): string {
  const fromRef = String(task.ref?.item_name || "").trim();
  if (fromRef) return fromRef;
  const fromTop = String(task.item_name || "").trim();
  if (fromTop) return fromTop;
  const ctx = task.context;
  if (ctx && typeof ctx === "object") {
    const fromCtx = String(
      (ctx as { item_name?: unknown }).item_name || "",
    ).trim();
    if (fromCtx) return fromCtx;
  }
  return "";
}

/**
 * Nama Barang utama untuk tampilan Tugas Tim.
 * Urutan: ref.item_name → top-level item_name → item_lines → context.item_name → fallback jujur.
 * Tidak memakai item_code / accurate_line_id sebagai nama.
 */
export function taskItemDisplayName(
  task: Pick<OperationalTask, "ref" | "item_name" | "item_lines" | "context">,
): string {
  const scalar = _scalarItemName(task);
  if (scalar) return scalar;
  const fromLines = taskItemLines(task)
    .map((l) => l.itemName)
    .filter(Boolean);
  if (fromLines.length) return fromLines.join("\n");
  return ITEM_NAME_FALLBACK;
}

/** Baris produk untuk Item & Progres vertikal (multi-item aman). */
export function taskItemLines(
  task: Pick<OperationalTask, "item_lines" | "ref" | "item_name" | "context">,
): TaskItemLineView[] {
  const raw = Array.isArray(task.item_lines) ? task.item_lines : [];
  const rows: TaskItemLineView[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const name = String(row.item_name || "").trim();
    if (!name) continue;
    const qtyNum = Number(row.qty);
    rows.push({
      accurateLineId: String(row.accurate_line_id || "").trim(),
      itemName: name,
      qty: Number.isFinite(qtyNum) ? qtyNum : null,
      unit: String(row.unit || "PCS").trim() || "PCS",
    });
  }
  if (rows.length) return rows;
  const single = _scalarItemName(task);
  if (single) {
    return [
      {
        accurateLineId: String(task.ref?.accurate_line_id || "").trim(),
        itemName: single,
        qty: null,
        unit: "PCS",
      },
    ];
  }
  return [];
}

/** Subteks teknis: Kode · Line (tanpa allocation id sebagai identitas produk). */
export function taskItemSecondaryMeta(
  task: Pick<OperationalTask, "ref">,
): string {
  const parts: string[] = [];
  const code = String(task.ref?.item_code || "").trim();
  const line = String(task.ref?.accurate_line_id || "").trim();
  if (code) parts.push(`Kode ${code}`);
  if (line) parts.push(`Line ${line}`);
  return parts.join(" · ");
}

/**
 * Form konfirmasi Qty:
 * - Screen: qty manual tanpa qty_target (kontrak backend) → tetap tampilkan form.
 * - Tugas qty lain: butuh qty_target agar sisa sah terdefinisi.
 */
export function canShowQtyConfirmForm(
  task: Pick<OperationalTask, "task_type" | "qty_target">,
): boolean {
  if (isPreliminaryLoadingTask(task.task_type)) return false;
  if (task.task_type === "screen") return true;
  return task.qty_target !== undefined;
}

/**
 * Qty Alokasi dari context Screen (info operasional).
 * Bukan qty_target — Screen tidak dibatasi jadwal di backend.
 */
export function screenAllocQty(
  task: Pick<OperationalTask, "task_type" | "context">,
): number | null {
  if (task.task_type !== "screen") return null;
  const raw = task.context?.qty_alokasi;
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : null;
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
 * Filter presentasional selaras backend ``loading_sj_visible_to_actor``:
 * cabang di server; Loading-SJ dengan assignee hanya untuk email yang ditugaskan
 * kecuali SA / operations.full / operations.tasks.view_all (read).
 * ``viewAllTasks`` ≠ mutation authority.
 */
export function filterTasksForActor(
  tasks: OperationalTask[],
  opts: {
    isSuperAdmin: boolean;
    actorEmail: string | null | undefined;
    operationalRole: string | null | undefined;
    operationsFull?: boolean;
    /** Effective ``operations.tasks.view_all`` (Holding auto atau grant). */
    viewAllTasks?: boolean;
  },
): OperationalTask[] {
  if (opts.isSuperAdmin || opts.operationsFull || opts.viewAllTasks) {
    return tasks;
  }
  const email = (opts.actorEmail || "").trim().toLowerCase();
  // Loading SJ: hanya assignee yang melihat sebagai pekerjaan aktif.
  return tasks.filter((t) => {
    if (t.task_type !== "loading-sj") return true;
    const assigned = String(
      (t.context && (t.context as { assigned_user_email?: string }).assigned_user_email) ||
        "",
    )
      .trim()
      .toLowerCase();
    if (!assigned) return true;
    return Boolean(email) && email === assigned;
  });
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
