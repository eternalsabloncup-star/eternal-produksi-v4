import { describe, expect, it } from "vitest";
import {
  isMobileOpsPath,
  mobileTabByPath,
  MOBILE_OPS_TABS,
} from "./mobileNav";

describe("mobileNav", () => {
  it("exposes exactly four bottom tabs", () => {
    expect(MOBILE_OPS_TABS.map((t) => t.id)).toEqual([
      "tasks",
      "schedule",
      "delivery",
      "monitoring",
    ]);
    expect(MOBILE_OPS_TABS[0].label).toBe("Tugas Saya");
  });

  it("resolves active tab from path", () => {
    expect(mobileTabByPath("/app/m/tasks").id).toBe("tasks");
    expect(mobileTabByPath("/app/m/schedule").id).toBe("schedule");
    expect(mobileTabByPath("/app/m/delivery").id).toBe("delivery");
    expect(mobileTabByPath("/app/m/monitoring").id).toBe("monitoring");
    expect(isMobileOpsPath("/app/m/tasks")).toBe(true);
    expect(isMobileOpsPath("/app/schedule")).toBe(false);
  });
});
