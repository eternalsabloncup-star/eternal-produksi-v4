import { describe, expect, it } from "vitest";
import { isSchedulingTask } from "./teamTaskUi";

/**
 * Guard: penjadwalan* harus dilayani form alokasi, bukan confirm Qty generik.
 * (Wiring UI di TeamTaskApiPanel — assert kontrak tipe di sini.)
 */
describe("scheduling task guard", () => {
  it("marks penjadwalan and penjadwalan-ulang as scheduling-only", () => {
    expect(isSchedulingTask("penjadwalan")).toBe(true);
    expect(isSchedulingTask("penjadwalan-ulang")).toBe(true);
    expect(isSchedulingTask("persiapan-cup")).toBe(false);
    expect(isSchedulingTask("cetak")).toBe(false);
  });
});
