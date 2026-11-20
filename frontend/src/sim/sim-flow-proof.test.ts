import { beforeEach, describe, expect, it } from "vitest";
import { handleSimRequest, type SimSession } from "./handlers";
import { resetSimState } from "./fixtures";

describe("sim production-flow by-stage", () => {
  let session: SimSession;

  beforeEach(() => {
    session = { active: true };
    resetSimState();
  });

  it("order masuk shows accepted only", async () => {
    const res = handleSimRequest(
      "/api/production-flow/by-stage?node_id=flow-order-masuk",
      { method: "GET" },
      session,
    );
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as {
      total: number;
      items: Array<{ po_no: string; acceptance: { status: string } }>;
    };
    expect(body.total).toBe(1);
    expect(body.items[0].po_no).toBe("SO-2026/11/002");
    expect(body.items[0].acceptance.status).toBe("accepted");
  });

  it("design empty while still order masuk frontier", async () => {
    const res = handleSimRequest(
      "/api/production-flow/by-stage?node_id=flow-design",
      { method: "GET" },
      session,
    );
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as { total: number };
    expect(body.total).toBe(0);
  });

  it("pending by-id returns 404", async () => {
    const res = handleSimRequest(
      "/api/production-flow?accurate_po_id=10001",
      { method: "GET" },
      session,
    );
    expect(res?.status).toBe(404);
  });
});
