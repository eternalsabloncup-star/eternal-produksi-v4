import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  generateAndCopyLiveTrackingLink,
  hasActiveTrackingLink,
  LiveTrackingCopyError,
} from "./liveTrackingCopy";

const listMock = vi.fn();
const generateMock = vi.fn();
const regenerateMock = vi.fn();

vi.mock("../api/tracking", () => ({
  listTrackingLinks: (...args: unknown[]) => listMock(...args),
  generateTrackingLink: (...args: unknown[]) => generateMock(...args),
  regenerateTrackingLink: (...args: unknown[]) => regenerateMock(...args),
}));

function activeRow(poKey: string) {
  return {
    id: `link-${poKey}`,
    accurate_po_id: poKey,
    po_no: `SO-${poKey}`,
    customer_name: "PT Acme",
    customer_slug: "pt-acme",
    po_slug: `po-${poKey}`,
    status: "active" as const,
    scopes: ["progress"],
    created_at: "2026-11-19T00:00:00Z",
    created_by: "ops@test",
    revoked_at: null,
    expires_at: null,
    access_count: 0,
    last_accessed_at: null,
  };
}

function genResponse(poKey: string) {
  const token = `tok-${poKey}-ABCDEFGH1234`;
  return {
    token,
    path: `/t/pt-acme/po-${poKey}/${token}`,
    link: activeRow(poKey),
    accurate_po_id: poKey,
    po_no: `SO-${poKey}`,
    customer_slug: "pt-acme",
    po_slug: `po-${poKey}`,
  };
}

describe("liveTrackingCopy — generate once", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    listMock.mockReset();
    generateMock.mockReset();
    regenerateMock.mockReset();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn(async () => undefined) },
    });
  });

  it("hasActiveTrackingLink detects active only", () => {
    expect(hasActiveTrackingLink([])).toBe(false);
    expect(hasActiveTrackingLink([{ ...activeRow("1"), status: "revoked" }])).toBe(
      false,
    );
    expect(hasActiveTrackingLink([activeRow("1")])).toBe(true);
  });

  it("1. generate first success → copy", async () => {
    listMock.mockResolvedValue([]);
    const gen = genResponse("10001");
    generateMock.mockResolvedValue(gen);

    const result = await generateAndCopyLiveTrackingLink(
      "10001",
      "http://127.0.0.1:5173",
    );

    expect(result.path).toBe(gen.path);
    expect(generateMock).toHaveBeenCalledWith("10001");
    expect(regenerateMock).not.toHaveBeenCalled();
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      `http://127.0.0.1:5173${gen.path}`,
    );
  });

  it("2. already active → already_created, no generate", async () => {
    listMock.mockResolvedValue([activeRow("10002")]);

    await expect(generateAndCopyLiveTrackingLink("10002")).rejects.toMatchObject({
      code: "already_created",
      linkCreated: true,
    });
    expect(generateMock).not.toHaveBeenCalled();
    expect(regenerateMock).not.toHaveBeenCalled();
  });

  it("3. clipboard fail after generate → linkCreated true", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: vi.fn(async () => {
          throw new Error("denied");
        }),
      },
    });
    listMock.mockResolvedValue([]);
    generateMock.mockResolvedValue(genResponse("10001"));

    await expect(generateAndCopyLiveTrackingLink("10001")).rejects.toMatchObject({
      code: "clipboard",
      linkCreated: true,
    });
    expect(generateMock).toHaveBeenCalledTimes(1);
  });

  it("4. generate API fail → no linkCreated", async () => {
    listMock.mockResolvedValue([]);
    generateMock.mockRejectedValue(new Error("Akses ditolak"));

    const err = await generateAndCopyLiveTrackingLink("10001").catch((e) => e);
    expect(err).toBeInstanceOf(LiveTrackingCopyError);
    expect(err.code).toBe("permission");
    expect(err.linkCreated).toBe(false);
  });

  it("5. two POs distinct generate", async () => {
    listMock.mockResolvedValue([]);
    generateMock.mockImplementation(async (po: string) => genResponse(po));

    const a = await generateAndCopyLiveTrackingLink("A1", "http://x");
    const b = await generateAndCopyLiveTrackingLink("B2", "http://x");
    expect(a.path).toContain("po-A1");
    expect(b.path).toContain("po-B2");
    expect(a.path).not.toBe(b.path);
  });

  it("6. concurrent same PO → busy", async () => {
    listMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve([]), 40);
        }),
    );
    generateMock.mockResolvedValue(genResponse("10001"));

    const first = generateAndCopyLiveTrackingLink("10001");
    await expect(generateAndCopyLiveTrackingLink("10001")).rejects.toMatchObject({
      code: "busy",
    });
    await first;
  });

  it("7. backend 409 already exists → already_created", async () => {
    listMock.mockResolvedValue([]);
    generateMock.mockRejectedValue(new Error("Link aktif sudah ada untuk PO ini."));

    await expect(generateAndCopyLiveTrackingLink("10001")).rejects.toMatchObject({
      code: "already_created",
      linkCreated: true,
    });
  });
});
