/** Role helpers — Super Admin never hides menus/buttons due to role limits. */

export const ROLE_SUPER_ADMIN = "super_admin";
export const ROLE_ADMIN = "admin";
/** Canonical Master role_key Holding — bukan label/email. */
export const ROLE_HOLDING = "holding";
export const PERM_VIEW_ALL_TASKS = "operations.tasks.view_all";
export const PERM_OPERATIONS_FULL = "operations.full";

export function isSuperAdmin(role: string | null | undefined): boolean {
  return (role || "").trim().toLowerCase() === ROLE_SUPER_ADMIN;
}

export function isHoldingRole(role: string | null | undefined): boolean {
  return (role || "").trim().toLowerCase() === ROLE_HOLDING;
}

export function hasPermission(
  permissions: string[] | null | undefined,
  permission: string,
): boolean {
  return Boolean(permissions?.includes(permission));
}

/** Read-only lintas 6 role — Holding auto atau grant eksplisit (bukan operations.full). */
export function hasViewAllTasks(user: {
  role?: string | null;
  permissions?: string[] | null;
} | null | undefined): boolean {
  if (!user) return false;
  if (isSuperAdmin(user.role)) return true;
  if (isHoldingRole(user.role)) return true;
  return hasPermission(user.permissions, PERM_VIEW_ALL_TASKS);
}

export function hasOperationsFull(user: {
  role?: string | null;
  permissions?: string[] | null;
} | null | undefined): boolean {
  if (!user) return false;
  if (isSuperAdmin(user.role)) return true;
  return hasPermission(user.permissions, PERM_OPERATIONS_FULL);
}

/**
 * Navigation/UI must not filter out items for Super Admin.
 * Returns true when the actor may see every module entry.
 */
export function canAccessAllModules(role: string | null | undefined): boolean {
  return isSuperAdmin(role);
}
