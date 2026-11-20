/**
 * Route handlers for local API simulation (DEV only).
 */

import {
  ALLOC_A,
  LINE_CETAK,
  PO_ID,
  PO_NO,
  SIM_ACTOR,
  SIM_BUFFER,
  SIM_CAPABILITIES,
  SIM_CSRF,
  SIM_DASHBOARD,
  SIM_DELIVERY_SCHEDULE,
  SIM_FLOW,
  SIM_PO_DOCS,
  SIM_ROLE_ACTIONS,
  SIM_SCHEDULE_LINES,
  SIM_TASK_TYPES,
  SIM_TRACKING,
  SIM_USER,
  simConfirmations,
  simTasks,
  type SimConfirmation,
} from "./fixtures";

export type SimSession = { active: boolean };

type SimTrackingLink = {
  id: string;
  accurate_po_id: string;
  po_no: string;
  customer_name: string;
  customer_slug: string;
  po_slug: string;
  status: "active" | "revoked" | "expired";
  scopes: string[];
  created_at: string;
  created_by: string | null;
  revoked_at: string | null;
  expires_at: string | null;
  access_count: number;
  last_accessed_at: string | null;
  /** Sim only — stands in for Owner-approved recoverable ciphertext → public path. */
  path?: string;
};

const SIM_TRACKING_STORE_KEY = "eternal.v4.sim.trackingLinks.v1";

/** Per-PO mutable link store. Persisted so refresh keeps "Sudah Dibuat" in DEV sim. */
const simTrackingLinksByPo = new Map<string, SimTrackingLink[]>();
let simTrackingStoreHydrated = false;

function seedActiveLink(poKey: string): SimTrackingLink {
  const customer_slug = "pt-contoh-kemasan";
  const po_slug = `po-${poKey}`;
  const token = `SIMSEED${poKey.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().padEnd(6, "X").slice(0, 6)}TOKEN22CHARS`;
  return {
    id: `link-sim-seed-${poKey}`,
    accurate_po_id: poKey,
    po_no: `SO-${poKey}`,
    customer_name: "PT Contoh Kemasan",
    customer_slug,
    po_slug,
    status: "active",
    scopes: ["progress", "documents"],
    created_at: "2026-11-18T00:00:00Z",
    created_by: SIM_ACTOR,
    revoked_at: null,
    expires_at: null,
    access_count: 1,
    last_accessed_at: "2026-11-19T08:00:00Z",
    path: `/t/${customer_slug}/${po_slug}/${token}`,
  };
}

function hydrateSimTrackingStore(): void {
  if (simTrackingStoreHydrated) return;
  simTrackingStoreHydrated = true;
  if (typeof sessionStorage !== "undefined") {
    try {
      const raw = sessionStorage.getItem(SIM_TRACKING_STORE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Record<string, SimTrackingLink[]>;
        for (const [poKey, links] of Object.entries(parsed)) {
          simTrackingLinksByPo.set(poKey, links);
        }
      }
    } catch {
      // ignore corrupt sim store
    }
  }
  // Fixture preview: 10002 already has active link; 10001 starts empty.
  if (!simTrackingLinksByPo.has("10002")) {
    simTrackingLinksByPo.set("10002", [seedActiveLink("10002")]);
  }
  if (!simTrackingLinksByPo.has("10001")) {
    simTrackingLinksByPo.set("10001", []);
  }
  persistSimTrackingStore();
}

function persistSimTrackingStore(): void {
  if (typeof sessionStorage === "undefined") return;
  const obj: Record<string, SimTrackingLink[]> = {};
  for (const [poKey, links] of simTrackingLinksByPo) {
    obj[poKey] = links;
  }
  try {
    sessionStorage.setItem(SIM_TRACKING_STORE_KEY, JSON.stringify(obj));
  } catch {
    // ignore quota
  }
}

function extractTrackingPoKey(path: string): string {
  const m = path.match(/^\/api\/tracking\/([^/]+)/);
  return decodeURIComponent(m?.[1] || PO_ID);
}

function getSimTrackingLinks(poKey: string): SimTrackingLink[] {
  hydrateSimTrackingStore();
  if (!simTrackingLinksByPo.has(poKey)) {
    simTrackingLinksByPo.set(poKey, []);
  }
  return simTrackingLinksByPo.get(poKey) || [];
}

function setSimTrackingLinks(poKey: string, links: SimTrackingLink[]): void {
  hydrateSimTrackingStore();
  simTrackingLinksByPo.set(poKey, links);
  persistSimTrackingStore();
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function ensureCsrfCookie(): void {
  if (typeof document === "undefined") return;
  if (!document.cookie.includes("eternal_csrf=")) {
    document.cookie = `eternal_csrf=${SIM_CSRF}; path=/; SameSite=Lax`;
  }
}

function mePayload() {
  return {
    user: SIM_USER,
    csrf_token: SIM_CSRF,
    capabilities: SIM_CAPABILITIES,
  };
}

function findTask(taskKey: string) {
  const decoded = decodeURIComponent(taskKey);
  return simTasks.find((t) => t.task_key === decoded || t.task_key === taskKey);
}

function applyConfirm(
  taskKey: string,
  qty: number,
  idempotencyKey: string,
  note?: string,
) {
  const task = findTask(taskKey);
  if (!task) return { error: "Tugas tidak ditemukan", status: 404 as const };
  const existing = simConfirmations.get(task.task_key) || [];
  const dup = existing.find((c) => c.idempotency_key === idempotencyKey);
  if (dup) {
    return { task, duplicate: true };
  }
  const nextQty = task.qty_confirmed + qty;
  task.qty_confirmed = nextQty;
  const target = task.qty_target ?? nextQty;
  if (nextQty >= target) task.status = "selesai";
  else if (nextQty > 0) task.status = "sebagian";
  const row: SimConfirmation = {
    idempotency_key: idempotencyKey,
    qty,
    actor_email: SIM_ACTOR,
    actor_role: "super_admin",
    note: note ?? null,
    created_at: new Date().toISOString(),
  };
  simConfirmations.set(task.task_key, [...existing, row]);
  return { task, duplicate: false };
}

function filterTasks(url: URL) {
  const role = url.searchParams.get("role");
  const taskType = url.searchParams.get("task_type");
  const done = url.searchParams.get("done");
  const limit = Number(url.searchParams.get("limit") || "200");
  let items = [...simTasks];
  if (role) items = items.filter((t) => t.assigned_role === role);
  if (taskType) items = items.filter((t) => t.task_type === taskType);
  if (done === "true") items = items.filter((t) => t.status === "selesai");
  if (done === "false") items = items.filter((t) => t.status !== "selesai");
  items = items.slice(0, Number.isFinite(limit) ? limit : 200);
  return { items, total: items.length, source: "api-simulation" };
}

/** Returns Response if handled; null to pass through to network. */
export function handleSimRequest(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  session: SimSession,
): Response | null {
  const raw =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  let url: URL;
  try {
    url = new URL(raw, "http://local.sim");
  } catch {
    return null;
  }
  if (!url.pathname.startsWith("/api/")) return null;

  const method = (init?.method || "GET").toUpperCase();
  const path = url.pathname;
  ensureCsrfCookie();

  // --- Auth ---
  if (path === "/api/auth/me" && method === "GET") {
    if (!session.active) return json({ detail: "Tidak terautentikasi" }, 401);
    return json(mePayload());
  }
  if (path === "/api/auth/login" && method === "POST") {
    session.active = true;
    ensureCsrfCookie();
    return json(mePayload());
  }
  if (path === "/api/auth/logout" && method === "POST") {
    session.active = false;
    return json({ ok: true });
  }
  if (path === "/api/health" && method === "GET") {
    return json({
      status: "ok",
      app_env: "simulation",
      mongodb: false,
      simulation: true,
    });
  }

  if (!session.active) {
    return json({ detail: "Tidak terautentikasi" }, 401);
  }

  // --- PO / Inbox ---
  if (path === "/api/po" && method === "GET") {
    return json({
      items: SIM_PO_DOCS,
      total: SIM_PO_DOCS.length,
      page: 1,
      pageSize: 50,
      source: "api-simulation",
    });
  }
  if (path === "/api/po/metrics" && method === "GET") {
    return json({
      total: SIM_PO_DOCS.length,
      baru: 1,
      qty: 12000,
      lines: 3,
      source: "api-simulation",
    });
  }
  if (path.startsWith("/api/po/") && path.endsWith("/accept") && method === "POST") {
    const id = path.split("/")[3];
    const doc = SIM_PO_DOCS.find((d) => String(d.accurate_id) === id);
    if (!doc) return json({ detail: "PO tidak ditemukan" }, 404);
    doc.acceptance = {
      status: "accepted",
      accepted_by: SIM_ACTOR,
      accepted_at: new Date().toISOString(),
      inbox_status: "PO Diterima",
    };
    return json(doc);
  }
  if (path.startsWith("/api/po/") && method === "GET") {
    const id = decodeURIComponent(path.slice("/api/po/".length));
    const doc =
      SIM_PO_DOCS.find((d) => String(d.accurate_id) === id) ||
      SIM_PO_DOCS.find(
        (d) => String((d.header as { po_no?: string })?.po_no) === id,
      );
    if (!doc) return json({ detail: "PO tidak ditemukan" }, 404);
    // Selaras backend _serialize_po_doc: top-level customer dari header.customer.
    const header = (doc.header || {}) as Record<string, unknown>;
    const items = Array.isArray(header.items) ? header.items : [];
    return json({
      ...doc,
      po_no: header.po_no || doc.po_no,
      customer: header.customer ?? null,
      item_count: items.length,
    });
  }

  // --- Master / tasks ---
  if (path === "/api/master/task-types" && method === "GET") {
    return json(SIM_TASK_TYPES);
  }
  if (path === "/api/master/role-actions" && method === "GET") {
    return json(SIM_ROLE_ACTIONS);
  }
  if (path === "/api/master/products" && method === "GET") {
    return json({
      items: [
        {
          item_code: "CUP-12OZ",
          item_name: "Cup 12oz Cetak",
          screen_size: "Besar",
        },
      ],
    });
  }
  if (path === "/api/tasks" && method === "GET") {
    return json(filterTasks(url));
  }
  if (path.startsWith("/api/tasks/") && path.endsWith("/confirmations") && method === "GET") {
    const key = path.slice("/api/tasks/".length, -"/confirmations".length);
    const task = findTask(key);
    if (!task) return json({ detail: "Tugas tidak ditemukan" }, 404);
    return json({
      task_key: task.task_key,
      items: simConfirmations.get(task.task_key) || [],
    });
  }
  if (path.startsWith("/api/tasks/") && path.endsWith("/confirm") && method === "POST") {
    const key = path.slice("/api/tasks/".length, -"/confirm".length);
    let body: { qty?: number; idempotency_key?: string; note?: string } = {};
    try {
      body = JSON.parse(String(init?.body || "{}")) as typeof body;
    } catch {
      body = {};
    }
    const result = applyConfirm(
      key,
      Number(body.qty || 0),
      String(body.idempotency_key || `sim:${Date.now()}`),
      body.note,
    );
    if ("error" in result) return json({ detail: result.error }, result.status);
    return json(result);
  }

  // Production qty confirmations
  if (
    (path.includes("/preparation") ||
      path.includes("/qc-packing") ||
      path.includes("/screen") ||
      path.includes("/print")) &&
    method === "POST"
  ) {
    let body: { qty?: number; idempotency_key?: string } = {};
    try {
      body = JSON.parse(String(init?.body || "{}")) as typeof body;
    } catch {
      body = {};
    }
    const roleHint = path.includes("/screen")
      ? "screener"
      : path.includes("/print")
        ? "operator"
        : path.includes("/qc-packing")
          ? "helper"
          : "helper";
    const task =
      simTasks.find(
        (t) =>
          t.assigned_role === roleHint &&
          t.status !== "selesai" &&
          (t.ref.allocation_id === ALLOC_A || t.ref.accurate_po_id === PO_ID),
      ) || simTasks.find((t) => t.assigned_role === roleHint);
    if (!task) return json({ detail: "Tugas tidak ditemukan" }, 404);
    const result = applyConfirm(
      task.task_key,
      Number(body.qty || 0),
      String(body.idempotency_key || `sim:${Date.now()}`),
    );
    if ("error" in result) return json({ detail: result.error }, result.status);
    return json(result);
  }
  if (path.includes("/samples/evidence/") && path.endsWith("/content") && method === "GET") {
    const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    return new Response(bytes, {
      status: 200,
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": "private, no-store",
      },
    });
  }
  if (path.includes("/samples/confirm") && method === "POST") {
    return json({
      sample: {
        sample_id: "sample-draft",
        allocation_id: ALLOC_A,
        version: 2,
        filename: "upload-sim.jpg",
        evidence_id: "evid:sample:sim:v2",
        status: "menunggu_approval",
        submitted_by: SIM_ACTOR,
        submitted_at: new Date().toISOString(),
      },
      duplicate: false,
      phase: "menunggu_approval",
    });
  }
  if (path.includes("/samples") && method === "GET") {
    return json({
      allocation_id: ALLOC_A,
      items: [
        {
          sample_id: "sample-1",
          allocation_id: ALLOC_A,
          version: 1,
          filename: "sample-v1.jpg",
          evidence_id: "evid:sample:sim:v1",
          status: "ditolak",
          submitted_by: "operator@eternal.local",
          submitted_at: "2026-11-18T09:00:00Z",
          reject_reason: "Warna kurang tajam",
        },
        {
          sample_id: "sample-draft",
          allocation_id: ALLOC_A,
          version: 2,
          filename: "upload-sim.jpg",
          evidence_id: "evid:sample:sim:v2",
          status: "draft",
          submitted_by: SIM_ACTOR,
          submitted_at: null,
        },
      ],
    });
  }
  if (path.includes("/samples") && method === "POST") {
    return json({
      sample: {
        sample_id: "sample-new",
        allocation_id: ALLOC_A,
        version: 3,
        filename: "upload-sim.jpg",
        evidence_id: "evid:sample:sim:v3",
        status: "draft",
        submitted_by: SIM_ACTOR,
        submitted_at: null,
      },
      phase: "draft",
    });
  }
  if (path.includes("/samples/decision") && method === "POST") {
    return json({
      sample_id: "sample-1",
      allocation_id: ALLOC_A,
      version: 1,
      filename: "sample-v1.jpg",
      status: "disetujui",
      submitted_by: "screener@eternal.local",
      submitted_at: "2026-11-18T09:00:00Z",
    });
  }

  // Scheduling
  if (path === "/api/scheduling/operators" && method === "GET") {
    return json({
      items: [
        {
          id: "sim-op-1",
          email: "operator@eternal.local",
          display_name: "Operator Cetak",
        },
        {
          id: "sim-op-2",
          email: "operator2@eternal.local",
          display_name: "Operator Cadangan",
        },
      ],
    });
  }
  if (path.startsWith("/api/scheduling/") && method === "GET") {
    if (path.includes("/lines/")) {
      const lineId = path.split("/lines/")[1]?.split("/")[0];
      const line = SIM_SCHEDULE_LINES.lines.find(
        (l) => l.accurate_line_id === lineId,
      );
      if (!line) return json({ detail: "Line tidak ditemukan" }, 404);
      return json(line);
    }
    const poId = decodeURIComponent(path.slice("/api/scheduling/".length).split("/")[0] || "");
    if (poId && poId !== PO_ID) {
      return json({ accurate_po_id: poId, po_no: poId, lines: [] });
    }
    // Selaras backend: hanya item Cetak masuk jadwal penjadwalan.
    return json({
      ...SIM_SCHEDULE_LINES,
      lines: SIM_SCHEDULE_LINES.lines.filter(
        (l) => (l as { process_kind?: string }).process_kind === "Cetak",
      ),
    });
  }
  if (path.includes("/allocations") && method === "POST") {
    const line = SIM_SCHEDULE_LINES.lines[0];
    const body = (init?.body ? JSON.parse(String(init.body)) : {}) as {
      allocations?: Array<{
        operator_email?: string;
        qty?: number;
        print_color?: string;
        screen_kind?: string;
      }>;
      schedule_date?: string;
    };
    const entry = body.allocations?.[0];
    return json({
      allocations: [
        {
          allocation_id: `alloc-sim-${Date.now()}`,
          accurate_po_id: line?.accurate_po_id || PO_ID,
          po_no: line?.po_no || PO_NO,
          accurate_line_id: line?.accurate_line_id || LINE_CETAK,
          item_code: line?.item_code || "CUP-12OZ",
          operator_email: entry?.operator_email || "operator@eternal.local",
          operator_display_name:
            entry?.operator_email === "operator2@eternal.local"
              ? "Operator Cadangan"
              : "Operator Cetak",
          qty: entry?.qty ?? 1000,
          status: "active",
          schedule_date: body.schedule_date || "2026-11-21",
          qty_printed: 0,
          print_color: entry?.print_color ?? null,
          screen_kind: entry?.screen_kind ?? null,
        },
      ],
      duplicate: false,
    });
  }
  if (path.includes("/allocations/") && path.endsWith("/qty") && method === "PUT") {
    const line = SIM_SCHEDULE_LINES.lines[0];
    const alloc = line?.allocations?.[0];
    const body = (init?.body ? JSON.parse(String(init.body)) : {}) as {
      qty?: number;
      reason?: string;
    };
    const printed = alloc?.qty_printed || 0;
    const qtyNew = Number(body.qty);
    if (!body.reason?.trim()) {
      return json({ detail: "Alasan revisi wajib diisi." }, 422);
    }
    if (!Number.isFinite(qtyNew) || qtyNew < 0 || qtyNew !== Math.trunc(qtyNew)) {
      return json({ detail: "Qty alokasi harus bilangan bulat." }, 422);
    }
    if (qtyNew < printed) {
      return json(
        {
          detail: {
            code: "ALLOCATION_BELOW_CONFIRMED_PRINT",
            detail:
              "Qty alokasi tidak boleh lebih kecil dari Qty cetak yang sudah dikonfirmasi operator.",
          },
        },
        409,
      );
    }
    const qtyTarget = line?.qty_target ?? 5000;
    if (qtyNew > qtyTarget) {
      return json(
        {
          detail: `Total alokasi ${qtyNew} melampaui Qty item line ${qtyTarget}.`,
        },
        422,
      );
    }
    if (alloc) {
      alloc.qty = qtyNew;
      alloc.qty_revise_min = printed;
      alloc.qty_revise_max = qtyTarget;
      alloc.can_revise = true;
      if (line) {
        line.qty_allocated = qtyNew;
        line.qty_unallocated = Math.max(qtyTarget - qtyNew, 0);
        line.qty_printed = printed;
      }
    }
    return json({
      allocation_id: ALLOC_A,
      accurate_po_id: alloc?.accurate_po_id || PO_ID,
      po_no: alloc?.po_no || PO_NO,
      accurate_line_id: alloc?.accurate_line_id || LINE_CETAK,
      item_code: alloc?.item_code || "CUP-12OZ",
      operator_email: alloc?.operator_email || "operator-sim@eternal.local",
      operator_display_name: alloc?.operator_display_name || "Operator Cetak",
      qty: qtyNew,
      status: "active",
      schedule_date: alloc?.schedule_date || "2026-11-20",
      qty_printed: printed,
      qty_revise_min: printed,
      qty_revise_max: qtyTarget,
      can_revise: true,
      print_color: alloc?.print_color ?? null,
      screen_kind: alloc?.screen_kind ?? null,
    });
  }

  // Delivery
  if (path.startsWith("/api/delivery/schedule") && method === "GET") {
    return json(SIM_DELIVERY_SCHEDULE);
  }
  if (
    path.includes("/api/delivery/") &&
    path.includes("/evidence/") &&
    path.endsWith("/content") &&
    method === "GET"
  ) {
    const bytes = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);
    return new Response(bytes, {
      status: 200,
      headers: {
        "Content-Type": "image/jpeg",
        "Cache-Control": "private, no-store",
      },
    });
  }
  if (
    path.startsWith("/api/design/") &&
    path.includes("/evidence/") &&
    path.endsWith("/content") &&
    method === "GET"
  ) {
    const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    return new Response(bytes, {
      status: 200,
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": "private, no-store",
      },
    });
  }
  if (path.match(/^\/api\/delivery\/[^/]+$/) && method === "GET") {
    const sj = SIM_DELIVERY_SCHEDULE.items[0];
    return json({
      delivery_note: sj,
      loading: null,
      loadings: [],
      revisions: [],
      events: [
        {
          attempt: 1,
          status: sj.delivery_status || "pending",
          courier_email: sj.driver_email || SIM_ACTOR,
          recipient_name: sj.recipient_name || null,
          failure_reason: null,
          photo_evidence_ids: [],
          created_at: "2026-11-19T10:00:00Z",
        },
      ],
      evidence: { photos: [], signatures: [] },
    });
  }
  if (path.includes("/loading-validation") && method === "GET") {
    return json({
      accurate_sj_id: SIM_DELIVERY_SCHEDULE.items[0].accurate_sj_id,
      sj_no: SIM_DELIVERY_SCHEDULE.items[0].sj_no,
      sj_version: 1,
      revision_status: "current",
      needs_reloading: false,
      delivered: false,
      active_loading_id: "load-1",
      all_matched: true,
      already_loaded: true,
      items: [
        {
          sj_line_id: "sjl-1",
          accurate_line_id: "L-100",
          item_code: "CUP-12OZ",
          qty: 2000,
          unit: "PCS",
          process_kind: "Cetak",
          qty_available: 2000,
          matched: true,
          reason: null,
        },
      ],
    });
  }
  if (
    (path.includes("/loading") ||
      path.includes("/delivered") ||
      path.includes("/failed") ||
      path.includes("/coordination") ||
      path.includes("/discrepancy") ||
      path.includes("/evidence")) &&
    method === "POST"
  ) {
    return json({ ok: true, simulation: true });
  }
  if (path.includes("/revisions") && method === "GET") {
    return json({ items: [] });
  }

  // Analytics
  if (path === "/api/buffer-stock" && method === "GET") return json(SIM_BUFFER);
  if (path.startsWith("/api/production-flow") && method === "GET") {
    return json(SIM_FLOW);
  }
  if (path === "/api/dashboard" && method === "GET") return json(SIM_DASHBOARD);
  if (path.startsWith("/api/reports/") && method === "GET") {
    const kind = path.split("/")[3];
    return json({
      kind,
      items: [{ po_no: "SO-2026/11/001", status: "berjalan", qty: 800 }],
      total: 1,
    });
  }
  if (path === "/api/reports" && method === "GET") {
    return json({ kinds: ["po", "tasks", "deliveries"] });
  }

  // Settings
  if (path === "/api/settings/catalog" && method === "GET") {
    return json({
      module_permissions: SIM_USER.permissions,
      sensitive_module_permissions: ["manage_roles"],
      operational_roles: SIM_TASK_TYPES.roles,
      workflow_policies: {
        items: [
          {
            id: "design-gate",
            label: "Gate Desain",
            status: "active",
            note: "Wajib sebelum jadwal Cetak",
          },
        ],
      },
    });
  }
  if (path === "/api/settings/users" && method === "GET") {
    return json({
      items: [
        {
          id: SIM_USER.id,
          email: SIM_USER.email,
          display_name: SIM_USER.display_name,
          role: SIM_USER.role,
          operational_role: null,
          branch_ids: ["HQ"],
          is_active: true,
          effective_module_permissions: SIM_USER.permissions,
        },
      ],
    });
  }
  if (path === "/api/settings/users" && method === "POST") {
    const body = (init?.body ? JSON.parse(String(init.body)) : {}) as {
      email?: string;
      display_name?: string;
      role?: string;
      operational_role?: string | null;
      branch_ids?: string[];
    };
    return json({
      id: "sim-new-user",
      email: body.email || "new@eternal.local",
      display_name: body.display_name || "User Baru",
      role: body.role || "user",
      operational_role: body.operational_role ?? "operator",
      branch_ids: Array.isArray(body.branch_ids) ? body.branch_ids : [],
      is_active: true,
      effective_module_permissions: [],
    });
  }
  if (path.startsWith("/api/settings/users/") && method === "PATCH") {
    const body = (init?.body ? JSON.parse(String(init.body)) : {}) as {
      display_name?: string;
      role?: string;
      operational_role?: string | null;
      branch_ids?: string[];
    };
    return json({
      id: path.split("/").pop(),
      email: SIM_USER.email,
      display_name: body.display_name || SIM_USER.display_name,
      role: body.role || SIM_USER.role,
      operational_role: body.operational_role ?? null,
      branch_ids: Array.isArray(body.branch_ids) ? body.branch_ids : ["HQ"],
      is_active: true,
      effective_module_permissions: SIM_USER.permissions,
    });
  }
  if (
    path.startsWith("/api/settings/branches/") &&
    path.endsWith("/active") &&
    method === "POST"
  ) {
    const code = decodeURIComponent(path.split("/")[4] || "HQ");
    const body = (init?.body ? JSON.parse(String(init.body)) : {}) as {
      is_active?: boolean;
    };
    return json({
      code,
      name: code === "HQ" ? "Head Office" : code,
      is_active: Boolean(body.is_active),
      accurate_branch_id: null,
      mapping_status: "unmapped",
      accurate_verified: false,
    });
  }
  if (
    path.startsWith("/api/settings/branches/") &&
    path.endsWith("/accurate-mapping") &&
    method === "PUT"
  ) {
    const code = decodeURIComponent(path.split("/")[4] || "HQ");
    const body = (init?.body ? JSON.parse(String(init.body)) : {}) as {
      accurate_branch_id?: string | null;
      reason?: string;
    };
    const aid =
      body.accurate_branch_id != null && String(body.accurate_branch_id).trim()
        ? String(body.accurate_branch_id).trim()
        : null;
    return json({
      code,
      name: code === "HQ" ? "Head Office" : code === "YGY" ? "Yogyakarta" : code,
      is_active: true,
      accurate_branch_id: aid,
      mapping_status: aid ? "mapped" : "unmapped",
      accurate_verified: false,
    });
  }
  if (path === "/api/settings/roles" && method === "GET") {
    return json({
      items: [
        {
          role_key: "super_admin",
          label: "Super Admin",
          kind: "system",
          module_permissions: SIM_USER.permissions,
          is_active: true,
          version: 1,
        },
        {
          role_key: "admin",
          label: "Admin",
          kind: "system",
          module_permissions: SIM_USER.permissions,
          is_active: true,
          version: 1,
        },
      ],
    });
  }
  if (path === "/api/settings/branches" && method === "GET") {
    return json({
      items: [
        {
          code: "HQ",
          name: "Head Office",
          is_active: true,
          accurate_branch_id: null,
          mapping_status: "unmapped",
          accurate_verified: false,
        },
        {
          code: "YGY",
          name: "Yogyakarta",
          is_active: true,
          accurate_branch_id: null,
          mapping_status: "unmapped",
          accurate_verified: false,
        },
      ],
    });
  }
  if (path === "/api/settings/branches" && method === "PUT") {
    const body = (init?.body ? JSON.parse(String(init.body)) : {}) as {
      code?: string;
      name?: string;
      is_active?: boolean;
    };
    return json({
      code: String(body.code || "HQ").toUpperCase(),
      name: body.name || "Cabang",
      is_active: body.is_active !== false,
      accurate_branch_id: null,
      mapping_status: "unmapped",
      accurate_verified: false,
    });
  }
  if (path === "/api/settings/machines" && method === "GET") {
    return json({
      items: [{ code: "M1", label: "Mesin 1", branch_code: "HQ" }],
    });
  }
  if (path === "/api/settings/shifts" && method === "GET") {
    return json({
      items: [
        {
          code: "Pagi",
          label: "Pagi",
          start_time: "08:00",
          end_time: "16:00",
        },
      ],
    });
  }
  if (path === "/api/settings/products" && method === "GET") {
    return json({
      items: [
        {
          item_code: "CUP-12OZ",
          item_name: "Cup 12oz Cetak",
          screen_size: "Besar",
        },
      ],
    });
  }
  if (path === "/api/settings/accurate" && method === "GET") {
    return json({
      configured: { account: false, token: false, signature: false },
      sj_live_sync_enabled: false,
      note: "Simulasi — Accurate sync OFF",
    });
  }
  if (path === "/api/settings/audit" && method === "GET") {
    return json({
      items: [
        {
          action: "settings.role.permissions",
          actor_email: SIM_ACTOR,
          created_at: "2026-11-19T10:00:00Z",
          detail: { simulation: true },
        },
      ],
      total: 1,
    });
  }
  if (path === "/api/settings/workflow-policies" && method === "GET") {
    return json({
      items: [
        {
          id: "design-gate",
          label: "Gate Desain",
          status: "active",
          note: "Wajib",
        },
      ],
    });
  }
  if (path.startsWith("/api/settings/") && method !== "GET") {
    return json({ ok: true, simulation: true });
  }

  // Tracking — mutable per-PO (simulasi lokal; bukan Mongo production).
  // `path` on list items previews Owner-approved recoverable storage — not production.
  if (path.startsWith("/api/tracking/") && method === "GET") {
    const poKey = extractTrackingPoKey(path);
    if (path.endsWith("/links")) {
      return json({ items: getSimTrackingLinks(poKey), accurate_po_id: poKey });
    }
    return json({
      ...SIM_TRACKING,
      order: { ...SIM_TRACKING.order, accurate_po_id: poKey },
      links: getSimTrackingLinks(poKey),
    });
  }
  if (path.startsWith("/api/tracking/") && method === "POST") {
    if (path.includes("/revoke")) {
      hydrateSimTrackingStore();
      for (const [poKey, links] of simTrackingLinksByPo) {
        const next = links.map((l) =>
          path.includes(l.id)
            ? { ...l, status: "revoked" as const, revoked_at: new Date().toISOString(), path: undefined }
            : l,
        );
        setSimTrackingLinks(poKey, next);
      }
      return json({ ok: true, revoked: true });
    }
    const poKey = extractTrackingPoKey(path);
    const isRegen = path.includes("/regenerate");
    const existing = getSimTrackingLinks(poKey);
    const activeExisting = existing.find((l) => l.status === "active");

    // Generate when active already exists → reject (no replacement link).
    if (!isRegen && activeExisting) {
      return json(
        { detail: "Link aktif sudah ada untuk PO ini." },
        409,
      );
    }

    if (isRegen) {
      const revoked = existing.map((l) =>
        l.status === "active"
          ? {
              ...l,
              status: "revoked" as const,
              revoked_at: new Date().toISOString(),
              path: undefined,
            }
          : l,
      );
      setSimTrackingLinks(poKey, revoked);
    }
    const token = `SIMNEW${poKey.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().padEnd(6, "X").slice(0, 6)}${Date.now().toString(36).slice(-8).toUpperCase()}`;
    const customer_slug = "pt-contoh-kemasan";
    const po_slug = `po-${poKey}`;
    const publicPath = `/t/${customer_slug}/${po_slug}/${token}`;
    const link: SimTrackingLink = {
      id: `link-sim-${poKey}-${Date.now().toString(36)}`,
      accurate_po_id: poKey,
      po_no: `SO-${poKey}`,
      customer_name: "PT Contoh Kemasan",
      customer_slug,
      po_slug,
      status: "active",
      scopes: ["progress", "documents"],
      created_at: new Date().toISOString(),
      created_by: SIM_ACTOR,
      revoked_at: null,
      expires_at: null,
      access_count: 0,
      last_accessed_at: null,
      path: publicPath,
    };
    const prev = getSimTrackingLinks(poKey);
    setSimTrackingLinks(poKey, [
      ...prev.filter((l) => l.status !== "active"),
      link,
    ]);
    return json({
      token,
      path: publicPath,
      link,
      accurate_po_id: poKey,
      po_no: link.po_no,
      customer_slug,
      po_slug,
      revoked_previous: isRegen
        ? existing.filter((l) => l.status === "active").length
        : 0,
    });
  }
  if (path.startsWith("/api/public/tracking/") && method === "GET") {
    return json({ ...SIM_TRACKING, view: "customer", links: undefined });
  }

  // Workflow stubs
  if (
    path.includes("/api/design/") &&
    path.includes("/evidence/") &&
    path.endsWith("/content") &&
    method === "GET"
  ) {
    // Simulasi: tidak ada blob desain — empty state jujur (bukan preview palsu).
    return json({ detail: "File desain tidak tersedia di simulasi." }, 404);
  }
  if (path.startsWith("/api/design/") && method === "GET") {
    return json({
      accurate_po_id: PO_ID,
      gates: { design: { state: "open" } },
      submissions: [
        {
          accurate_po_id: PO_ID,
          po_no: PO_NO,
          version: 1,
          evidence_id: "evid:design:sim:v1",
          filename: "desain-cup.png",
          submitted_by: "designer@eternal.local",
          submitted_at: "2026-11-18T09:00:00Z",
          confirmed_at: "2026-11-18T10:00:00Z",
          confirmed_by: "designer@eternal.local",
        },
      ],
      latest: {
        accurate_po_id: PO_ID,
        po_no: PO_NO,
        version: 1,
        evidence_id: "evid:design:sim:v1",
        filename: "desain-cup.png",
        submitted_by: "designer@eternal.local",
        submitted_at: "2026-11-18T09:00:00Z",
        confirmed_at: "2026-11-18T10:00:00Z",
        confirmed_by: "designer@eternal.local",
      },
      approval: null,
    });
  }
  if (path.startsWith("/api/payment/") && method === "GET") {
    return json({
      accurate_po_id: PO_ID,
      gates: { payment: { state: "review" } },
      receipts: [],
      bypass: null,
    });
  }
  if (
    (path.startsWith("/api/design/") ||
      path.startsWith("/api/payment/") ||
      path.startsWith("/api/workflow/") ||
      path.startsWith("/api/evidence/")) &&
    method !== "GET"
  ) {
    return json({ ok: true, simulation: true });
  }
  if (path.startsWith("/api/workflow/") && method === "GET") {
    return json({
      gates: {
        design: { state: "closed" },
        payment: { state: "review" },
      },
    });
  }

  // Table preferences
  if (path.startsWith("/api/me/table-preferences/") && method === "GET") {
    return json({
      table_id: path.split("/").pop(),
      column_order: [],
      hidden_columns: [],
      filters: {},
    });
  }
  if (path.startsWith("/api/me/table-preferences/") && method === "PUT") {
    return json({
      table_id: path.split("/").pop(),
      column_order: [],
      hidden_columns: [],
      filters: {},
    });
  }
  if (path.startsWith("/api/me/table-preferences/") && method === "DELETE") {
    return json({ ok: true });
  }

  // Fallback: avoid leaking to incomplete local backend
  return json(
    {
      detail: `Simulasi API: endpoint belum dimock (${method} ${path})`,
      simulation: true,
    },
    501,
  );
}
