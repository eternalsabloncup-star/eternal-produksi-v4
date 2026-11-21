import { describe, expect, it } from "vitest";
import { formatPwaBranchLabel } from "./branchDisplay";

describe("formatPwaBranchLabel", () => {
  it("shows Master name without Cabang {id} prefix", () => {
    expect(
      formatPwaBranchLabel({
        branchId: "50",
        branchName: "Yogyakarta",
        branchNameVerified: true,
      }),
    ).toEqual({ label: "Yogyakarta", missingMapping: false });
  });

  it("uses neutral fallback when Master name missing", () => {
    expect(
      formatPwaBranchLabel({
        branchId: "50",
        branchName: null,
        branchNameVerified: false,
      }),
    ).toEqual({
      label: "Cabang (nama Master belum terpetakan)",
      missingMapping: true,
    });
  });

  it("handles absent branch id", () => {
    expect(
      formatPwaBranchLabel({ branchId: null, branchName: null }),
    ).toEqual({ label: "Cabang belum tersedia", missingMapping: false });
  });
});
