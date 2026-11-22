import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchProductionFlowStageCounts } from "./analytics";

describe("fetchProductionFlowStageCounts", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls stage-counts with q and urgent", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        counts: { "flow-all": 2, "flow-order-masuk": 1 },
        accepted_only: true,
        source: "mongodb",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await fetchProductionFlowStageCounts({
      q: "ALPHA",
      urgent: true,
    });
    expect(res.counts["flow-all"]).toBe(2);
    const calledUrl = String(fetchMock.mock.calls.at(0)?.at(0) ?? "");
    expect(calledUrl).toContain("/api/production-flow/stage-counts?");
    expect(calledUrl).toContain("q=ALPHA");
    expect(calledUrl).toContain("urgent=true");
  });
});
