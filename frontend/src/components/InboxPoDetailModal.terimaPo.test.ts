import { describe, expect, it, vi } from "vitest";
import { PoAcceptError } from "../api/poAcceptance";
import {
  TERIMA_PO_SUCCESS_FEEDBACK,
  TERIMA_PO_UNCERTAIN_FEEDBACK,
  isTerimaPoAcceptDisabled,
  shouldResetTerimaPoOverlay,
  terimaPoErrorFeedback,
  terimaPoFeedbackMessage,
  terimaPoOverlayAutoCloseMs,
} from "./InboxPoDetailModal";

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

  it("falls back for empty PoAcceptError and network-like failures", () => {
    expect(terimaPoFeedbackMessage(new PoAcceptError("   ", 500))).toBe(
      "Gagal menerima PO. Silakan coba lagi.",
    );
    expect(terimaPoFeedbackMessage(new Error("Failed to fetch"))).toBe("Failed to fetch");
    expect(terimaPoFeedbackMessage("boom")).toBe(
      "Gagal menerima PO. Silakan coba lagi.",
    );
  });
});

describe("ConfirmationSuccessOverlay contract for Terima PO", () => {
  it("success copy matches Owner requirement (incl. duplicate:true path)", () => {
    expect(TERIMA_PO_SUCCESS_FEEDBACK).toEqual({
      tone: "success",
      title: "PO berhasil diterima",
      message: "PO berhasil diterima.",
    });
    expect(terimaPoOverlayAutoCloseMs("success")).toBe(1800);
  });

  it("error overlay uses tone=error with safe API message for each status class", () => {
    const cases: Array<[number, string]> = [
      [403, "Akses ditolak"],
      [409, "Gagal mengklaim penerimaan PO."],
      [422, "accurate_po_id wajib."],
      [500, "Gagal menerima PO (HTTP 500)."],
    ];
    for (const [status, detail] of cases) {
      expect(terimaPoErrorFeedback(new PoAcceptError(detail, status))).toEqual({
        tone: "error",
        title: "Gagal menerima PO",
        message: detail,
      });
    }
    expect(terimaPoErrorFeedback(new Error("Failed to fetch"))).toEqual({
      tone: "error",
      title: "Gagal menerima PO",
      message: "Failed to fetch",
    });
    expect(terimaPoOverlayAutoCloseMs("error")).toBe(2600);
  });

  it("failed accept must not invoke onAccepted and must keep prior status", async () => {
    const onAccepted = vi.fn();
    const priorStatus = "PO Baru";
    let status = priorStatus;
    let feedback = null as ReturnType<typeof terimaPoErrorFeedback> | typeof TERIMA_PO_SUCCESS_FEEDBACK | null;

    const acceptPo = vi
      .fn()
      .mockRejectedValue(new PoAcceptError("Akses ditolak", 403));

    try {
      await acceptPo("acc-1");
      status = "PO Diterima";
      feedback = TERIMA_PO_SUCCESS_FEEDBACK;
      onAccepted({ status });
    } catch (err) {
      feedback = terimaPoErrorFeedback(err);
    }

    expect(onAccepted).not.toHaveBeenCalled();
    expect(status).toBe(priorStatus);
    expect(feedback?.tone).toBe("error");
    expect(feedback?.title).toBe("Gagal menerima PO");
  });

  it("HTTP 200 including duplicate:true uses success feedback after resolve only", async () => {
    const onAccepted = vi.fn();
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
    const next = {
      acceptanceStatus: res.acceptance.status || "accepted",
      status: res.acceptance.inbox_status || "PO Diterima",
    };
    onAccepted(next);

    expect(res.duplicate).toBe(true);
    expect(onAccepted).toHaveBeenCalledTimes(1);
    expect(next.status).toBe("PO Diterima");
    expect(TERIMA_PO_SUCCESS_FEEDBACK.tone).toBe("success");
  });
});

describe("network uncertain / timeout reconcile contract", () => {
  it("maps uncertain PoAcceptError to dedicated feedback (no success)", () => {
    const err = new PoAcceptError("Status penerimaan belum dapat dipastikan", 0, {
      uncertain: true,
    });
    expect(terimaPoErrorFeedback(err)).toEqual(TERIMA_PO_UNCERTAIN_FEEDBACK);
    expect(terimaPoFeedbackMessage(err)).toBe(TERIMA_PO_UNCERTAIN_FEEDBACK.message);
    expect(TERIMA_PO_UNCERTAIN_FEEDBACK.tone).toBe("error");
  });

  it("reconcile accepted after uncertain uses success path without re-accept", async () => {
    const onAccepted = vi.fn();
    const acceptPo = vi.fn().mockRejectedValue(
      new PoAcceptError("Status penerimaan belum dapat dipastikan", 0, {
        uncertain: true,
      }),
    );
    const fetchStatus = vi.fn().mockResolvedValue({
      status: "accepted",
      inbox_status: "PO Diterima",
      accepted_by: "kp@eternal.local",
      accepted_at: "2026-11-20T00:00:00Z",
    });

    let feedback:
      | typeof TERIMA_PO_SUCCESS_FEEDBACK
      | typeof TERIMA_PO_UNCERTAIN_FEEDBACK
      | null = null;
    try {
      await acceptPo("acc-1");
      feedback = TERIMA_PO_SUCCESS_FEEDBACK;
    } catch (err) {
      if (err instanceof PoAcceptError && err.uncertain) {
        const status = await fetchStatus("acc-1");
        if (status?.status === "accepted") {
          feedback = TERIMA_PO_SUCCESS_FEEDBACK;
          onAccepted({ acceptanceStatus: "accepted" });
        } else {
          feedback = TERIMA_PO_UNCERTAIN_FEEDBACK;
        }
      }
    }

    expect(acceptPo).toHaveBeenCalledTimes(1);
    expect(fetchStatus).toHaveBeenCalledTimes(1);
    expect(feedback).toEqual(TERIMA_PO_SUCCESS_FEEDBACK);
    expect(onAccepted).toHaveBeenCalledTimes(1);
  });
});

describe("overlay reset key (production animation parity)", () => {
  it("does not change when only acceptanceStatus would change", () => {
    const before = shouldResetTerimaPoOverlay({
      open: true,
      rowId: "SO/1",
      accurateId: "95106",
    });
    const afterAccept = shouldResetTerimaPoOverlay({
      open: true,
      rowId: "SO/1",
      accurateId: "95106",
    });
    expect(afterAccept).toBe(before);
  });

  it("changes when open/id/accurateId changes", () => {
    const base = shouldResetTerimaPoOverlay({
      open: true,
      rowId: "SO/1",
      accurateId: "95106",
    });
    expect(
      shouldResetTerimaPoOverlay({
        open: false,
        rowId: "SO/1",
        accurateId: "95106",
      }),
    ).not.toBe(base);
    expect(
      shouldResetTerimaPoOverlay({
        open: true,
        rowId: "SO/2",
        accurateId: "95106",
      }),
    ).not.toBe(base);
    expect(
      shouldResetTerimaPoOverlay({
        open: true,
        rowId: "SO/1",
        accurateId: "99999",
      }),
    ).not.toBe(base);
  });
});

describe("loading and double-submit guards", () => {
  it("disables accept while loading/success/already accepted/no permission", () => {
    expect(isTerimaPoAcceptDisabled("loading", false, true)).toBe(true);
    expect(isTerimaPoAcceptDisabled("success", false, true)).toBe(true);
    expect(isTerimaPoAcceptDisabled("idle", true, true)).toBe(true);
    expect(isTerimaPoAcceptDisabled("idle", false, false)).toBe(true);
    expect(isTerimaPoAcceptDisabled("error", false, true)).toBe(false);
    expect(isTerimaPoAcceptDisabled("idle", false, true)).toBe(false);
  });

  it("blocks a second click while the first request is in flight", async () => {
    let phase: "idle" | "loading" | "success" | "error" = "idle";
    const acceptPo = vi.fn().mockImplementation(async () => {
      await new Promise((r) => setTimeout(r, 20));
      return { duplicate: false, acceptance: { status: "accepted", inbox_status: "PO Diterima" } };
    });

    async function attempt() {
      if (isTerimaPoAcceptDisabled(phase, false, true)) return;
      phase = "loading";
      try {
        await acceptPo("acc-1");
        phase = "success";
      } catch {
        phase = "error";
      }
    }

    const p1 = attempt();
    const p2 = attempt();
    await Promise.all([p1, p2]);
    expect(acceptPo).toHaveBeenCalledTimes(1);
  });
});
