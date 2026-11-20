/**
 * Smoke preview DEV sim — board filter/pagination/KPI + facets (bukan browser GUI).
 */
import { describe, expect, it } from "vitest";
import { handleSimRequest } from "./handlers";

const session = { active: true };

async function get(path: string) {
  const url = new URL(path, "http://local.test");
  const res = handleSimRequest(url, { method: "GET" }, session);
  expect(res).not.toBeNull();
  if (res == null) {
    throw new Error(`Sim handler tidak menangani ${path}`);
  }
  return {
    status: res.status,
    body: (await res.json()) as Record<string, unknown>,
  };
}

describe("M6 sim board preview", () => {
  it("returns display columns, facets, KPI, and server empty filter", async () => {
    const board = await get("/api/scheduling/board?page=1&pageSize=50");
    expect(board.status).toBe(200);
    const body = board.body as {
      total: number;
      items: Array<{
        outlet_name?: string;
        paint_color?: string;
        ship_dates?: string[];
      }>;
      kpi: { print_line_count: number };
      filter_facets: Record<string, string[]>;
      accepted_only: boolean;
    };
    expect(body.accepted_only).toBe(true);
    expect(body.total).toBeGreaterThan(0);
    expect(body.kpi.print_line_count).toBe(body.total);
    expect(body.filter_facets.outletName?.length).toBeGreaterThan(0);
    const row = body.items[0];
    expect(row).toBeTruthy();
    expect(typeof row.outlet_name === "string" || row.outlet_name == null).toBe(
      true,
    );

    const empty = await get(
      "/api/scheduling/board?columnFilters=" +
        encodeURIComponent(JSON.stringify({ outletName: ["__none__"] })),
    );
    expect((empty.body as { total: number }).total).toBe(0);
  });
});
