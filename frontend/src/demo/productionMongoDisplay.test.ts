/**
 * Regresi: F12/Mongo header 95106 (sanitized) → ApiPoSource → tampilan Inbox/modal.
 */
import { describe, expect, it } from "vitest";
import { formatDateDisplay } from "../lib/formatDate";
import { mapHeaderToPoRow } from "./ApiPoSource";

/** Header setelah normalize F12 95106 — nilai bisnis Exact, identitas disanitasi. */
const F12_95106_HEADER = {
  accurate_id: "95106",
  po_no: "PO/18/11/2026/000010",
  customer: "PELANGGAN-SANITIZED",
  po_date: "18/11/2026",
  eta_ship: "21/11/2026",
  recipient: "",
  seller: "Sales Transaksi Sanitized",
  user_name: "",
  ship_zone: "",
  buffer_label: "Non - Buffer",
  priority_label: "Normal",
  notes: "CATATAN-SANITIZED\nPROMO-SANITIZED",
  payment_terms: "TRANSFER BANK",
  expedition: "OUTLET",
  address: "ALAMAT-SANITIZED\nJl. Contoh No. 0, Kota Contoh 00000",
  address_pic_name: "PIC-SANITIZED",
  print_user_name: "print.user.sanitized",
  created_by_id: "1162627",
  urgent: false,
  items: [
    {
      line_id: "accurate-line:106318",
      accurate_line_id: "106318",
      item_code: "SKU-CETAK-SAN",
      item_name: "Item Cetak Sanitized",
      qty: 1000,
      unit: "PCS",
      process_raw: "Cetak",
      process_kind: "Cetak",
      color: "PUTIH",
    },
    {
      line_id: "accurate-line:106319",
      accurate_line_id: "106319",
      item_code: "SKU-NON-SAN",
      item_name: "Item Non-Cetak Sanitized",
      qty: 1000,
      unit: "PCS",
      process_raw: "Tidak Cetak",
      process_kind: "Non-Cetak",
      color: "BENING",
    },
  ],
};

describe("F12 / production Mongo 95106 display chain", () => {
  it("maps F12 fields through ApiPoSource for Inbox/modal", () => {
    const row = mapHeaderToPoRow({
      accurate_id: "95106",
      source_po_id: "accurate:95106",
      po_no: "PO/18/11/2026/000010",
      origin: "accurate",
      header: F12_95106_HEADER,
      operational: {},
    });
    expect(row).not.toBeNull();
    expect(row!.origin).toBe("accurate");
    expect(row!.poNo).toBe("PO/18/11/2026/000010");
    expect(row!.customer).toBe("PELANGGAN-SANITIZED");
    expect(row!.poDate).toBe("18/11/2026");
    expect(row!.etaShip).toBe("21/11/2026");
    expect(formatDateDisplay(row!.poDate)).toBe("18/11/2026");
    expect(formatDateDisplay(row!.etaShip)).toBe("21/11/2026");
    expect(row!.bufferLabel).toBe("Non - Buffer");
    expect(row!.priorityLabel).toBe("Normal");
    expect(row!.seller).toBe("Sales Transaksi Sanitized");
    expect(row!.userName).toBe("");
    expect(row!.printUserName).toBe("print.user.sanitized");
    expect(row!.createdById).toBe("1162627");
    expect(row!.addressPicName).toBe("PIC-SANITIZED");
    expect(row!.recipient).toBe("");
    expect(row!.shipZone).toBe("");
    expect(row!.itemCount).toBe(2);
    expect(row!.qty).toBe(2000);
    expect(row!.totalQtyDisplay).toBe("1.000 PCS + 1.000 PCS");
    const kinds = row!.items.map((i) => i.processKind);
    expect(kinds).toEqual(["Cetak", "Non-Cetak"]);
    expect(row!.items.map((i) => i.accurateLineId)).toEqual(["106318", "106319"]);
  });

  it("formatDateDisplay shows Mongo DD/MM dates in Inbox/modal", () => {
    expect(formatDateDisplay("18/11/2026")).toBe("18/11/2026");
    expect(formatDateDisplay("21/11/2026")).toBe("21/11/2026");
    expect(formatDateDisplay("")).toBe("—");
  });

  it("does not treat printUserName or PIC as creator/recipient", () => {
    const row = mapHeaderToPoRow({
      accurate_id: "95106",
      origin: "accurate",
      header: F12_95106_HEADER,
    });
    expect(row!.printUserName).toBe("print.user.sanitized");
    expect(row!.userName).toBe("");
    expect(row!.addressPicName).toBe("PIC-SANITIZED");
    expect(row!.recipient).toBe("");
    expect(formatDateDisplay(row!.poNo)).toBe("—");
  });
});
