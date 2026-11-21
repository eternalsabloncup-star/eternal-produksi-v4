import { describe, expect, it } from "vitest";
import headerSource from "./Header.tsx?raw";
import mobileShellSource from "../mobile/MobileOpsShell.tsx?raw";
import scheduleSource from "../components/ScheduleApiPanel.tsx?raw";

describe("Owner web/PWA UI refinements", () => {
  it("places Mobile link beside shell badge to /app/m/tasks", () => {
    expect(headerSource).toContain('to="/app/m/tasks"');
    expect(headerSource).toContain('data-testid="shell-mobile-ops-link"');
    expect(headerSource).toMatch(/>\s*Mobile\s*</);
  });

  it("uses Eternal Packaging branding and Role label in PWA shell", () => {
    expect(mobileShellSource).toContain("Eternal Packaging");
    expect(mobileShellSource).not.toContain("Eternal Ops");
    expect(mobileShellSource).toMatch(
      /data-testid="mobile-ops-role-viewall">\s*Role\s*</,
    );
    expect(mobileShellSource).not.toContain("Baca tugas (view_all)");
  });

  it("removes Ukuran Screen note and Jadwal Produksi duplicate title", () => {
    expect(scheduleSource).not.toContain(
      "Ukuran Screen diambil dari Master Produk",
    );
    expect(scheduleSource).not.toContain("schedule-screen-size-note");
    expect(scheduleSource).not.toContain("Jadwal Produksi · MongoDB V4");
  });
});
