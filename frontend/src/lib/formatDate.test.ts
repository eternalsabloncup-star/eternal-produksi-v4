import { describe, expect, it } from "vitest";
import { formatDateDisplay, isIsoDateString } from "./formatDate";

describe("formatDateDisplay", () => {
  it("formats YYYY-MM-DD to DD/MM/YYYY", () => {
    expect(formatDateDisplay("2026-11-18")).toBe("18/11/2026");
  });

  it("formats Accurate DD/MM/YYYY without inventing values", () => {
    expect(formatDateDisplay("18/11/2026")).toBe("18/11/2026");
    expect(formatDateDisplay("8/3/2026")).toBe("08/03/2026");
  });

  it("formats datetime variants proven from calendar sources", () => {
    expect(formatDateDisplay("2026-11-18T00:00:00Z")).toBe("18/11/2026");
    expect(formatDateDisplay("18/11/2026 14:30")).toBe("18/11/2026");
    expect(formatDateDisplay("18/11/2026 14:30:01")).toBe("18/11/2026");
  });

  it("rejects empty and invalid", () => {
    expect(formatDateDisplay("")).toBe("—");
    expect(formatDateDisplay(null)).toBe("—");
    expect(formatDateDisplay(undefined)).toBe("—");
    expect(formatDateDisplay("not-a-date")).toBe("—");
    expect(formatDateDisplay("32/11/2026")).toBe("—");
    expect(formatDateDisplay("2026-13-01")).toBe("—");
  });

  it("does not parse tanggal from nomor PO segments", () => {
    expect(formatDateDisplay("PO/18/11/2026/000010")).toBe("—");
    expect(formatDateDisplay("18/11/2026/000010")).toBe("—");
  });

  it("allows custom empty label", () => {
    expect(formatDateDisplay("", "")).toBe("");
  });
});

describe("isIsoDateString", () => {
  it("detects ISO calendar prefix", () => {
    expect(isIsoDateString("2026-11-18")).toBe(true);
    expect(isIsoDateString("18/11/2026")).toBe(false);
  });
});
