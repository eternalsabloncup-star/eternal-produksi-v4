import { describe, expect, it } from "vitest";
import { OWNER_DOC_SLOTS, OWNER_SNAKE_STAGES, buildSnakeStages } from "./snakeMapping";
import {
  FORBIDDEN_CUSTOMER_TECHNICAL_SNIPPETS,
  formatEstimasiLabel,
  formatItemCountLabel,
  milestoneStateLabel,
} from "./liveTrackingView";
import type { TrackingMilestone } from "../../api/tracking";

/** Vite raw import — no node:fs; keeps tsc production + test typecheck aligned. */
const trackingSources = import.meta.glob("./*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const appSources = import.meta.glob("../../App.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

function milestone(
  partial: Partial<TrackingMilestone> & Pick<TrackingMilestone, "id" | "state">,
): TrackingMilestone {
  return {
    seq: 0,
    label: partial.id,
    detail: "",
    timestamp: null,
    relevant: true,
    ...partial,
  };
}

describe("liveTrackingView helpers", () => {
  it("formats item_count as Jumlah Item Pesanan label", () => {
    expect(formatItemCountLabel(2)).toBe("2 Item Pesanan");
    expect(formatItemCountLabel(0)).toBe("0 Item Pesanan");
    expect(formatItemCountLabel(null)).toBe("0 Item Pesanan");
    expect(formatItemCountLabel(2.9)).toBe("2 Item Pesanan");
  });

  it("formats eta_ship without hardcoding screenshot dates", () => {
    expect(formatEstimasiLabel("2026-11-25")).toBe("25/11/2026");
    expect(formatEstimasiLabel(null)).toBe("Belum tersedia");
    expect(formatEstimasiLabel("  ", "Menunggu")).toBe("Menunggu");
  });

  it("maps milestone states to Indonesian labels", () => {
    expect(milestoneStateLabel("completed")).toBe("Selesai");
    expect(milestoneStateLabel("in_progress")).toBe("Berjalan");
    expect(milestoneStateLabel("pending")).toBe("Menunggu");
  });
});

describe("Live Tracking customer redesign contracts", () => {
  it("keeps 15 snake stages and wait without source", () => {
    expect(OWNER_SNAKE_STAGES).toHaveLength(15);
    const stages = buildSnakeStages([
      milestone({ id: "order_received", state: "completed", seq: 1 }),
    ]);
    expect(stages).toHaveLength(15);
    expect(stages[0].visual).toBe("done");
    expect(stages[3].visual).toBe("wait"); // Payment
    expect(stages[8].visual).toBe("wait"); // Operator
  });

  it("keeps five Owner document slots", () => {
    expect(OWNER_DOC_SLOTS.map((s) => s.title)).toEqual([
      "Design",
      "Pembayaran",
      "Faktur Penjualan",
      "Surat Jalan",
      "Pengiriman",
    ]);
  });

  it("removes customer-facing technical presentation text from page source", () => {
    const page = trackingSources["./LiveTrackingPage.tsx"];
    expect(page, "LiveTrackingPage.tsx raw source").toBeTruthy();
    for (const snippet of FORBIDDEN_CUSTOMER_TECHNICAL_SNIPPETS) {
      expect(page.includes(snippet), `found forbidden: ${snippet}`).toBe(false);
    }
    expect(page).toContain("formatItemCountLabel");
    expect(page).toContain('setPanel("detail")');
    expect(page).toContain("milestoneStateLabel");
    expect(page).not.toContain("Gap mapping");
    expect(page).not.toContain("process_kind");
  });

  it("uses production LiveTrackingPage component (no alternate demo page)", () => {
    const app = appSources["../../App.tsx"];
    expect(app, "App.tsx raw source").toBeTruthy();
    expect(app).toContain('path="/t/:customerSlug/:poSlug/:token"');
    expect(app).toContain("LiveTrackingPage");
  });
});
