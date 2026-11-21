import { describe, expect, it } from "vitest";
import {
  USER_NAME_UNAVAILABLE,
  actorDisplayLabel,
  userDisplayName,
} from "./userDisplayName";

describe("userDisplayName", () => {
  it("returns Master display_name", () => {
    expect(userDisplayName("Budi Santoso")).toBe("Budi Santoso");
  });

  it("does not fall back to email", () => {
    expect(userDisplayName("")).toBe(USER_NAME_UNAVAILABLE);
    expect(userDisplayName(null)).toBe(USER_NAME_UNAVAILABLE);
    expect(userDisplayName("   ")).toBe(USER_NAME_UNAVAILABLE);
  });

  it("prefers actor_display_name enrichment", () => {
    expect(
      actorDisplayLabel({
        actor_display_name: "Owner Simulasi",
        display_name: "Other",
      }),
    ).toBe("Owner Simulasi");
  });
});
