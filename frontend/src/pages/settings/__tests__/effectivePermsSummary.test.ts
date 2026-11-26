import { describe, expect, it } from "vitest";
import type { SettingsRole, SettingsUser } from "../../../api/settings";
import { buildEffectivePermsView } from "../effectivePermsSummary";

const roles: SettingsRole[] = [
  {
    role_key: "admin",
    label: "Admin",
    kind: "system",
    module_permissions: ["users.manage", "reports.view"],
    is_active: true,
    version: 1,
  },
  {
    role_key: "user",
    label: "User",
    kind: "system",
    module_permissions: ["reports.view"],
    is_active: true,
    version: 1,
  },
];

const editingUser: SettingsUser = {
  id: "u1",
  email: "a@b.c",
  display_name: "A",
  role: "admin",
  operational_role: "helper",
  is_active: true,
  branch_ids: ["YG"],
  effective_module_permissions: ["users.manage", "reports.view", "legacy.snap"],
};

describe("buildEffectivePermsView", () => {
  it("preview follows selected role catalog + policy, not old snapshot", () => {
    const view = buildEffectivePermsView({
      editingUser,
      selectedRoleKey: "user",
      roles,
      policyGrants: {
        user: [
          {
            permission: "operations.tasks.view_all",
            source: "holding_auto",
            revocable: false,
          },
        ],
      },
    });
    expect(view.roleChanged).toBe(true);
    expect(view.previewFollowsSelectedRole).toBe(true);
    expect(view.previewAfterSave).toEqual(
      ["operations.tasks.view_all", "reports.view"].sort(),
    );
    expect(view.previewAfterSave).not.toContain("legacy.snap");
    expect(view.previewAfterSave).not.toContain("users.manage");
    expect(view.currentSaved).toEqual([]);
  });

  it("when role unchanged, preview uses selected role + policy (not only snapshot)", () => {
    const view = buildEffectivePermsView({
      editingUser,
      selectedRoleKey: "admin",
      roles,
      policyGrants: {
        admin: [
          {
            permission: "operations.tasks.view_all",
            source: "holding_auto",
            revocable: false,
          },
        ],
      },
    });
    expect(view.roleChanged).toBe(false);
    expect(view.previewAfterSave).toContain("users.manage");
    expect(view.previewAfterSave).toContain("operations.tasks.view_all");
    expect(view.previewAfterSave).not.toContain("legacy.snap");
    expect(view.currentSaved).toEqual(
      ["legacy.snap", "reports.view", "users.manage"].sort(),
    );
  });
});
