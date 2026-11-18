import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiPoSource } from "./ApiPoSource";
import {
  buildInboxApiListParams,
  inboxFilterEnabled,
  INBOX_API_SUPPORTED_FILTERS,
} from "./inboxApiFilters";

describe("inbox API filter capabilities", () => {
  it("marks only q + urgent as supported in API mode", () => {
    expect(INBOX_API_SUPPORTED_FILTERS).toEqual(["q", "urgent"]);
    expect(inboxFilterEnabled("api", "q")).toBe(true);
    expect(inboxFilterEnabled("api", "urgent")).toBe(true);
    expect(inboxFilterEnabled("api", "branch")).toBe(false);
    expect(inboxFilterEnabled("api", "status")).toBe(false);
    expect(inboxFilterEnabled("api", "machine")).toBe(false);
  });

  it("keeps all filters enabled in DEMO mode", () => {
    for (const key of ["q", "urgent", "branch", "status", "machine"] as const) {
      expect(inboxFilterEnabled("demo", key)).toBe(true);
    }
  });

  it("buildInboxApiListParams applies supported filters and ignores unsupported ones", () => {
    const params = buildInboxApiListParams({
      page: 2,
      pageSize: 25,
      q: " 95106 ",
      urgent: true,
      branch: "Jakarta",
      status: "Produksi",
      machine: "M1",
    });
    expect(params.get("page")).toBe("2");
    expect(params.get("pageSize")).toBe("25");
    expect(params.get("q")).toBe("95106");
    expect(params.get("urgent")).toBe("true");
    // Mencegah filter terlihat aktif tetapi diam-diam diabaikan di wire.
    expect(params.has("branch")).toBe(false);
    expect(params.has("status")).toBe(false);
    expect(params.has("machine")).toBe(false);
  });
});

describe("ApiPoSource list/metrics wire", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("list sends only supported filter params even if UI state has branch/status", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input));
        return Response.json({
          items: [],
          total: 0,
          page: 1,
          pageSize: 50,
          source: "mongodb",
        });
      }),
    );

    const source = new ApiPoSource("/api/po");
    await source.list({
      page: 1,
      pageSize: 50,
      q: "ACC",
      urgent: true,
      branch: "Bandung",
      status: "PO Baru",
      machine: "X",
    });

    expect(urls).toHaveLength(1);
    const url = urls[0];
    expect(url).toContain("/api/po?");
    expect(url).toContain("q=ACC");
    expect(url).toContain("urgent=true");
    expect(url).not.toContain("branch=");
    expect(url).not.toContain("status=");
    expect(url).not.toContain("machine=");
  });

  it("metrics uses aggregation endpoint instead of pageSize=500 sample", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        urls.push(url);
        if (url.endsWith("/api/po/metrics")) {
          return Response.json({
            total: 501,
            baru: 501,
            qty: 1002,
            lines: 501,
            urgent: 51,
            printed: 0,
            source: "mongodb",
            origin: "accurate",
          });
        }
        throw new Error(`unexpected fetch ${url}`);
      }),
    );

    const source = new ApiPoSource("/api/po");
    const m = await source.metrics();
    expect(urls).toEqual(["/api/po/metrics"]);
    expect(m).toEqual({
      total: 501,
      baru: 501,
      qty: 1002,
      lines: 501,
      urgent: 51,
      printed: 0,
    });
  });
});
