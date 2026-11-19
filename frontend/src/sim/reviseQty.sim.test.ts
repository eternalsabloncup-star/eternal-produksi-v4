import { beforeEach, describe, expect, it } from "vitest";
import { resetSimState } from "./fixtures";
import { handleSimRequest, type SimSession } from "./handlers";

describe("sim revise allocation qty", () => {
  let session: SimSession;

  beforeEach(() => {
    session = { active: true };
    resetSimState();
  });

  it("rejects below confirmed print with stable 409 code", async () => {
    const beforeRes = handleSimRequest(
      "/api/scheduling/10001",
      { method: "GET" },
      session,
    );
    expect(beforeRes).not.toBeNull();
    const before = await beforeRes!.json();
    const alloc = before.lines[0].allocations[0];
    expect(alloc.can_revise).toBe(true);
    expect(alloc.qty_revise_min).toBe(800);

    const bad = handleSimRequest(
      `/api/scheduling/allocations/${alloc.allocation_id}/qty`,
      {
        method: "PUT",
        body: JSON.stringify({ qty: 100, reason: "terlalu kecil" }),
      },
      session,
    );
    expect(bad).not.toBeNull();
    expect(bad!.status).toBe(409);
    const body = await bad!.json();
    expect(body.detail.code).toBe("ALLOCATION_BELOW_CONFIRMED_PRINT");

    const ok = handleSimRequest(
      `/api/scheduling/allocations/${alloc.allocation_id}/qty`,
      {
        method: "PUT",
        body: JSON.stringify({ qty: 1500, reason: "turun valid" }),
      },
      session,
    );
    expect(ok).not.toBeNull();
    expect(ok!.status).toBe(200);
    const revised = await ok!.json();
    expect(revised.qty).toBe(1500);
    expect(revised.qty_printed).toBe(800);

    const afterRes = handleSimRequest(
      "/api/scheduling/10001",
      { method: "GET" },
      session,
    );
    expect(afterRes).not.toBeNull();
    const after = await afterRes!.json();
    expect(after.lines[0].allocations[0].qty).toBe(1500);
    expect(after.lines[0].qty_unallocated).toBe(3500);
    expect(after.lines[0].qty_printed).toBe(800);
  });
});
