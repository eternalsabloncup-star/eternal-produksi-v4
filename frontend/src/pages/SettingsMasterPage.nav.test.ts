import { describe, expect, it } from "vitest";
import {
  SETTINGS_NAV_ITEMS,
  SETTINGS_NAV_SECTIONS,
  assignmentsFromTaskTypes,
  grantKey,
  serializeGrants,
  serializePerms,
} from "./SettingsMasterPage";
import type { MasterTaskType, RoleActionGrant } from "../api/operational";
import { computeJobConfigStatus } from "./settings/jobConfigStatus";
import { classifyListResult } from "./settings/loadState";

describe("Settings Center V2 navigation", () => {
  it("exposes five groups including jobs-board hub", () => {
    expect(SETTINGS_NAV_SECTIONS).toHaveLength(5);
    expect(SETTINGS_NAV_SECTIONS.map((s) => s.id)).toEqual([
      "access",
      "jobs",
      "org",
      "products",
      "system",
    ]);
    expect(SETTINGS_NAV_ITEMS.length).toBeGreaterThanOrEqual(15);

    const ids = SETTINGS_NAV_ITEMS.map((x) => x.id);
    expect(ids).toContain("overview");
    expect(ids).toContain("users");
    expect(ids).toContain("roles-catalog");
    expect(ids).toContain("module-perms");
    expect(ids).toContain("jobs-board");
    expect(ids).toContain("ops-grants");
    expect(ids).toContain("task-types");
    expect(ids).toContain("task-assign");
    expect(ids).toContain("branches");
    expect(ids).toContain("machines");
    expect(ids).toContain("shifts");
    expect(ids).toContain("products");
    expect(ids).toContain("product-screen");
    expect(ids).toContain("accurate");
    expect(ids).toContain("workflow");
    expect(ids).toContain("audit");

    // Leaf tabs preserved; no legacy combined "roles" tab.
    expect(ids).not.toContain("roles");
  });

  it("uses Indonesian labels for key menus", () => {
    const byId = Object.fromEntries(SETTINGS_NAV_ITEMS.map((i) => [i.id, i.label]));
    expect(byId["module-perms"]).toBe("Hak Akses Menu");
    expect(byId["ops-grants"]).toBe("Izin Pekerjaan");
    expect(byId["task-assign"]).toBe("Penanggung Jawab");
    expect(byId["jobs-board"]).toBe("Papan Konfigurasi");
    expect(byId["shifts"]).toBe("Shift / Jam Kerja");
    expect(byId["audit"]).toBe("Riwayat Perubahan");
    expect(byId["workflow"]).toBe("Aturan Workflow");
  });

  it("detects draft permission and grant diffs without calling APIs", () => {
    expect(serializePerms(["b", "a"])).toBe(serializePerms(["a", "b"]));
    expect(serializePerms(["a"])).not.toBe(serializePerms(["a", "b"]));

    const a: RoleActionGrant[] = [
      {
        role: "helper",
        task_type: "t1",
        actions: ["view", "input"],
        is_default_mapping: false,
      },
    ];
    const b: RoleActionGrant[] = [
      {
        role: "helper",
        task_type: "t1",
        actions: ["input", "view"],
        is_default_mapping: false,
      },
    ];
    const c: RoleActionGrant[] = [
      {
        role: "helper",
        task_type: "t1",
        actions: ["view"],
        is_default_mapping: false,
      },
    ];
    expect(serializeGrants(a)).toBe(serializeGrants(b));
    expect(serializeGrants(a)).not.toBe(serializeGrants(c));
    expect(grantKey("helper", "t1")).toBe("helper::t1");
  });

  it("builds assignment draft map from task types", () => {
    const items = [
      { task_type: "a", assigned_role: "helper" },
      { task_type: "b", assigned_role: "operator" },
    ] as MasterTaskType[];
    expect(assignmentsFromTaskTypes(items)).toEqual({
      a: "helper",
      b: "operator",
    });
  });

  it("computes job config status from real data without restoring grants", () => {
    const task = {
      task_type: "design",
      label: "Design",
      assigned_role: "designer",
      actions: ["view", "complete"],
    } as unknown as MasterTaskType;
    expect(
      computeJobConfigStatus({
        task: { ...task, assigned_role: "" as MasterTaskType["assigned_role"] },
        grants: [],
        users: [],
      }),
    ).toBe("BELUM_ADA_PENANGGUNG_JAWAB");
    expect(computeJobConfigStatus({ task, grants: [], users: [] })).toBe(
      "IZIN_BELUM_LENGKAP",
    );
    const grant = {
      role: "designer",
      task_type: "design",
      actions: ["complete"],
      is_default_mapping: false,
    } as unknown as RoleActionGrant;
    expect(computeJobConfigStatus({ task, grants: [grant], users: [] })).toBe(
      "BELUM_ADA_PENGGUNA_AKTIF",
    );
    expect(
      computeJobConfigStatus({
        task,
        grants: [grant],
        users: [
          {
            id: "1",
            email: "a@b.c",
            display_name: "A",
            role: "user",
            operational_role: "designer",
            is_active: true,
            branch_ids: [],
            effective_module_permissions: [],
          },
        ],
      }),
    ).toBe("SIAP");
  });

  it("classifies empty vs failed load states distinctly", () => {
    expect(classifyListResult(true, 0)).toBe("DATA_EMPTY");
    expect(classifyListResult(false, 0)).toBe("LOAD_FAILED");
    expect(classifyListResult(false, 0, true)).toBe("FORBIDDEN");
    expect(classifyListResult(true, 3)).toBe("SUCCESS");
  });
});
