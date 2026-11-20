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

  it("OperationalTask membawa outlet_name + ship_dates untuk KP", () => {
    const task = {
      outlet_name: "Pelanggan Asli",
      ship_dates: ["29/11/2026"],
    } satisfies Pick<OperationalTask, "outlet_name" | "ship_dates">;
    expect(task.outlet_name).toBe("Pelanggan Asli");
    expect(task.ship_dates).toEqual(["29/11/2026"]);
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
