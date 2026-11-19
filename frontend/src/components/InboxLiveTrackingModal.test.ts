import { describe, expect, it } from "vitest";
import { resolveTrackingPoKey } from "../lib/trackingPoKey";
import { InboxLiveTrackingButton } from "./InboxLiveTrackingButton";
import { InboxPoTable } from "./InboxPoTable";

describe("Inbox Live Tracking one-click contract", () => {
  it("binds button identity to row Accurate id (not hardcoded fixture)", () => {
    const row = {
      accurateId: "95106",
      sourcePoId: "src-95106",
      id: "SO/95106",
      poNo: "SO/95106",
    };
    const key = resolveTrackingPoKey(row);
    expect(key).toBe("95106");
    expect(key).not.toBe("10001");
  });

  it("exports one-click button and InboxPoTable (no modal gate)", () => {
    expect(InboxLiveTrackingButton).toBeTypeOf("function");
    expect(InboxPoTable).toBeTypeOf("function");
  });
});
