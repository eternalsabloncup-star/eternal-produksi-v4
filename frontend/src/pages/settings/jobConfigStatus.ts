import type { MasterTaskType, RoleActionGrant } from "../../api/operational";
import type { SettingsBranch, SettingsUser } from "../../api/settings";
import type { JobConfigStatus } from "./settingsNav";
import type { ResourceLoadState } from "./loadState";

export type JobConfigLoadInputs = {
  usersLoad: ResourceLoadState;
  grantsLoad: ResourceLoadState;
  branchesLoad: ResourceLoadState;
};

function loadIsUsable(state: ResourceLoadState | undefined): boolean {
  return state === "SUCCESS" || state === "DATA_EMPTY";
}

function normalizeBranchCodes(codes: string[] | undefined): Set<string> {
  return new Set((codes || []).map((c) => c.trim().toUpperCase()).filter(Boolean));
}

/** Active branch codes from master — empty set means no active branches. */
export function activeBranchCodeSet(branches: SettingsBranch[]): Set<string> {
  return normalizeBranchCodes(
    branches.filter((b) => b.is_active).map((b) => b.code),
  );
}

/**
 * Apakah pengguna aktif dengan peran kerja punya cakupan cabang valid
 * terhadap master cabang aktif. Jika tidak ada cabang aktif, anggap lolos
 * (tidak ada kebutuhan cabang yang bisa diverifikasi).
 */
export function userCoversActiveBranches(
  user: SettingsUser,
  activeBranches: Set<string>,
): boolean {
  if (activeBranches.size === 0) return true;
  const have = normalizeBranchCodes(user.branch_ids);
  for (const code of have) {
    if (activeBranches.has(code)) return true;
  }
  return false;
}

/** Hitung status konfigurasi dari data nyata — tidak menulis / merestore grant. */
export function computeJobConfigStatus(args: {
  task: MasterTaskType;
  grants: RoleActionGrant[];
  users: SettingsUser[];
  branches?: SettingsBranch[];
  load?: JobConfigLoadInputs;
}): JobConfigStatus {
  const load = args.load;
  if (load) {
    if (!loadIsUsable(load.usersLoad) || !loadIsUsable(load.grantsLoad)) {
      return "BELUM_TERVERIFIKASI";
    }
    if (!loadIsUsable(load.branchesLoad)) {
      return "BELUM_TERVERIFIKASI";
    }
  }

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

  const candidates = args.users.filter(
    (u) =>
      u.is_active &&
      (u.operational_role || "").trim() === assigned,
  );
  if (candidates.length === 0) return "BELUM_ADA_PENGGUNA_AKTIF";

  // Branch coverage: only when branch master load is usable (or omitted in unit tests).
  if (load || args.branches) {
    const active = activeBranchCodeSet(args.branches || []);
    if (active.size > 0) {
      const covered = candidates.some((u) => userCoversActiveBranches(u, active));
      if (!covered) return "BELUM_TERVERIFIKASI";
    }
  }

  return "SIAP";
}
