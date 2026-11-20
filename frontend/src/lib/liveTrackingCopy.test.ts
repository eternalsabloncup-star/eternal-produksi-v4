import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  copyActiveLiveTrackingLink,
  createOrCopyLiveTrackingLink,
  generateAndCopyLiveTrackingLink,
  hasActiveTrackingLink,
  LiveTrackingCopyError,
  recoverAndCopyLiveTrackingLink,
} from "./liveTrackingCopy";
import { TrackingApiError } from "../api/tracking";

const listMock = vi.fn();
const generateMock = vi.fn();
const regenerateMock = vi.fn();
const activeUrlMock = vi.fn();

vi.mock("../api/tracking", async () => {
  const actual = await vi.importActual<typeof import("../api/tracking")>(
    "../api/tracking",
  );
  return {
    ...actual,
    listTrackingLinks: (...args: unknown[]) => listMock(...args),
    generateTrackingLink: (...args: unknown[]) => generateMock(...args),
    regenerateTrackingLink: (...args: unknown[]) => regenerateMock(...args),
    fetchActiveTrackingUrl: (...args: unknown[]) => activeUrlMock(...args),
  };
});

function activeRow(poKey: string, recoverable = true) {
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
    url_recoverable: recoverable,
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

describe("liveTrackingCopy — generate + repeated copy", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    listMock.mockReset();
    generateMock.mockReset();
    regenerateMock.mockReset();
    activeUrlMock.mockReset();
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
    activeUrlMock.mockRejectedValue(
      new TrackingApiError(404, "Tidak ada link", "x"),
    );
    const gen = genResponse("10001");
    generateMock.mockResolvedValue(gen);

    const result = await createOrCopyLiveTrackingLink(
      "10001",
      "http://127.0.0.1:5173",
    );

    expect(result.path).toBe(gen.path);
    expect(result.mode).toBe("generate");
    expect(generateMock).toHaveBeenCalledWith("10001");
    expect(regenerateMock).not.toHaveBeenCalled();
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      `http://127.0.0.1:5173${gen.path}`,
    );
  });

  it("2. second copy → reveal only, no POST generate", async () => {
    const path = "/t/pt-acme/po-10002/TOKENCOPY2XXXXXXXXXX";
    activeUrlMock.mockResolvedValue({
      status: "ok",
      path,
      link_id: "link-10002",
      accurate_po_id: "10002",
      customer_slug: "pt-acme",
      po_slug: "po-10002",
      url_recoverable: true,
    });

    const first = await createOrCopyLiveTrackingLink("10002", "http://x");
    const second = await copyActiveLiveTrackingLink("10002", "http://x");

    expect(first.mode).toBe("copy");
    expect(second.mode).toBe("copy");
    expect(generateMock).not.toHaveBeenCalled();
    expect(regenerateMock).not.toHaveBeenCalled();
    expect(activeUrlMock).toHaveBeenCalledTimes(2);
    expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith(`http://x${path}`);
  });

  it("3. clipboard fail → no success path (error clipboard)", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: vi.fn(async () => {
          throw new Error("denied");
        }),
      },
    });
    activeUrlMock.mockRejectedValue(
      new TrackingApiError(404, "Tidak ada link", "x"),
    );
    generateMock.mockResolvedValue(genResponse("10001"));

    await expect(createOrCopyLiveTrackingLink("10001")).rejects.toMatchObject({
      code: "clipboard",
      linkCreated: true,
    });
    expect(generateMock).toHaveBeenCalledTimes(1);
  });

  it("4. generate API fail → no linkCreated", async () => {
    activeUrlMock.mockRejectedValue(
      new TrackingApiError(404, "Tidak ada link", "x"),
    );
    generateMock.mockRejectedValue(new Error("Akses ditolak"));

    const err = await createOrCopyLiveTrackingLink("10001").catch((e) => e);
    expect(err).toBeInstanceOf(LiveTrackingCopyError);
    expect(err.code).toBe("permission");
    expect(err.linkCreated).toBe(false);
  });

  it("5. concurrent same PO → busy", async () => {
    activeUrlMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(
            () =>
              resolve({
                status: "ok",
                path: "/t/pt-acme/po-10001/TOK",
                link_id: "l",
                accurate_po_id: "10001",
                customer_slug: "pt-acme",
                po_slug: "po-10001",
                url_recoverable: true,
              }),
            40,
          );
        }),
    );

    const first = createOrCopyLiveTrackingLink("10001");
    await expect(createOrCopyLiveTrackingLink("10001")).rejects.toMatchObject({
      code: "busy",
    });
    await first;
  });

  it("6. legacy unrecoverable → no auto regenerate", async () => {
    activeUrlMock.mockRejectedValue(
      new TrackingApiError(
        409,
        {
          code: "legacy_unrecoverable",
          message: "Link hash-only",
          link_id: "legacy-1",
          requires_explicit_recovery: true,
        },
        "x",
      ),
    );

    await expect(createOrCopyLiveTrackingLink("10001")).rejects.toMatchObject({
      code: "legacy_unrecoverable",
    });
    expect(generateMock).not.toHaveBeenCalled();
    expect(regenerateMock).not.toHaveBeenCalled();
  });

  it("7. recover only with confirmed true", async () => {
    await expect(
      recoverAndCopyLiveTrackingLink("10001", { confirmed: false }),
    ).rejects.toMatchObject({ code: "legacy_unrecoverable" });
    expect(regenerateMock).not.toHaveBeenCalled();

    regenerateMock.mockResolvedValue(genResponse("10001"));
    const out = await recoverAndCopyLiveTrackingLink(
      "10001",
      { confirmed: true },
      "http://x",
    );
    expect(out.mode).toBe("recover");
    expect(regenerateMock).toHaveBeenCalledTimes(1);
  });

  it("8. generateAndCopy still works for absent PO", async () => {
    listMock.mockResolvedValue([]);
    generateMock.mockResolvedValue(genResponse("10001"));
    const out = await generateAndCopyLiveTrackingLink("10001", "http://x");
    expect(out.mode).toBe("generate");
  });

  it("9. 403 permission on reveal", async () => {
    activeUrlMock.mockRejectedValue(
      new TrackingApiError(403, "Akses ditolak", "x"),
    );
    await expect(copyActiveLiveTrackingLink("10001")).rejects.toMatchObject({
      code: "permission",
    });
  });
});
