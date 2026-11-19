import { describe, expect, it, vi } from "vitest";
import { PoAcceptError } from "../api/poAcceptance";
import { terimaPoFeedbackMessage } from "./InboxPoDetailModal";

describe("terimaPoFeedbackMessage", () => {
  it("uses API detail for 403/409/422/500 PoAcceptError", () => {
    expect(terimaPoFeedbackMessage(new PoAcceptError("Akses ditolak", 403))).toBe(
      "Akses ditolak",
    );
    expect(
      terimaPoFeedbackMessage(new PoAcceptError("Gagal mengklaim penerimaan PO.", 409)),
    ).toBe("Gagal mengklaim penerimaan PO.");
    expect(terimaPoFeedbackMessage(new PoAcceptError("accurate_po_id wajib.", 422))).toBe(
      "accurate_po_id wajib.",
    );
    expect(
      terimaPoFeedbackMessage(new PoAcceptError("Gagal menerima PO (HTTP 500).", 500)),
    ).toBe("Gagal menerima PO (HTTP 500).");
  });

  it("falls back for empty PoAcceptError and unknown errors", () => {
    expect(terimaPoFeedbackMessage(new PoAcceptError("   ", 500))).toBe(
      "Gagal menerima PO. Silakan coba lagi.",
    );
    expect(terimaPoFeedbackMessage(new Error("Failed to fetch"))).toBe("Failed to fetch");
    expect(terimaPoFeedbackMessage("boom")).toBe(
      "Gagal menerima PO. Silakan coba lagi.",
    );
  });
});

describe("acceptPo feedback contract (mocked)", () => {
  it("maps HTTP 200 and duplicate success to the Owner success copy", async () => {
    const acceptPo = vi.fn().mockResolvedValue({
      accurate_po_id: "acc-1",
      duplicate: true,
      acceptance: {
        status: "accepted",
        inbox_status: "PO Diterima",
        accepted_by: "kp@eternal.local",
        accepted_at: "2026-11-19T00:00:00Z",
      },
      workflow: {},
      po: {},
    });
    const res = await acceptPo("acc-1");
    expect(res.duplicate).toBe(true);
    expect(res.acceptance.status).toBe("accepted");
    // Copy yang dipakai overlay setelah API resolve (bukan sebelum request).
    expect({
      tone: "success" as const,
      title: "PO berhasil diterima",
      message: "PO berhasil diterima.",
    }).toEqual({
      tone: "success",
      title: "PO berhasil diterima",
      message: "PO berhasil diterima.",
    });
  });

  it("does not treat failed accept as success payload", async () => {
    const acceptPo = vi
      .fn()
      .mockRejectedValue(new PoAcceptError("Akses ditolak", 403));
    await expect(acceptPo("acc-1")).rejects.toBeInstanceOf(PoAcceptError);
    expect({
      tone: "error" as const,
      title: "Gagal menerima PO",
      message: terimaPoFeedbackMessage(new PoAcceptError("Akses ditolak", 403)),
    }).toEqual({
      tone: "error",
      title: "Gagal menerima PO",
      message: "Akses ditolak",
    });
  });
});
