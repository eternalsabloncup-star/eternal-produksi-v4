import { describe, expect, it } from "vitest";
import source from "./TrackingAdminPage.tsx?raw";

/** Guard: /app/tracking admin feedback aligns with Inbox Live Tracking overlay. */
describe("TrackingAdminPage action feedback contract", () => {
  it("uses ConfirmationSuccessOverlay for success and error", () => {
    expect(source).toMatch(/ConfirmationSuccessOverlay/);
    expect(source).toMatch(/tone:\s*"success"\s*\|\s*"error"/);
    expect(source).toMatch(/if \(busy\) return/);
  });

  it("exposes busy state to primary actions", () => {
    expect(source).toMatch(/aria-busy=\{busy\}/);
    expect(source).toMatch(/disabled=\{busy/);
  });
});
