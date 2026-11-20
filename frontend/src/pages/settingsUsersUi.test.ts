import { describe, expect, it } from "vitest";
import {
  activeBranchOptions,
  formatUserBranches,
  sameBranchSet,
  toggleBranchId,
} from "./settingsUsersUi";

describe("settingsUsersUi", () => {
  it("formatUserBranches shows Belum ditentukan when empty", () => {
    expect(formatUserBranches([])).toBe("Belum ditentukan");
    expect(formatUserBranches(null)).toBe("Belum ditentukan");
  });

  it("formatUserBranches joins code and name from catalog", () => {
    expect(
      formatUserBranches(["hq", "SMG"], [
        { code: "HQ", name: "Head Office" },
        { code: "SMG", name: "Semarang" },
      ]),
    ).toBe("HQ — Head Office, SMG — Semarang");
  });

  it("activeBranchOptions filters inactive", () => {
    expect(
      activeBranchOptions([
        { code: "HQ", name: "A", is_active: true },
        { code: "JOG", name: "B", is_active: false },
      ]).map((b) => b.code),
    ).toEqual(["HQ"]);
  });

  it("toggleBranchId adds and removes without dupes", () => {
    expect(toggleBranchId(["HQ"], "smg", true)).toEqual(["HQ", "SMG"]);
    expect(toggleBranchId(["HQ", "SMG"], "hq", false)).toEqual(["SMG"]);
    expect(toggleBranchId(["HQ"], "HQ", true)).toEqual(["HQ"]);
  });

  it("sameBranchSet ignores order/case", () => {
    expect(sameBranchSet(["hq", "SMG"], ["SMG", "HQ"])).toBe(true);
    expect(sameBranchSet(["HQ"], ["JOG"])).toBe(false);
  });
});
