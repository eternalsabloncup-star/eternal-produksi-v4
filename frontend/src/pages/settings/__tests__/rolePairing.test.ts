import { describe, expect, it } from "vitest";
import type { SettingsRole, SettingsUser } from "../../../api/settings";
import { recommendSystemRoleForOps } from "../rolePairing";

const roles: SettingsRole[] = [
  {
    role_key: "user",
    label: "User",
    kind: "custom",
    module_permissions: ["reports.view"],
    is_active: true,
    version: 1,
  },
  {
    role_key: "admin",
    label: "Admin",
    kind: "custom",
    module_permissions: ["users.manage", "settings.manage"],
    is_active: true,
    version: 1,
  },
  {
    role_key: "super_admin",
    label: "Super Admin",
    kind: "system",
    module_permissions: ["access.all_modules"],
    is_active: true,
    version: 1,
  },
];

function u(
  partial: Partial<SettingsUser> & Pick<SettingsUser, "id" | "email" | "role">,
): SettingsUser {
  return {
    display_name: partial.display_name || partial.email,
    operational_role: partial.operational_role ?? null,
    branch_ids: partial.branch_ids || [],
    is_active: partial.is_active ?? true,
    effective_module_permissions: partial.effective_module_permissions || [],
    ...partial,
  };
}

describe("recommendSystemRoleForOps", () => {
  it("recommends unique safe pairing from active users", () => {
    const res = recommendSystemRoleForOps({
      operationalRole: "designer",
      roles,
      users: [
        u({ id: "1", email: "a@x", role: "user", operational_role: "designer" }),
        u({ id: "2", email: "b@x", role: "user", operational_role: "designer" }),
      ],
    });
    expect(res).toEqual(
      expect.objectContaining({ kind: "recommend", systemRole: "user" }),
    );
  });

  it("never recommends super_admin or sensitive-permission roles", () => {
    const res = recommendSystemRoleForOps({
      operationalRole: "operator",
      roles,
      users: [
        u({ id: "1", email: "a@x", role: "super_admin", operational_role: "operator" }),
        u({ id: "2", email: "b@x", role: "admin", operational_role: "operator" }),
      ],
    });
    expect(res.kind).toBe("require_explicit");
  });

  it("requires explicit when no evidence", () => {
    const res = recommendSystemRoleForOps({
      operationalRole: "helper",
      roles,
      users: [],
    });
    expect(res.kind).toBe("require_explicit");
  });

  it("requires explicit when tied candidates", () => {
    const safeAdmin: SettingsRole = {
      role_key: "lead",
      label: "Lead",
      kind: "custom",
      module_permissions: ["reports.view"],
      is_active: true,
      version: 1,
    };
    const res = recommendSystemRoleForOps({
      operationalRole: "helper",
      roles: [...roles, safeAdmin],
      users: [
        u({ id: "1", email: "a@x", role: "user", operational_role: "helper" }),
        u({ id: "2", email: "b@x", role: "lead", operational_role: "helper" }),
      ],
    });
    expect(res.kind).toBe("require_explicit");
    if (res.kind === "require_explicit") {
      expect(res.candidates.sort()).toEqual(["lead", "user"]);
    }
  });
});
