import { describe, expect, it } from "vitest";
import {
  allocCardNeedsItemIdentity,
  pwaBodyShouldRepeatDocumentRef,
  pwaItemSummaryShowsRemaining,
} from "./teamTaskDedupe";

describe("teamTaskDedupe canonical placement", () => {
  it("keeps document refs in body only when not on PWA worksheet", () => {
    const task = {
      ref: { sj_no: "SJ-1", accurate_delivery_note_id: "dn-1" },
    };
    expect(pwaBodyShouldRepeatDocumentRef(true, task)).toBe(false);
    expect(pwaBodyShouldRepeatDocumentRef(false, task)).toBe(true);
  });

  it("requires alloc item identity only for multi-line sheets", () => {
    expect(allocCardNeedsItemIdentity(1)).toBe(false);
    expect(allocCardNeedsItemIdentity(2)).toBe(true);
  });

  it("hides Sisa from item summary on PWA (footer owns it)", () => {
    expect(pwaItemSummaryShowsRemaining(true)).toBe(false);
    expect(pwaItemSummaryShowsRemaining(false)).toBe(true);
  });
});
