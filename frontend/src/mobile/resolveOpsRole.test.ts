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

  it("honors override when provided", () => {
    const role = resolveOpsRoleFromUser(
      { role: "super_admin", operational_role: "driver" },
      "designer",
    );
    expect(role?.id).toBe("designer");
    expect(teamTaskRoleById("designer").label).toBe("Designer");
  });
});
