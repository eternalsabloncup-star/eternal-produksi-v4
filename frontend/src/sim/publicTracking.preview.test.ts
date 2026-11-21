import { describe, expect, it } from "vitest";
import { handleSimRequest } from "./handlers";

describe("public tracking fixture (DEV sim)", () => {
  it("serves customer payload for dummy token", async () => {
    const res = handleSimRequest(
      "/api/public/tracking/pt-contoh-kemasan/so-2026-11-001/SIMPREVIEWTOKEN22CHARSXX",
      undefined,
      { active: true },
    );
    expect(res).not.toBeNull();
    expect(res!.status).toBe(200);
    const body = await res!.json();
    expect(body.view).toBe("customer");
    expect(body.order.summary.item_count).toBe(2);
    expect(body.order.accurate_po_id).toBeNull();
    expect(body.milestones.length).toBeGreaterThan(0);
    expect(body.design_approval.eligible).toBe(true);
    expect(body.design_approval.link_status).toBe("active");
    expect(JSON.stringify(body)).not.toMatch(/SIMAPPROVAL/);
  });

  it("rejects invalid dummy token", async () => {
    const res = handleSimRequest(
      "/api/public/tracking/a/b/INVALIDTOKENXXXXXXXXXXXX",
      undefined,
      { active: true },
    );
    expect(res!.status).toBe(404);
  });

  it("no-link / approved / expired scenarios", async () => {
    const none = await handleSimRequest(
      "/api/public/tracking/pt-contoh-kemasan/so-2026-11-001/SIMDAPNOLINKTOKENXXXXXX",
      undefined,
      { active: true },
    )!.json();
    expect(none.design_approval.eligible).toBe(false);
    expect(none.design_approval.link_status).toBe("none");

    const appr = await handleSimRequest(
      "/api/public/tracking/pt-contoh-kemasan/so-2026-11-001/SIMDAPAPPRTOKENXXXXXXXX",
      undefined,
      { active: true },
    )!.json();
    expect(appr.design_approval.link_status).toBe("approved");

    const exp = await handleSimRequest(
      "/api/public/tracking/pt-contoh-kemasan/so-2026-11-001/SIMDAPEXPIREDTOKENXXXXX",
      undefined,
      { active: true },
    )!.json();
    expect(exp.design_approval.eligible).toBe(false);
  });

  it("resolve design-approval path (DEV dummy only)", async () => {
    const ok = handleSimRequest(
      "/api/public/tracking/pt-contoh-kemasan/so-2026-11-001/SIMPREVIEWTOKEN22CHARSXX/design-approval",
      undefined,
      { active: true },
    );
    expect(ok!.status).toBe(200);
    const body = await ok!.json();
    expect(body.path).toBe("/approval/SIMAPPROVALTOKENDUMMYXX");

    const deny = handleSimRequest(
      "/api/public/tracking/pt-contoh-kemasan/so-2026-11-001/SIMDAPEXPIREDTOKENXXXXX/design-approval",
      undefined,
      { active: true },
    );
    expect(deny!.status).toBe(404);
  });
});
