import { describe, expect, it } from "vitest";
import type { RoleActionGrant } from "../../../api/operational";
import {
  applyColumnSelectAll,
  baselineModulePerms,
  columnSelectState,
  countChangedGrants,
  modulePermsAreDirty,
  serializePerms,
} from "../settingsDraftUtils";

describe("baselineModulePerms / discard safety", () => {
  it("keeps non-revocable policy grants in baseline", () => {
    const base = baselineModulePerms(["reports.view"], [
      {
        permission: "operations.tasks.view_all",
        source: "holding_auto",
        revocable: false,
      },
      {
        permission: "users.manage",
        source: "manual",
        revocable: true,
      },
    ]);
    expect(base).toContain("reports.view");
    expect(base).toContain("operations.tasks.view_all");
    expect(base).not.toContain("users.manage");
  });

  it("dirty detection ignores already-effective policy grants", () => {
    const rolePerms = ["reports.view"];
    const policy = [
      {
        permission: "operations.tasks.view_all",
        source: "holding_auto",
        revocable: false,
      },
    ];
    const draft = baselineModulePerms(rolePerms, policy);
    expect(modulePermsAreDirty(draft, rolePerms, policy)).toBe(false);
    expect(
      modulePermsAreDirty([...draft, "users.manage"], rolePerms, policy),
    ).toBe(true);
  });

  it("serializePerms is order-insensitive", () => {
    expect(serializePerms(["b", "a"])).toBe(serializePerms(["a", "b"]));
  });
});

describe("column select-all", () => {
  const rows = [
    { role: "helper" as const, task_type: "t1" },
    { role: "helper" as const, task_type: "t2" },
  ];

  it("reports none / partial / all", () => {
    expect(columnSelectState([{ actions: [] }, { actions: [] }], "view")).toBe("none");
    expect(
      columnSelectState([{ actions: ["view"] }, { actions: [] }], "view"),
    ).toBe("partial");
    expect(
      columnSelectState([{ actions: ["view"] }, { actions: ["view"] }], "view"),
    ).toBe("all");
  });

  it("applies only to provided matrix rows and stays draft-local", () => {
    const draft: RoleActionGrant[] = [
      {
        role: "helper",
        task_type: "t1",
        actions: ["input"],
        is_default_mapping: false,
      },
      {
        role: "operator",
        task_type: "other",
        actions: ["view"],
        is_default_mapping: false,
      },
    ];
    const next = applyColumnSelectAll(draft, rows, "view", true);
    const t1 = next.find((g) => g.task_type === "t1");
    const t2 = next.find((g) => g.task_type === "t2");
    const other = next.find((g) => g.task_type === "other");
    expect(t1?.actions).toEqual(expect.arrayContaining(["input", "view"]));
    expect(t2?.actions).toEqual(["view"]);
    expect(other?.actions).toEqual(["view"]);
  });

  it("uncheck does not create rows outside filter set", () => {
    const draft: RoleActionGrant[] = [
      {
        role: "helper",
        task_type: "t1",
        actions: ["view", "input"],
        is_default_mapping: false,
      },
    ];
    const next = applyColumnSelectAll(
      draft,
      [{ role: "helper", task_type: "t1" }],
      "view",
      false,
    );
    expect(next.find((g) => g.task_type === "t1")?.actions).toEqual(["input"]);
    expect(next.find((g) => g.task_type === "ghost")).toBeUndefined();
  });

  it("counts changed grants for sticky save", () => {
    const server: RoleActionGrant[] = [
      {
        role: "helper",
        task_type: "t1",
        actions: ["view"],
        is_default_mapping: false,
      },
    ];
    const draft: RoleActionGrant[] = [
      {
        role: "helper",
        task_type: "t1",
        actions: ["view", "input"],
        is_default_mapping: false,
      },
    ];
    expect(countChangedGrants(draft, server)).toBe(1);
    expect(countChangedGrants(server, server)).toBe(0);
  });
});
