import { afterEach, describe, expect, it, vi } from "vitest";
import {
  assertSoftDeleteReason,
  DeliveryApiError,
  softDeleteDeliveryEvidence,
  softDeleteEvidenceErrorMessage,
} from "./delivery";

describe("assertSoftDeleteReason", () => {
  it("rejects short or empty reason", () => {
    expect(() => assertSoftDeleteReason("")).toThrow(DeliveryApiError);
    expect(() => assertSoftDeleteReason("ab")).toThrow(/min/);
  });

  it("trims valid reason", () => {
    expect(assertSoftDeleteReason("  ok reason  ")).toBe("ok reason");
  });
});

describe("softDeleteEvidenceErrorMessage", () => {
  it("maps 403/404/409/network", () => {
    expect(softDeleteEvidenceErrorMessage(new DeliveryApiError("x", 403))).toMatch(
      /403|Izin|ditolak/i,
    );
    expect(softDeleteEvidenceErrorMessage(new DeliveryApiError("x", 404))).toMatch(
      /404|tidak ditemukan/i,
    );
    expect(softDeleteEvidenceErrorMessage(new DeliveryApiError("x", 409))).toMatch(
      /409|Konflik/i,
    );
    expect(softDeleteEvidenceErrorMessage(new DeliveryApiError("down", 0))).toMatch(
      /Koneksi|down/i,
    );
  });
});

describe("softDeleteDeliveryEvidence", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function stubDomCookie() {
    vi.stubGlobal("document", { cookie: "csrf_token=test-csrf" });
  }

  it("POSTs body only after reason validation and treats success only on ok", async () => {
    stubDomCookie();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ evidence_id: "ev-1", deleted: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await softDeleteDeliveryEvidence("ev-1", "Foto buram");
    expect(res.evidence_id).toBe("ev-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/delivery/evidence/soft-delete");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      evidence_id: "ev-1",
      reason: "Foto buram",
    });
  });

  it("does not call fetch when reason invalid", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(softDeleteDeliveryEvidence("ev-1", "x")).rejects.toBeInstanceOf(
      DeliveryApiError,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces 403 from backend without claiming success", async () => {
    stubDomCookie();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ detail: "Izin ditolak" }),
      }),
    );
    await expect(
      softDeleteDeliveryEvidence("ev-1", "Alasan cukup"),
    ).rejects.toMatchObject({ status: 403, message: "Izin ditolak" });
  });
});
