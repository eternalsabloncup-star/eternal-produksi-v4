import { describe, expect, it } from "vitest";
import { modulePermLabel, roleKindLabel } from "../settingsLabels";

describe("settingsLabels", () => {
  it("maps role kinds to Indonesian UI labels", () => {
    expect(roleKindLabel("system")).toBe("Sistem");
    expect(roleKindLabel("custom")).toBe("Kustom");
  });

  it("keeps Indonesian module permission labels without requiring raw IDs", () => {
    expect(modulePermLabel("users.manage")).toBe("Kelola pengguna");
    expect(modulePermLabel("operations.tasks.view_all")).toBe("Lihat semua tugas");
  });
});
