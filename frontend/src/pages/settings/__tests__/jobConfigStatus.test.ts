import { describe, expect, it } from "vitest";
import type { MasterTaskType, RoleActionGrant } from "../../../api/operational";
import type { SettingsBranch, SettingsUser } from "../../../api/settings";
import { computeJobConfigStatus } from "../jobConfigStatus";

const task = {
  task_type: "design",
  label: "Design",
  assigned_role: "designer",
  actions: ["view", "complete"],
} as unknown as MasterTaskType;

const grant = {
  role: "designer",
  task_type: "design",
  actions: ["complete"],
  is_default_mapping: false,
} as unknown as RoleActionGrant;

const userOk: SettingsUser = {
  id: "1",
  email: "d@b.c",
  display_name: "D",
  role: "user",
  operational_role: "designer",
  is_active: true,
  branch_ids: ["YG"],
  effective_module_permissions: [],
};

const branches: SettingsBranch[] = [
  { code: "YG", name: "Yogyakarta", is_active: true },
];

const loadOk = {
  usersLoad: "SUCCESS" as const,
  grantsLoad: "SUCCESS" as const,
  branchesLoad: "SUCCESS" as const,
};

describe("computeJobConfigStatus load + branch gates", () => {
  it("does not return SIAP when users load failed", () => {
    expect(
      computeJobConfigStatus({
        task,
        grants: [grant],
        users: [userOk],
        branches,
        load: { ...loadOk, usersLoad: "LOAD_FAILED" },
      }),
    ).toBe("BELUM_TERVERIFIKASI");
  });

  it("does not return SIAP when grants load failed", () => {
    expect(
      computeJobConfigStatus({
        task,
        grants: [grant],
        users: [userOk],
        branches,
        load: { ...loadOk, grantsLoad: "LOAD_FAILED" },
      }),
    ).toBe("BELUM_TERVERIFIKASI");
  });

  it("does not return SIAP when branches load failed", () => {
    expect(
      computeJobConfigStatus({
        task,
        grants: [grant],
        users: [userOk],
        branches,
        load: { ...loadOk, branchesLoad: "FORBIDDEN" },
      }),
    ).toBe("BELUM_TERVERIFIKASI");
  });

  it("returns BELUM_TERVERIFIKASI when active branches exist but user has none", () => {
    expect(
      computeJobConfigStatus({
        task,
        grants: [grant],
        users: [{ ...userOk, branch_ids: [] }],
        branches,
        load: loadOk,
      }),
    ).toBe("BELUM_TERVERIFIKASI");
  });

  it("returns SIAP only when loads ok, grants ok, user active, and branch covered", () => {
    expect(
      computeJobConfigStatus({
        task,
        grants: [grant],
        users: [userOk],
        branches,
        load: loadOk,
      }),
    ).toBe("SIAP");
  });
});
