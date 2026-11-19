import { describe, expect, it, beforeEach } from "vitest";
import { handleSimRequest, type SimSession } from "./handlers";
import { resetSimState, simTasks } from "./fixtures";

describe("handleSimRequest", () => {
  let session: SimSession;

  beforeEach(() => {
    session = { active: true };
    resetSimState();
  });

  it("returns auth me when session active", async () => {
    const res = handleSimRequest("/api/auth/me", { method: "GET" }, session);
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as { user: { role: string } };
    expect(body.user.role).toBe("super_admin");
  });

  it("lists tasks for a role using API panel contract", async () => {
    const res = handleSimRequest(
      "/api/tasks?role=operator&done=false",
      { method: "GET" },
      session,
    );
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as { items: unknown[]; source: string };
    expect(body.source).toBe("api-simulation");
    expect(body.items.length).toBeGreaterThan(0);
  });

  it("confirms task qty and updates status", async () => {
    const open = simTasks.find(
      (t) => t.assigned_role === "helper" && t.task_type === "persiapan-cup",
    )!;
    const res = handleSimRequest(
      `/api/tasks/${encodeURIComponent(open.task_key)}/confirm`,
      {
        method: "POST",
        body: JSON.stringify({ qty: 5000, idempotency_key: "test-1" }),
      },
      session,
    );
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as {
      task: { status: string; qty_confirmed: number };
      duplicate: boolean;
    };
    expect(body.duplicate).toBe(false);
    expect(body.task.status).toBe("selesai");
    expect(body.task.qty_confirmed).toBe(5000);
  });

  it("does not handle non-api paths", () => {
    expect(handleSimRequest("/app/inbox", { method: "GET" }, session)).toBe(
      null,
    );
  });
});
