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
      scan_capped?: boolean;
      items: Array<{
        po_no: string;
        acceptance: { status: string };
        monitoring_label?: string;
        stage_status: string;
      }>;
    };
    expect(body.total).toBe(1);
    expect(body.scan_capped).toBe(false);
    expect(body.items[0].po_no).toBe("SO-2026/11/002");
    expect(body.items[0].acceptance.status).toBe("accepted");
    expect(body.items[0].stage_status).toBe("selesai");
    expect(body.items[0].monitoring_label).toBe("Aktif");
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

  it("stage-counts returns flow-all and active stage PO counts", async () => {
    const res = handleSimRequest(
      "/api/production-flow/stage-counts",
      { method: "GET" },
      session,
    );
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as {
      counts: Record<string, number>;
      accepted_only?: boolean;
    };
    expect(body.accepted_only).toBe(true);
    expect(body.counts["flow-all"]).toBe(1);
    expect(body.counts["flow-order-masuk"]).toBe(1);
    expect(body.counts["flow-design"]).toBe(0);
  });

  it("flow-all by-stage lists accepted unique POs", async () => {
    const res = handleSimRequest(
      "/api/production-flow/by-stage?node_id=flow-all",
      { method: "GET" },
      session,
    );
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as { total: number; node_id: string };
    expect(body.node_id).toBe("flow-all");
    expect(body.total).toBe(1);
  });
});
