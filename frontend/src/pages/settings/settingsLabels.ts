import type { OperationalRole, TaskAction } from "../../api/operational";

/** Label peran kerja (lantai produksi) — istilah bisnis Eternal. */
export const ROLE_WORK_LABEL: Record<string, string> = {
  "production-head": "Kepala Produksi",
  designer: "Designer",
  helper: "Helper",
  screener: "Screener",
  operator: "Operator",
  driver: "Driver",
};

/** Label aksi pada jenis pekerjaan. */
export const ACTION_LABEL: Record<string, string> = {
  view: "Lihat",
  input: "Input",
  confirm: "Konfirmasi",
  approve: "Approval",
  edit: "Edit",
  delete: "Hapus",
  manage_evidence: "Kelola Bukti",
  cancel: "Batal",
};

/** Label hak akses menu (permission ID → bahasa operasional). */
export const MODULE_PERM_LABEL: Record<string, string> = {
  "access.all_modules": "Akses semua menu",
  "access.all_branches": "Akses semua cabang",
  "users.manage": "Kelola pengguna",
  "roles.manage": "Kelola peran sistem",
  "settings.manage": "Kelola pengaturan",
  "reports.view": "Lihat laporan",
  "audit.view": "Lihat catatan perubahan",
  "operations.full": "Operasional penuh",
  "payment.bypass": "Lewati pembayaran",
  "po.branch.edit": "Ubah cabang PO",
  "operations.tasks.view_all": "Lihat semua tugas",
};

export const SCOPE_LABEL: Record<string, string> = {
  po: "Per PO",
  po_line: "Per baris PO",
  po_line_allocation: "Per alokasi baris PO",
  delivery_note: "Per Surat Jalan",
};

export function modulePermLabel(id: string): string {
  return MODULE_PERM_LABEL[id] || id;
}

export function roleWorkLabel(role: string): string {
  return ROLE_WORK_LABEL[role] || role;
}

export function actionLabel(action: string): string {
  return ACTION_LABEL[action] || action;
}

export function scopeLabel(scope: string): string {
  return SCOPE_LABEL[scope] || scope;
}

export function workflowStatusLabel(status: string): string {
  if (status === "available") return "Tersedia";
  if (status === "unavailable") return "Belum tersedia";
  return status;
}

/** Urutan kolom aksi matriks (stabil untuk UI). */
export const MATRIX_ACTION_ORDER: TaskAction[] = [
  "view",
  "input",
  "confirm",
  "approve",
  "edit",
  "delete",
  "manage_evidence",
  "cancel",
];

export function sortActions(actions: TaskAction[]): TaskAction[] {
  return [...actions].sort(
    (a, b) => MATRIX_ACTION_ORDER.indexOf(a) - MATRIX_ACTION_ORDER.indexOf(b),
  );
}

export type OperationalRoleOption = OperationalRole;
