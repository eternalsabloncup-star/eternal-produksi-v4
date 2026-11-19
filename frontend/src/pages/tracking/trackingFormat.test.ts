import { describe, expect, it } from "vitest";
import { MILESTONE_STATE_LABEL, formatQty, formatTs } from "../../api/tracking";

describe("tracking format helpers", () => {
  it("formats qty with id-ID and dash for null", () => {
    expect(formatQty(null)).toBe("—");
    expect(formatQty(1250)).toMatch(/1\.250/);
  });

  it("formats timestamp in Asia/Jakarta or dash", () => {
    expect(formatTs(null)).toBe("—");
    expect(formatTs("2026-11-19T05:00:00+00:00")).toMatch(/2026/);
  });

  it("exposes all milestone state labels", () => {
    expect(Object.keys(MILESTONE_STATE_LABEL)).toHaveLength(6);
  });
});
