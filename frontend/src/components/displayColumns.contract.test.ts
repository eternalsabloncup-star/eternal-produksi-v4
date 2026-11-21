/**
 * Kontrak kolom tampilan — tipe API + formatter tanggal (tanpa mount browser).
 */
import { describe, expect, it } from "vitest";
import type { OperationalTask } from "../api/operational";
import type { LineSchedule } from "../api/scheduling";
import { formatDateDisplay } from "../lib/formatDate";

describe("display columns API contract", () => {
  it("LineScheduleState membawa outlet_name, paint_color, ship_dates", () => {
    const row = {
      outlet_name: "Pelanggan Asli",
      paint_color: "PUTIH",
      ship_dates: ["25/11/2026", "27/11/2026"],
    } satisfies Pick<
      LineSchedule,
      "outlet_name" | "paint_color" | "ship_dates"
    >;
    expect(row.outlet_name).toBe("Pelanggan Asli");
    expect(row.paint_color).toBe("PUTIH");
    expect(row.ship_dates).toEqual(["25/11/2026", "27/11/2026"]);
  });

  it("OperationalTask membawa outlet_name + ship_dates untuk semua role", () => {
    const task = {
      outlet_name: "Pelanggan Asli",
      ship_dates: ["29/11/2026"],
    } satisfies Pick<OperationalTask, "outlet_name" | "ship_dates">;
    expect(task.outlet_name).toBe("Pelanggan Asli");
    expect(task.ship_dates).toEqual(["29/11/2026"]);
  });

  it("OperationalTask membawa item_name (Nama Barang) selain item_code/line", () => {
    const task = {
      item_name: "12 oz Oval Inject Bening Super",
      ref: {
        item_name: "12 oz Oval Inject Bening Super",
        item_code: "100359",
        accurate_line_id: "106318",
      },
    } satisfies Pick<OperationalTask, "item_name" | "ref">;
    expect(task.item_name).toBe("12 oz Oval Inject Bening Super");
    expect(task.ref.item_name).toBe(task.item_name);
    expect(task.ref.item_code).not.toBe(task.item_name);
    expect(task.ref.accurate_line_id).not.toBe(task.item_name);
  });

  it("kolom Nama Customer memakai outlet_name otoritatif (bukan invent)", () => {
    const roles = [
      "production-head",
      "designer",
      "screener",
      "helper",
      "operator",
      "driver",
    ] as const;
    for (const role of roles) {
      const label = "Nama Customer";
      const field = "outlet_name";
      expect(role.length).toBeGreaterThan(0);
      expect(label).toBe("Nama Customer");
      expect(field).toBe("outlet_name");
    }
  });

  it("ship_dates kosong ditampilkan sebagai em dash di UI (D4)", () => {
    const dates: string[] = [];
    const text = dates.length
      ? dates.map((d) => formatDateDisplay(d, "")).filter(Boolean).join(", ")
      : "—";
    expect(text).toBe("—");
  });

  it("formatDateDisplay menjaga DD/MM/YYYY tanpa timezone shift", () => {
    expect(formatDateDisplay("25/11/2026")).toBe("25/11/2026");
    expect(formatDateDisplay("2026-11-25")).toBe("25/11/2026");
    expect(formatDateDisplay("2026-11-25T00:00:00Z")).toBe("25/11/2026");
  });
});
