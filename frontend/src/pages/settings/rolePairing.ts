import type { SettingsRole, SettingsUser } from "../../api/settings";

/** System roles that must never be auto-recommended. */
export const BLOCKED_AUTO_SYSTEM_ROLES = new Set(["super_admin"]);

/** Module permissions that disqualify a system role from auto-recommendation. */
export const BLOCKED_AUTO_SYSTEM_PERMS = new Set([
  "settings.manage",
  "operations.full",
  "payment.bypass",
  "access.all_modules",
  "users.manage",
  "roles.manage",
]);

export type RolePairingResult =
  | {
      kind: "recommend";
      systemRole: string;
      reason: string;
    }
  | {
      kind: "require_explicit";
      reason: string;
      candidates: string[];
    }
  | {
      kind: "none";
      reason: string;
    };

function roleIsSafeForAuto(role: SettingsRole | undefined): boolean {
  if (!role) return false;
  if (BLOCKED_AUTO_SYSTEM_ROLES.has(role.role_key)) return false;
  const perms = role.module_permissions || [];
  return !perms.some((p) => BLOCKED_AUTO_SYSTEM_PERMS.has(p));
}

/**
 * Recommend a system role for an operational role ONLY from existing user evidence.
 * Never invents mappings. Never recommends sensitive/system-admin roles.
 */
export function recommendSystemRoleForOps(args: {
  operationalRole: string;
  users: SettingsUser[];
  roles: SettingsRole[];
}): RolePairingResult {
  const ops = (args.operationalRole || "").trim();
  if (!ops) {
    return { kind: "none", reason: "Belum memilih jabatan/pekerjaan." };
  }

  const roleByKey = new Map(args.roles.map((r) => [r.role_key, r]));
  const counts = new Map<string, number>();
  for (const u of args.users) {
    if (!u.is_active) continue;
    if ((u.operational_role || "").trim() !== ops) continue;
    const sys = (u.role || "").trim();
    if (!sys) continue;
    if (!roleIsSafeForAuto(roleByKey.get(sys))) continue;
    counts.set(sys, (counts.get(sys) || 0) + 1);
  }

  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (ranked.length === 0) {
    return {
      kind: "require_explicit",
      reason:
        "Belum ada bukti pairing aman dari pengguna aktif untuk jabatan ini. Pilih peran sistem secara eksplisit.",
      candidates: [],
    };
  }
  if (ranked.length > 1 && ranked[0][1] === ranked[1][1]) {
    return {
      kind: "require_explicit",
      reason:
        "Beberapa peran sistem dipakai setara untuk jabatan ini. Tidak ada pemetaan tunggal yang aman.",
      candidates: ranked.map(([k]) => k),
    };
  }
  if (ranked.length > 1) {
    // Unique plurality winner — still only recommend if clear majority uniqueness of top key
    // (already unique max). OK to recommend top.
  }
  const winner = ranked[0][0];
  return {
    kind: "recommend",
    systemRole: winner,
    reason: `Disarankan dari ${ranked[0][1]} pengguna aktif dengan jabatan yang sama (dapat diubah).`,
  };
}

export function isSensitiveSystemRoleKey(roleKey: string): boolean {
  return BLOCKED_AUTO_SYSTEM_ROLES.has(roleKey);
}
