/**
 * Resolve peran operasional mobile dari user Master — fail-closed.
 * Tidak menebak role dari email atau system role admin.
 * Tidak ada override URL / storage / state — hanya operational_role akun.
 */

import {
  TEAM_TASK_ROLES,
  type TeamTaskRole,
  type TeamTaskRoleId,
} from "../app/nav";
import type { UserPublic } from "../api";
import type { OperationalRole } from "../api/operational";

const ROLE_IDS = new Set<string>(TEAM_TASK_ROLES.map((r) => r.id));

export function isOperationalRoleId(value: string | null | undefined): value is TeamTaskRoleId {
  return Boolean(value && ROLE_IDS.has(value));
}

export function teamTaskRoleById(id: TeamTaskRoleId): TeamTaskRole {
  const found = TEAM_TASK_ROLES.find((r) => r.id === id);
  if (!found) {
    throw new Error(`Unknown operational role id: ${id}`);
  }
  return found;
}

/**
 * Role aktif untuk tab Tugas Saya.
 * - `operational_role` akun login menang (source-of-truth authentication)
 * - Super Admin tanpa ops role: null (tidak impersonate Kepala Produksi)
 * - Role kosong / tidak valid: null → UI terkunci, tanpa dummy task
 */
export function resolveOpsRoleFromUser(
  user: Pick<UserPublic, "operational_role" | "role"> | null | undefined,
): TeamTaskRole | null {
  const op = (user?.operational_role || "").trim();
  if (isOperationalRoleId(op)) {
    return teamTaskRoleById(op);
  }
  return null;
}

export function toOperationalRole(role: TeamTaskRole): OperationalRole {
  return role.id as OperationalRole;
}
