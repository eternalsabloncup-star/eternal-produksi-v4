import { describe, expect, it } from "vitest";
import { APP_NAV, navByPath } from "./nav";

describe("Live Tracking sidebar placement", () => {
  it("does not list Live Tracking in APP_NAV sidebar", () => {
    expect(APP_NAV.some((n) => n.id === "tracking")).toBe(false);
    expect(APP_NAV.some((n) => n.path === "/app/tracking")).toBe(false);
    expect(APP_NAV.some((n) => /live tracking/i.test(n.label))).toBe(false);
  });

  it("keeps header metadata for /app/tracking compatibility route", () => {
    const meta = navByPath("/app/tracking");
    expect(meta.id).toBe("tracking");
    expect(meta.label).toBe("Live Tracking");
  });
});
