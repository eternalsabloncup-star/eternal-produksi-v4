/**
 * Resolve peran operasional mobile dari user Master — fail-closed.
 * Tidak menebak role dari email atau system role admin.
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
  return TEAM_TASK_ROLES.find((r) => r.id === id) ?? TEAM_TASK_ROLES[0];
}

/**
 * Role aktif untuk tab Tugas Saya.
 * - operational_role Master menang
 * - Super Admin tanpa ops role: boleh memilih (caller menyediakan override)
 * - Selain itu: null → UI terkunci, tanpa dummy task
 */
export function resolveOpsRoleFromUser(
  user: Pick<UserPublic, "operational_role" | "role"> | null | undefined,
  override?: TeamTaskRoleId | null,
): TeamTaskRole | null {
  if (override && isOperationalRoleId(override)) {
    return teamTaskRoleById(override);
  }
  const op = (user?.operational_role || "").trim();
  if (isOperationalRoleId(op)) {
    return teamTaskRoleById(op);
  }
  return null;
}

export function toOperationalRole(role: TeamTaskRole): OperationalRole {
  return role.id as OperationalRole;
}
