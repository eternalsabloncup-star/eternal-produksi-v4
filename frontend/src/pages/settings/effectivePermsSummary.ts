import type { SettingsRole, SettingsUser } from "../../api/settings";
import { baselineModulePerms, type PolicyGrant } from "./settingsDraftUtils";

export type EffectivePermsView = {
  selectedRoleKey: string;
  selectedRoleLabel: string;
  /** Izin efektif berdasarkan peran yang dipilih di form (+ policy non-revocable). */
  previewAfterSave: string[];
  /** Snapshot tersimpan pengguna; hanya relevan bila peran form = peran tersimpan. */
  currentSaved: string[];
  roleChanged: boolean;
  /** true bila preview memakai katalog peran, bukan snapshot lama peran lain. */
  previewFollowsSelectedRole: true;
};

/**
 * Ringkasan izin: preview selalu mengikuti peran form.
 * Snapshot lama tidak boleh dipakai sebagai izin peran baru.
 */
export function buildEffectivePermsView(args: {
  editingUser: SettingsUser | null;
  selectedRoleKey: string;
  roles: SettingsRole[];
  policyGrants: Record<string, PolicyGrant[] | undefined>;
}): EffectivePermsView {
  const selectedRoleKey = (args.selectedRoleKey || "").trim();
  const roleDoc = args.roles.find((r) => r.role_key === selectedRoleKey);
  const previewAfterSave = baselineModulePerms(
    roleDoc?.module_permissions || [],
    args.policyGrants[selectedRoleKey],
  );
  const savedRole = (args.editingUser?.role || "").trim();
  const roleChanged = Boolean(args.editingUser) && savedRole !== selectedRoleKey;
  const currentSaved = roleChanged
    ? []
    : [...(args.editingUser?.effective_module_permissions || [])].sort();

  return {
    selectedRoleKey,
    selectedRoleLabel: roleDoc?.label || selectedRoleKey || "—",
    previewAfterSave: [...previewAfterSave].sort(),
    currentSaved,
    roleChanged,
    previewFollowsSelectedRole: true,
  };
}
