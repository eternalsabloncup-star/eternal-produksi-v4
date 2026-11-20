import { describe, expect, it } from "vitest";
import {
  poDetailPathKey,
  summarizePoDetailDoc,
  urgencyLabel,
} from "./poDetail";

describe("summarizePoDetailDoc", () => {
  it("reads customer and urgent from Accurate PO contract fields", () => {
    const summary = summarizePoDetailDoc({
      accurate_id: "10001",
      po_no: "SO-1",
      customer: "PT Contoh Kemasan",
      branch_id: "150",
      branch_name: null,
      branch_name_verified: false,
      header: {
        customer: "PT Contoh Kemasan",
        urgent: true,
        items: [
          {
            accurate_line_id: "L-1",
            item_code: "CUP",
            item_name: "Cup Cetak",
            qty: 5000,
            unit: "PCS",
            process_kind: "Cetak",
          },
          {
            accurate_line_id: "L-2",
            item_name: "Lid",
            qty: 5000,
            process_kind: "Non-Cetak",
          },
        ],
      },
    });
    expect(summary.customer).toBe("PT Contoh Kemasan");
    expect(summary.branchId).toBe("150");
    expect(summary.branchNameVerified).toBe(false);
    expect(summary.urgent).toBe(true);
    expect(urgencyLabel(summary.urgent)).toBe("Urgent");
    expect(summary.itemCount).toBe(2);
    expect(summary.printLines).toHaveLength(1);
    expect(summary.printLines[0].itemName).toBe("Cup Cetak");
    expect(summary.printLines[0].qty).toBe(5000);
  });

  it("marks urgency unavailable when header.urgent absent", () => {
    const summary = summarizePoDetailDoc({
      header: { items: [] },
      customer: null,
    });
    expect(summary.urgent).toBeNull();
    expect(urgencyLabel(summary.urgent)).toBe("Belum tersedia");
    expect(summary.customer).toBeNull();
  });

  it("does not invent customer from customer_name alias", () => {
    const summary = summarizePoDetailDoc({
      header: {
        customer_name: "Nama Alias",
        urgent: false,
        items: [],
      },
    });
    expect(summary.customer).toBeNull();
    expect(summary.urgent).toBe(false);
    expect(urgencyLabel(false)).toBe("Tidak Urgent");
  });
});

describe("poDetailPathKey", () => {
  it("preserves slash segments like ApiPoSource", () => {
    expect(poDetailPathKey("PO/18/11/2026/000010")).toBe(
      "PO/18/11/2026/000010",
    );
    expect(poDetailPathKey("10001")).toBe("10001");
  });
});
