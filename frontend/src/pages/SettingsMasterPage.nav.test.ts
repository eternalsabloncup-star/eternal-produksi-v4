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

describe("Settings Center navigation", () => {
  it("exposes exactly 11 submenu items with required icons", () => {
    expect(SETTINGS_NAV_ITEMS).toHaveLength(11);
    expect(SETTINGS_NAV_SECTIONS).toHaveLength(3);

    const expected: Array<{ id: string; icon: string }> = [
      { id: "overview", icon: "layout-dashboard" },
      { id: "users", icon: "users" },
      { id: "roles", icon: "shield-check" },
      { id: "task-types", icon: "clipboard-list" },
      { id: "branches", icon: "building-2" },
      { id: "machines", icon: "cog" },
      { id: "shifts", icon: "clock-3" },
      { id: "products", icon: "package" },
      { id: "workflow", icon: "workflow" },
      { id: "accurate", icon: "plug-zap" },
      { id: "audit", icon: "history" },
    ];

    for (const row of expected) {
      const item = SETTINGS_NAV_ITEMS.find((x) => x.id === row.id);
      expect(item, row.id).toBeTruthy();
      expect(item?.icon).toBe(row.icon);
    }

    // Cabang / Mesin / Shift must be separate (no combined org tab).
    const ids = SETTINGS_NAV_ITEMS.map((x) => x.id);
    expect(ids).not.toContain("org");
    expect(SETTINGS_NAV_ITEMS.filter((x) => x.id === "branches")).toHaveLength(1);
    expect(SETTINGS_NAV_ITEMS.filter((x) => x.id === "machines")).toHaveLength(1);
    expect(SETTINGS_NAV_ITEMS.filter((x) => x.id === "shifts")).toHaveLength(1);
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
