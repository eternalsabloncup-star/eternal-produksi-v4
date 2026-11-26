import type { MasterTaskType, RoleActionGrant } from "../../api/operational";
import type { SettingsUser } from "../../api/settings";
import type { JobConfigStatus } from "./settingsNav";

/** Hitung status konfigurasi dari data nyata — tidak menulis / merestore grant. */
export function computeJobConfigStatus(args: {
  task: MasterTaskType;
  grants: RoleActionGrant[];
  users: SettingsUser[];
}): JobConfigStatus {
  const assigned = (args.task.assigned_role || "").trim();
  if (!assigned) return "BELUM_ADA_PENANGGUNG_JAWAB";

  const relevant = (args.task.actions || []).filter((a) => a !== "view");
  const check = relevant.length > 0 ? relevant : args.task.actions || [];
  const grant = args.grants.find(
    (g) => g.role === assigned && g.task_type === args.task.task_type,
  );
  const have = new Set(grant?.actions || []);
  if (check.length > 0 && !check.some((a) => have.has(a))) {
    return "IZIN_BELUM_LENGKAP";
  }

  const hasActiveUser = args.users.some(
    (u) =>
      u.is_active &&
      (u.operational_role || "").trim() === assigned,
  );
  if (!hasActiveUser) return "BELUM_ADA_PENGGUNA_AKTIF";

  return "SIAP";
}
