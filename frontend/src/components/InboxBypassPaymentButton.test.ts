import { describe, expect, it } from "vitest";
import { WorkflowApiError } from "../api/workflow";
import type { PoRow } from "../demo/types";
import { __test__ } from "./InboxBypassPaymentButton";

const { isBypassed, isPaymentOpenNormal, safeErrorMessage } = __test__;

function row(partial: Partial<PoRow>): PoRow {
  return {
    id: "PO-1",
    poNo: "PO-1",
    sourcePoId: "accurate:1",
    accurateId: "1",
    origin: "accurate",
    customer: "Cust",
    product: "",
    qty: 0,
    printed: 0,
    qc: 0,
    branch: "",
    status: "PO Diterima",
    machine: "",
    deadline: "",
    urgent: false,
    owner: "",
    sample: false,
    activity: [],
    poDate: "2026-11-01",
    paymentTerms: "",
    etaShip: "",
    seller: "",
    expedition: "",
    shipZone: "",
    recipient: "",
    address: "",
    userName: "",
    bufferLabel: "",
    notes: "",
    totalQtyDisplay: "",
    itemCount: 0,
    items: [],
    acceptanceStatus: "accepted",
    ...partial,
  };
}

describe("InboxBypassPaymentButton helpers", () => {
  it("membedakan bypass vs open normal", () => {
    expect(
      isBypassed(
        row({ paymentGate: { state: "open", bypass: true, reason: "x" } }),
      ),
    ).toBe(true);
    expect(
      isPaymentOpenNormal(
        row({ paymentGate: { state: "open", bypass: true } }),
      ),
    ).toBe(false);
    expect(
      isPaymentOpenNormal(
        row({ paymentGate: { state: "open", bypass: false } }),
      ),
    ).toBe(true);
    expect(
      isBypassed(row({ paymentGate: { state: "review", bypass: false } })),
    ).toBe(false);
  });

  it("menyusun pesan error aman per status HTTP", () => {
    expect(safeErrorMessage(new WorkflowApiError("ditolak cabang", 403))).toContain(
      "ditolak",
    );
    expect(safeErrorMessage(new WorkflowApiError("", 409))).toMatch(/bentrok|Gate/i);
    expect(safeErrorMessage(new WorkflowApiError("", 422))).toMatch(/Alasan/i);
    expect(safeErrorMessage(new WorkflowApiError("", 404))).toMatch(/tidak ditemukan/i);
    expect(safeErrorMessage(new Error("jaringan putus"))).toBe("jaringan putus");
  });
});
