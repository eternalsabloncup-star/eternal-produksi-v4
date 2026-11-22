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

describe("Settings Center V2 navigation", () => {
  it("exposes five groups and separate operational leaves", () => {
    expect(SETTINGS_NAV_SECTIONS).toHaveLength(5);
    expect(SETTINGS_NAV_ITEMS.length).toBeGreaterThanOrEqual(14);

    const ids = SETTINGS_NAV_ITEMS.map((x) => x.id);
    expect(ids).toContain("overview");
    expect(ids).toContain("users");
    expect(ids).toContain("roles-catalog");
    expect(ids).toContain("module-perms");
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

    // Cabang / Mesin / Shift remain separate (no combined org tab).
    expect(ids).not.toContain("org");
    expect(ids).not.toContain("roles"); // legacy combined tab removed
  });

  it("uses Indonesian operational labels for key menus", () => {
    const byId = Object.fromEntries(SETTINGS_NAV_ITEMS.map((i) => [i.id, i.label]));
    expect(byId["module-perms"]).toBe("Hak Akses Menu");
    expect(byId["ops-grants"]).toBe("Izin Pekerjaan");
    expect(byId["task-assign"]).toBe("Pembagian Tugas");
    expect(byId["shifts"]).toBe("Jam Kerja");
    expect(byId["audit"]).toBe("Catatan Perubahan");
    expect(byId["workflow"]).toBe("Aturan Alur Kerja");
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
});
