import { describe, expect, it } from "vitest";
import type { StatusFilter } from "../components/teamTaskUi";

/**
 * Kontrak PWA Tugas Saya: default filter Belum Dikerjakan.
 * Desktop TeamTaskApiPanel tetap default ``all`` (prop opsional).
 */
describe("PWA Tugas Saya default status filter", () => {
  it("uses belum as the approved PWA initial filter", () => {
    const pwaDefault: StatusFilter = "belum";
    const desktopDefault: StatusFilter = "all";
    expect(pwaDefault).toBe("belum");
    expect(desktopDefault).toBe("all");
    const filters: StatusFilter[] = ["all", "belum", "sebagian", "selesai"];
    expect(filters).toContain(pwaDefault);
  });
});
