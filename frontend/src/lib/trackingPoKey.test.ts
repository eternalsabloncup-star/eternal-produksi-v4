import { describe, expect, it } from "vitest";
import { resolveTrackingPoKey } from "./trackingPoKey";

describe("resolveTrackingPoKey", () => {
  it("prefers accurateId over sourcePoId and display id", () => {
    expect(
      resolveTrackingPoKey({
        accurateId: "10001",
        sourcePoId: "src-x",
        id: "SO-1",
        poNo: "SO-1",
      }),
    ).toBe("10001");
  });

  it("falls back to sourcePoId then poNo", () => {
    expect(
      resolveTrackingPoKey({
        accurateId: null,
        sourcePoId: "src-9",
        id: "SO-9",
        poNo: "SO-9",
      }),
    ).toBe("src-9");
    expect(
      resolveTrackingPoKey({
        accurateId: "",
        sourcePoId: "",
        id: "SO-8",
        poNo: "SO-8",
      }),
    ).toBe("SO-8");
  });

  it("returns null when no identity", () => {
    expect(
      resolveTrackingPoKey({
        accurateId: null,
        sourcePoId: "",
        id: "",
        poNo: "",
      }),
    ).toBeNull();
  });
});
