import { describe, expect, it } from "vitest";
import {
  isOperationalRoleId,
  resolveOpsRoleFromUser,
  teamTaskRoleById,
} from "./resolveOpsRole";

describe("resolveOpsRoleFromUser", () => {
  it("maps Master operational_role for all six roles", () => {
    const roles = [
      "production-head",
      "designer",
      "screener",
      "helper",
      "operator",
      "driver",
    ] as const;
    for (const id of roles) {
      const role = resolveOpsRoleFromUser({
        role: "user",
        operational_role: id,
      });
      expect(role?.id).toBe(id);
      expect(isOperationalRoleId(id)).toBe(true);
    }
  });

  it("fail-closed when operational_role missing", () => {
    expect(
      resolveOpsRoleFromUser({ role: "admin", operational_role: null }),
    ).toBeNull();
    expect(
      resolveOpsRoleFromUser({ role: "admin", operational_role: "unknown" }),
    ).toBeNull();
  });

  it("does not impersonate ops role for Super Admin without operational_role", () => {
    expect(
      resolveOpsRoleFromUser({
        role: "super_admin",
        operational_role: null,
      }),
    ).toBeNull();
  });

  it("uses account operational_role even for Super Admin (no override)", () => {
    const role = resolveOpsRoleFromUser({
      role: "super_admin",
      operational_role: "driver",
    });
    expect(role?.id).toBe("driver");
    expect(teamTaskRoleById("designer").label).toBe("Designer");
  });
});
