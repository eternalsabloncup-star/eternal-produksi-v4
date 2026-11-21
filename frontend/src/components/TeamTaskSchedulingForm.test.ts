import { describe, expect, it } from "vitest";
import { isSchedulingTask } from "./teamTaskUi";
import {
  operatorCandidateLabel,
  operatorsLoadFromSuccess,
} from "./schedulingOperators";

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

describe("Tambah Alokasi operator options", () => {
  it("does not drop candidates without display_name", () => {
    const state = operatorsLoadFromSuccess([
      { id: "1", email: "op@eternal.local", display_name: "" },
      { id: "2", email: "named@eternal.local", display_name: "Named" },
    ]);
    expect(state.status).toBe("ok");
    if (state.status !== "ok") return;
    expect(state.items).toHaveLength(2);
    expect(operatorCandidateLabel(state.items[0])).toBe("Nama belum tersedia");
  });

  it("keeps selected email stable while other fields change conceptually", () => {
    const selected = "op@eternal.local";
    const afterReload = operatorsLoadFromSuccess([
      { id: "1", email: "op@eternal.local", display_name: "Op Baru" },
    ]);
    expect(afterReload.status).toBe("ok");
    if (afterReload.status !== "ok") return;
    expect(afterReload.items.some((o) => o.email === selected)).toBe(true);
  });
});
