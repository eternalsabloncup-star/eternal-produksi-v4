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

  it("lists and partially confirms Non-Cetak packing task", async () => {
    const list = handleSimRequest(
      "/api/tasks?role=helper&done=false",
      { method: "GET" },
      session,
    );
    expect(list?.status).toBe(200);
    const listed = (await list!.json()) as {
      items: Array<{
        task_key: string;
        task_type: string;
        label?: string;
        ref?: { process_kind?: string; accurate_po_id?: string; accurate_line_id?: string };
      }>;
    };
    const nc = listed.items.find(
      (t) =>
        t.task_type === "qc-packing" &&
        t.ref?.process_kind === "Non-Cetak",
    );
    expect(nc).toBeTruthy();
    expect(nc!.label).toBe("Packing Non-Cetak");

    const res = handleSimRequest(
      `/api/production/${nc!.ref!.accurate_po_id}/lines/${nc!.ref!.accurate_line_id}/qc-packing`,
      {
        method: "POST",
        body: JSON.stringify({ qty: 3000, idempotency_key: "nc-sim-3000" }),
      },
      session,
    );
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as {
      task: {
        status: string;
        qty_confirmed: number;
        task_key: string;
        ref?: { process_kind?: string };
      };
    };
    expect(body.task.task_key).toBe(nc!.task_key);
    expect(body.task.qty_confirmed).toBe(3000);
    expect(body.task.status).toBe("sebagian");
    expect(body.task.ref?.process_kind).toBe("Non-Cetak");
  });

  it("does not handle non-api paths", () => {
    expect(handleSimRequest("/app/inbox", { method: "GET" }, session)).toBe(
      null,
    );
  });

  it("SJ detail loadings expose Master display names for cancel actors", async () => {
    const res = handleSimRequest("/api/delivery/SJ-1", { method: "GET" }, session);
    expect(res?.status).toBe(200);
    const body = (await res!.json()) as {
      loadings: Array<{
        status: string;
        cancelled_by?: string | null;
        cancelled_by_display_name?: string;
        actor_display_name?: string;
      }>;
      events: Array<{ courier_email: string; actor_display_name?: string }>;
    };
    const cancelled = body.loadings.filter((l) => l.status === "dibatalkan");
    expect(cancelled.length).toBeGreaterThanOrEqual(2);
    expect(cancelled[0].cancelled_by).toBe("kp@eternal.local");
    expect(cancelled[0].cancelled_by_display_name).toBe("Kepala Produksi");
    expect(cancelled.some((l) => l.cancelled_by_display_name === "Nama belum tersedia")).toBe(
      true,
    );
    const active = body.loadings.find((l) => l.status === "aktif");
    expect(active?.actor_display_name).toBeTruthy();
    expect(active?.cancelled_by == null || active?.cancelled_by === "").toBe(true);
    expect(body.events[0]?.actor_display_name).toBeTruthy();
  });
});
