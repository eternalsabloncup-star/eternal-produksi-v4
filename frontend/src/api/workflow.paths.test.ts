import { describe, expect, it } from "vitest";
import { customerApprovalPath } from "./workflow";

describe("workflow client helpers", () => {
  it("builds customer approval path with encoded token", () => {
    expect(customerApprovalPath("abc/def")).toBe("/approval/abc%2Fdef");
    expect(customerApprovalPath("tok-1")).toBe("/approval/tok-1");
  });
});
