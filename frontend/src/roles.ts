/** Role helpers — Super Admin never hides menus/buttons due to role limits. */

export const ROLE_SUPER_ADMIN = "super_admin";
export const ROLE_ADMIN = "admin";

export function isSuperAdmin(role: string | null | undefined): boolean {
  return (role || "").trim().toLowerCase() === ROLE_SUPER_ADMIN;
}

/**
 * Navigation/UI must not filter out items for Super Admin.
 * Returns true when the actor may see every module entry.
 */
export function canAccessAllModules(role: string | null | undefined): boolean {
  return isSuperAdmin(role);
}
