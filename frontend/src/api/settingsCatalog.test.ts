import { describe, expect, it } from "vitest";
import { SettingsApiError } from "./settings";

describe("SettingsApiError", () => {
  it("carries HTTP status for 403/409 handling", () => {
    const err = new SettingsApiError("Konflik versi", 409);
    expect(err.status).toBe(409);
    expect(err.message).toContain("Konflik");
  });
});
