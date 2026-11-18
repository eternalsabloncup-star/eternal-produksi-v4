import { describe, expect, it } from "vitest";
import type { DeliveryScheduleRow } from "../api/delivery";
import {
  display,
  displayBoolean,
  filterDeliveryRows,
  formatQtyByUnit,
  loadingStatusLabel,
  matchesSearch,
  matchesShipDate,
  qtyByUnit,
  revisionStatusLabel,
  rowTotalQtyDisplay,
  scheduleMetrics,
} from "./deliverySj";

function row(overrides: Partial<DeliveryScheduleRow> = {}): DeliveryScheduleRow {
  return {
    accurate_sj_id: "770001",
    sj_no: "TEST-SJ/0001",
    accurate_po_id: "95106",
    po_no: "TEST-PO/000010",
    customer_po_no: "PO-PELANGGAN-9",
    sj_date: "18/11/2026",
    ship_date: "18/11/2026",
    customer_name: "PELANGGAN-TEST A",
    customer_no: "C-TEST-001",
    destination: "ALAMAT-TEST",
    contact_info: "KONTAK-TEST",
    salesman_name: "PENJUAL-TEST",
    created_by_user_name: "user.pembuat.test",
    shipment: "EKSPEDISI-TEST",
    ship_zone: "ZONA-TEST",
    branch_name: "CABANG-TEST",
    description: "KETERANGAN-TEST",
    printed_by_user: "user.cetak.test",
    printed_time: "18/11/2026 10:05:00",
    packing_number: "PACK-TEST-0001",
    accurate_status_name: "Belum Terkirim",
    attachment_exist: false,
    items: [
      {
        sj_line_id: "880001",
        accurate_line_id: "106318",
        item_code: "BRG-TEST-001",
        item_name: "Cup Test Motif A",
        qty: 2000,
        unit: "PCS",
        warehouse_name: "GUDANG-TEST-A",
        detail_notes: "CATATAN-1",
      },
      {
        sj_line_id: "880002",
        accurate_line_id: "106319",
        item_code: "BRG-TEST-001",
        item_name: "Cup Test Motif B",
        qty: 500,
        unit: "PCS",
      },
      {
        sj_line_id: "880003",
        accurate_line_id: "106320",
        item_code: "BRG-TEST-900",
        item_name: "Lid Test",
        qty: 20,
        unit: "PACK",
      },
    ],
    item_count: 3,
    qty_by_unit: { PCS: 2500, PACK: 20 },
    loaded: false,
    needs_reloading: false,
    delivery_status: "menunggu_pengiriman",
    attempts: 0,
    driver_email: "",
    recipient_name: "",
    ...overrides,
  };
}

describe("tampilan nilai kosong", () => {
  it("menandai field kosong sebagai kosong, bukan nilai asumsi", () => {
    expect(display("")).toBe("—");
    expect(display(null)).toBe("—");
    expect(display("   ")).toBe("—");
    expect(display("ZONA-TEST")).toBe("ZONA-TEST");
  });

  it("membedakan boolean tidak tersedia dari false", () => {
    expect(displayBoolean(true)).toBe("Ya");
    expect(displayBoolean(false)).toBe("Tidak");
    expect(displayBoolean(null)).toBe("—");
    expect(displayBoolean(undefined)).toBe("—");
  });
});

describe("total kuantitas", () => {
  it("tidak menjumlahkan satuan berbeda", () => {
    expect(qtyByUnit(row().items)).toEqual({ PCS: 2500, PACK: 20 });
    expect(rowTotalQtyDisplay(row())).toBe("2.500 PCS + 20 PACK");
  });

  it("menghitung dari item ketika agregat backend belum ada", () => {
    expect(rowTotalQtyDisplay(row({ qty_by_unit: {} }))).toBe(
      "2.500 PCS + 20 PACK",
    );
  });

  it("menampilkan kosong saat tidak ada item", () => {
    expect(formatQtyByUnit({})).toBe("—");
  });

  it("kode barang sama pada dua baris tidak digabung", () => {
    const sameCode = row().items.filter((i) => i.item_code === "BRG-TEST-001");
    expect(sameCode).toHaveLength(2);
    expect(sameCode.map((i) => i.accurate_line_id)).toEqual(["106318", "106319"]);
  });
});

describe("filter tanggal kirim", () => {
  it("cocok untuk satu tanggal lintas format sumber", () => {
    expect(matchesShipDate("18/11/2026", { kind: "single", date: "2026-11-18" })).toBe(
      true,
    );
    expect(matchesShipDate("2026-11-18", { kind: "single", date: "2026-11-18" })).toBe(
      true,
    );
    expect(matchesShipDate("19/11/2026", { kind: "single", date: "2026-11-18" })).toBe(
      false,
    );
  });

  it("cocok untuk rentang tanggal", () => {
    const filter = { kind: "range", start: "2026-11-17", end: "2026-11-19" } as const;
    expect(matchesShipDate("18/11/2026", filter)).toBe(true);
    expect(matchesShipDate("20/11/2026", filter)).toBe(false);
  });

  it("baris tanpa tanggal tidak ikut rentang apa pun", () => {
    expect(matchesShipDate("", { kind: "single", date: "2026-11-18" })).toBe(false);
    expect(matchesShipDate("", { kind: "all" })).toBe(true);
  });
});

describe("pencarian", () => {
  it("mencari pada field header dan item", () => {
    expect(matchesSearch(row(), "penjual-test")).toBe(true);
    expect(matchesSearch(row(), "PACK-TEST-0001")).toBe(true);
    expect(matchesSearch(row(), "Lid Test")).toBe(true);
    expect(matchesSearch(row(), "tidak-ada")).toBe(false);
  });

  it("kata kunci kosong tidak memfilter", () => {
    expect(matchesSearch(row(), "  ")).toBe(true);
  });
});

describe("gabungan filter dan KPI", () => {
  const rows = [
    row(),
    row({
      accurate_sj_id: "770002",
      sj_no: "TEST-SJ/0002",
      ship_date: "20/11/2026",
      destination: "ALAMAT-TEST-2",
      loaded: true,
      qty_by_unit: { PCS: 100 },
      items: [
        {
          sj_line_id: "880011",
          accurate_line_id: "106318",
          item_code: "BRG-TEST-001",
          item_name: "Cup Test Motif A",
          qty: 100,
          unit: "PCS",
        },
      ],
      item_count: 1,
    }),
  ];

  it("menyaring tanggal dan kata kunci sekaligus", () => {
    expect(
      filterDeliveryRows(rows, { kind: "single", date: "2026-11-20" }, "").map(
        (r) => r.sj_no,
      ),
    ).toEqual(["TEST-SJ/0002"]);
    expect(filterDeliveryRows(rows, { kind: "all" }, "Lid Test")).toHaveLength(1);
  });

  it("KPI mengikuti baris hasil filter", () => {
    const all = scheduleMetrics(rows);
    expect(all.sjCount).toBe(2);
    expect(all.qtyByUnit).toEqual({ PCS: 2600, PACK: 20 });
    expect(all.destinationCount).toBe(2);
    expect(all.loadedCount).toBe(1);

    const filtered = scheduleMetrics(
      filterDeliveryRows(rows, { kind: "single", date: "2026-11-20" }, ""),
    );
    expect(filtered.sjCount).toBe(1);
    expect(filtered.qtyByUnit).toEqual({ PCS: 100 });
  });
});

describe("label status operasional", () => {
  it("membedakan belum, sudah, dan perlu Loading ulang", () => {
    expect(loadingStatusLabel(row())).toBe("Belum Loading");
    expect(loadingStatusLabel(row({ loaded: true }))).toBe("Sudah Loading");
    expect(
      loadingStatusLabel(row({ loaded: true, needs_reloading: true })),
    ).toBe("Perlu Loading Ulang");
  });

  it("menerjemahkan status revisi SJ", () => {
    expect(revisionStatusLabel("aktif")).toBe("Aktif");
    expect(revisionStatusLabel("PERLU_LOADING_ULANG")).toBe("Perlu Loading Ulang");
    expect(revisionStatusLabel("PERLU_KOREKSI_ADMINISTRASI")).toBe(
      "Perlu Koreksi Administrasi",
    );
    expect(revisionStatusLabel(undefined)).toBe("—");
  });
});
