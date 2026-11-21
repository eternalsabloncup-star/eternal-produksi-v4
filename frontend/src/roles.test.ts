import { describe, expect, it } from "vitest";
import {
  hasViewAllTasks,
  isHoldingRole,
  PERM_VIEW_ALL_TASKS,
  ROLE_HOLDING,
} from "./roles";

describe("hasViewAllTasks / Holding", () => {
  it("auto-enables for Holding role_key without listed grant", () => {
    expect(isHoldingRole(ROLE_HOLDING)).toBe(true);
    expect(
      hasViewAllTasks({ role: ROLE_HOLDING, permissions: [] }),
    ).toBe(true);
  });

  it("honors explicit grant for non-Holding", () => {
    expect(
      hasViewAllTasks({
        role: "user",
        permissions: [PERM_VIEW_ALL_TASKS],
      }),
    ).toBe(true);
    expect(hasViewAllTasks({ role: "user", permissions: [] })).toBe(false);
  });

  it("does not treat Super Admin as Holding", () => {
    expect(isHoldingRole("super_admin")).toBe(false);
    expect(
      hasViewAllTasks({ role: "super_admin", permissions: [] }),
    ).toBe(true);
  });
});
