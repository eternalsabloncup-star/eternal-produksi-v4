import { describe, expect, it } from "vitest";
import { mapHeaderToPoRow } from "./ApiPoSource";

describe("mapHeaderToPoRow acceptance gate", () => {
  it("maps pending acceptance as PO Baru without fake production", () => {
    const row = mapHeaderToPoRow({
      accurate_id: "95106",
      po_no: "PO/1",
      origin: "accurate",
      header: {
        po_no: "PO/1",
        customer: "Cust",
        items: [{ item_code: "A", item_name: "Cup", qty: 10, unit: "PCS" }],
      },
      operational: {},
      acceptance: { status: "pending", inbox_status: "PO Baru" },
    });
    expect(row?.acceptanceStatus).toBe("pending");
    expect(row?.status).toBe("PO Baru");
    expect(row?.printed).toBe(0);
    expect(row?.qc).toBe(0);
  });

  it("maps accepted acceptance to PO Diterima", () => {
    const row = mapHeaderToPoRow({
      accurate_id: "95106",
      po_no: "PO/1",
      origin: "accurate",
      header: {
        po_no: "PO/1",
        customer: "Cust",
        items: [],
      },
      acceptance: {
        status: "accepted",
        accepted_by: "kp@eternal.local",
        accepted_at: "2026-11-19T00:00:00Z",
        inbox_status: "PO Diterima",
      },
    });
    expect(row?.acceptanceStatus).toBe("accepted");
    expect(row?.acceptedBy).toBe("kp@eternal.local");
    expect(row?.status).toBe("PO Diterima");
  });
});
