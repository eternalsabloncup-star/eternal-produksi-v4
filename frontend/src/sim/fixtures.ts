/**
 * Fixture shapes mirror production API contracts.
 * Imported only by sim handlers (DEV + VITE_API_SIMULATION).
 */

import type { OperationalRole, OperationalTask } from "../api/operational";

export const SIM_CSRF = "sim-csrf-eternal-v4";
export const SIM_ACTOR = "owner-sim@eternal.local";

export const SIM_USER = {
  id: "sim-user-owner",
  email: SIM_ACTOR,
  display_name: "Owner Simulasi",
  role: "super_admin",
  operational_role: null as string | null,
  branch_ids: ["HQ"],
  permissions: [
    "manage_users",
    "manage_roles",
    "manage_settings",
    "operations_full",
    "view_reports",
    "manage_tracking",
  ],
};

export const SIM_CAPABILITIES: Record<string, boolean> = {
  manage_users: true,
  manage_roles: true,
  manage_settings: true,
  operations_full: true,
  view_reports: true,
  manage_tracking: true,
};

const PO_ID = "10001";
const PO_NO = "SO-2026/11/001";
const LINE_CETAK = "L-100";
const LINE_NON = "L-200";
const ALLOC_A = "alloc-sim-1";
const SJ_ID = "sj-9001";

export const SIM_PO_DOCS: Array<Record<string, unknown>> = [
  {
    accurate_id: PO_ID,
    origin: "accurate",
    header: {
      accurate_id: PO_ID,
      po_no: PO_NO,
      customer_name: "PT Contoh Kemasan",
      customer_no: "C-001",
      po_date: "2026-11-10",
      ship_date: "2026-11-25",
      salesman_name: "Sales Demo",
      branch_name: "Jakarta",
      status: "PO Baru",
      items: [
        {
          line_id: LINE_CETAK,
          accurate_line_id: LINE_CETAK,
          item_code: "CUP-12OZ",
          item_name: "Cup 12oz Cetak",
          qty: 5000,
          unit: "PCS",
          process_kind: "Cetak",
          process_raw: "Cetak",
          color: "Putih",
        },
        {
          line_id: LINE_NON,
          accurate_line_id: LINE_NON,
          item_code: "LID-12",
          item_name: "Lid 12oz",
          qty: 5000,
          unit: "PCS",
          process_kind: "Non-Cetak",
          process_raw: "Non-Cetak",
          color: "Natural",
        },
      ],
    },
    acceptance: { status: "pending", inbox_status: "PO Baru" },
    operational: { inbox_status: "PO Baru" },
  },
  {
    accurate_id: "10002",
    origin: "accurate",
    header: {
      accurate_id: "10002",
      po_no: "SO-2026/11/002",
      customer_name: "CV Outlet Timur",
      customer_no: "C-014",
      po_date: "2026-11-12",
      ship_date: "2026-11-28",
      salesman_name: "Sales Demo",
      branch_name: "Jakarta",
      status: "PO Diterima",
      items: [
        {
          line_id: "L-300",
          accurate_line_id: "L-300",
          item_code: "CUP-16OZ",
          item_name: "Cup 16oz Cetak",
          qty: 2000,
          unit: "PCS",
          process_kind: "Cetak",
          process_raw: "Cetak",
          color: "Hitam",
        },
      ],
    },
    acceptance: {
      status: "accepted",
      accepted_by: SIM_ACTOR,
      accepted_at: "2026-11-12T08:00:00Z",
      inbox_status: "PO Diterima",
    },
    operational: { inbox_status: "PO Diterima" },
  },
];

function task(
  partial: Omit<OperationalTask, "qty_confirmed"> & { qty_confirmed?: number },
): OperationalTask {
  return { qty_confirmed: 0, ...partial };
}

/** Mutable task list — confirm handlers update in place. */
export let simTasks: OperationalTask[] = [
  task({
    task_key: `po:${PO_ID}:penjadwalan`,
    task_type: "penjadwalan",
    label: "Penjadwalan Produksi",
    scope: "po",
    assigned_role: "production-head",
    status: "belum_dikerjakan",
    qty_target: 5000,
    ref: { accurate_po_id: PO_ID, po_no: PO_NO },
  }),
  task({
    task_key: `po:${PO_ID}:design`,
    task_type: "design",
    label: "Desain",
    scope: "po",
    assigned_role: "designer",
    status: "belum_dikerjakan",
    qty_target: 1,
    ref: { accurate_po_id: PO_ID, po_no: PO_NO },
  }),
  task({
    task_key: `po:${PO_ID}:line:${LINE_CETAK}:persiapan-cup`,
    task_type: "persiapan-cup",
    label: "Persiapan Cup",
    scope: "po_line",
    assigned_role: "helper",
    status: "belum_dikerjakan",
    qty_target: 5000,
    ref: {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      accurate_line_id: LINE_CETAK,
      item_code: "CUP-12OZ",
      process_kind: "Cetak",
    },
  }),
  task({
    task_key: `alloc:${ALLOC_A}:screen`,
    task_type: "screen",
    label: "Screen",
    scope: "po_line_allocation",
    assigned_role: "screener",
    status: "belum_dikerjakan",
    qty_target: 2500,
    ref: {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      accurate_line_id: LINE_CETAK,
      allocation_id: ALLOC_A,
      item_code: "CUP-12OZ",
      process_kind: "Cetak",
    },
  }),
  task({
    task_key: `alloc:${ALLOC_A}:cetak`,
    task_type: "cetak",
    label: "Cetak",
    scope: "po_line_allocation",
    assigned_role: "operator",
    status: "sebagian",
    qty_target: 2500,
    qty_confirmed: 800,
    ref: {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      accurate_line_id: LINE_CETAK,
      allocation_id: ALLOC_A,
      item_code: "CUP-12OZ",
      process_kind: "Cetak",
    },
  }),
  task({
    task_key: `po:${PO_ID}:line:${LINE_CETAK}:qc-packing`,
    task_type: "qc-packing",
    label: "QC & Packing",
    scope: "po_line",
    assigned_role: "helper",
    status: "belum_dikerjakan",
    qty_target: 5000,
    ref: {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      accurate_line_id: LINE_CETAK,
      item_code: "CUP-12OZ",
    },
  }),
  task({
    task_key: `sj:${SJ_ID}:pengiriman`,
    task_type: "pengiriman",
    label: "Pengiriman",
    scope: "delivery_note",
    assigned_role: "driver",
    status: "belum_dikerjakan",
    qty_target: 2000,
    ref: {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      accurate_delivery_note_id: SJ_ID,
      sj_no: "SJ-2026/11/88",
    },
  }),
  task({
    task_key: `po:10002:design`,
    task_type: "design",
    label: "Desain",
    scope: "po",
    assigned_role: "designer",
    status: "selesai",
    qty_target: 1,
    qty_confirmed: 1,
    ref: { accurate_po_id: "10002", po_no: "SO-2026/11/002" },
  }),
];

export type SimConfirmation = {
  idempotency_key: string;
  qty: number;
  actor_email: string;
  actor_role: string;
  note?: string | null;
  created_at: string;
};

export const simConfirmations = new Map<string, SimConfirmation[]>();

export const SIM_TASK_TYPES = {
  items: [
    {
      task_type: "penjadwalan",
      label: "Penjadwalan",
      scope: "po",
      assigned_role: "production-head" as OperationalRole,
      actions: ["view", "input", "confirm"] as const,
      coordination: false,
      partial_qty: true,
      is_default_mapping: true,
    },
    {
      task_type: "design",
      label: "Desain",
      scope: "po",
      assigned_role: "designer" as OperationalRole,
      actions: ["view", "input", "confirm", "approve"] as const,
      coordination: false,
      partial_qty: false,
      is_default_mapping: true,
    },
    {
      task_type: "persiapan-cup",
      label: "Persiapan Cup",
      scope: "po_line",
      assigned_role: "helper" as OperationalRole,
      actions: ["view", "input", "confirm"] as const,
      coordination: false,
      partial_qty: true,
      is_default_mapping: true,
    },
    {
      task_type: "screen",
      label: "Screen",
      scope: "po_line_allocation",
      assigned_role: "screener" as OperationalRole,
      actions: ["view", "input", "confirm"] as const,
      coordination: false,
      partial_qty: true,
      is_default_mapping: true,
    },
    {
      task_type: "cetak",
      label: "Cetak",
      scope: "po_line_allocation",
      assigned_role: "operator" as OperationalRole,
      actions: ["view", "input", "confirm"] as const,
      coordination: false,
      partial_qty: true,
      is_default_mapping: true,
    },
    {
      task_type: "qc-packing",
      label: "QC & Packing",
      scope: "po_line",
      assigned_role: "helper" as OperationalRole,
      actions: ["view", "input", "confirm"] as const,
      coordination: false,
      partial_qty: true,
      is_default_mapping: true,
    },
    {
      task_type: "pengiriman",
      label: "Pengiriman",
      scope: "delivery_note",
      assigned_role: "driver" as OperationalRole,
      actions: ["view", "input", "confirm", "manage_evidence"] as const,
      coordination: false,
      partial_qty: false,
      is_default_mapping: true,
    },
  ],
  roles: [
    "production-head",
    "designer",
    "helper",
    "screener",
    "operator",
    "driver",
  ] as OperationalRole[],
  actions: [
    "view",
    "input",
    "confirm",
    "approve",
    "edit",
    "delete",
    "manage_evidence",
  ],
};

export const SIM_ROLE_ACTIONS = {
  items: SIM_TASK_TYPES.items.map((t) => ({
    role: t.assigned_role,
    task_type: t.task_type,
    actions: [...t.actions],
    is_default_mapping: true,
  })),
};

export const SIM_SCHEDULE_LINES = {
  accurate_po_id: PO_ID,
  po_no: PO_NO,
  lines: [
    {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      accurate_line_id: LINE_CETAK,
      item_code: "CUP-12OZ",
      item_name: "Cup 12oz Cetak",
      qty_target: 5000,
      qty_allocated: 2500,
      qty_unallocated: 2500,
      qty_printed: 800,
      screen_size: "Besar",
      allocations: [
        {
          allocation_id: ALLOC_A,
          accurate_po_id: PO_ID,
          po_no: PO_NO,
          accurate_line_id: LINE_CETAK,
          item_code: "CUP-12OZ",
          operator_email: "operator@eternal.local",
          qty: 2500,
          status: "berjalan",
          schedule_date: "2026-11-20",
          qty_printed: 800,
        },
      ],
      gates: {
        design: { state: "open" as const },
        payment: { state: "open" as const },
      },
      scheduling_unlocked: true,
    },
    {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      accurate_line_id: LINE_NON,
      item_code: "LID-12",
      item_name: "Lid 12oz",
      qty_target: 5000,
      qty_allocated: 0,
      qty_unallocated: 5000,
      qty_printed: 0,
      screen_size: "",
      allocations: [],
      gates: {
        design: { state: "open" as const, reason: "Non-Cetak" },
        payment: { state: "review" as const, reason: "Menunggu bukti" },
      },
      scheduling_unlocked: false,
    },
  ],
};

export const SIM_DELIVERY_SCHEDULE = {
  items: [
    {
      accurate_sj_id: SJ_ID,
      sj_no: "SJ-2026/11/88",
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      customer_po_no: "PO-CUST-77",
      ship_date: "2026-11-25",
      customer_name: "PT Contoh Kemasan",
      destination: "Gudang Timur — Jakarta",
      shipment: "Kurir Internal",
      items: [
        {
          sj_line_id: "sjl-1",
          accurate_line_id: LINE_CETAK,
          item_code: "CUP-12OZ",
          item_name: "Cup 12oz Cetak",
          qty: 2000,
          unit: "PCS",
        },
      ],
      item_count: 1,
      qty_by_unit: { PCS: 2000 },
      loaded: true,
      loaded_at: "2026-11-19T02:00:00Z",
      loading_id: "load-1",
      needs_reloading: false,
      delivery_status: "siap_dikirim",
      attempts: 0,
      driver_email: "driver@eternal.local",
      recipient_name: "",
    },
  ],
  summary: {
    sj_count: 1,
    qty_by_unit: { PCS: 2000 },
    destination_count: 1,
  },
  source: "api",
};

export const SIM_BUFFER = {
  items: [
    {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      accurate_line_id: LINE_CETAK,
      outlet_warehouse: "Gudang Produksi",
      item_code: "CUP-12OZ",
      item_name: "Cup 12oz Cetak",
      color: "Putih",
      unit: "PCS",
      qty_ordered: 5000,
      qty_printed: 800,
      qty_remaining: 4200,
      qty_ready_warehouse: 800,
      ready_mismatch: false,
      qty_shipped: 0,
      buffer_status: "produksi",
      sales_pic: "Sales Demo",
    },
  ],
  kpi: {
    total_buffer_ordered: 5000,
    qty_printed: 800,
    qty_remaining: 4200,
    qty_ready_warehouse: 800,
    qty_shipped: 0,
    ready_mismatch_rows: 0,
    ready_incomplete_rows: 0,
  },
  not_configured: [],
  ready_formula: "qty_cetak - qty_terkirim_terkonfirmasi",
};

export const SIM_FLOW = {
  items: [
    {
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      has_print_items: true,
      nodes: [
        {
          id: "inbox",
          label: "Inbox",
          status: "selesai" as const,
          detail: "PO masuk",
        },
        {
          id: "design",
          label: "Desain",
          status: "berjalan" as const,
          detail: "Menunggu approval",
        },
        {
          id: "schedule",
          label: "Jadwal",
          status: "menunggu" as const,
          detail: "Belum dialokasi penuh",
        },
        {
          id: "print",
          label: "Cetak",
          status: "berjalan" as const,
          detail: "800 / 2500",
        },
        {
          id: "delivery",
          label: "Kirim",
          status: "menunggu" as const,
          detail: "SJ siap",
        },
      ],
    },
  ],
};

export const SIM_DASHBOARD = {
  po_total: 2,
  po_waiting_design_gate: 1,
  po_waiting_payment_gate: 1,
  lines_ready_to_schedule: 1,
  tasks_open: 6,
  qty_printed_by_unit: { PCS: 800 },
  qty_packed_by_unit: { PCS: 0 },
  sj_total: 1,
  sj_loaded: 1,
  sj_delivered_success: 0,
  sj_delivered_failed: 0,
  limitations: ["Simulasi lokal — bukan Mongo production"],
};

export const SIM_TRACKING = {
  view: "internal" as const,
  generated_at: "2026-11-19T15:00:00Z",
  order: {
    accurate_po_id: PO_ID,
    po_no: PO_NO,
    customer: "PT Contoh Kemasan",
    po_date: "2026-11-10",
    eta_ship: "2026-11-25",
    has_print_items: true,
    status_label: "Produksi berjalan",
    last_updated_at: "2026-11-19T12:00:00Z",
    summary: {
      customer: "PT Contoh Kemasan",
      po_no: PO_NO,
      po_date: "2026-11-10",
      eta_ship: "2026-11-25",
      item_count: 2,
      items: [
        {
          item_code: "CUP-12OZ",
          item_name: "Cup 12oz Cetak",
          qty: 5000,
          unit: "PCS",
          process_kind: "Cetak",
          color: "Putih",
        },
      ],
    },
  },
  progress: {
    milestone: { completed: 3, relevant: 12, ratio: 0.25, label: "3/12" },
    production: {
      qty_po: 5000,
      qty_produced: 800,
      qty_qc_passed: null,
      ratio: 0.16,
      source: "task_cetak",
    },
    shipping: {
      qty_shipped: 0,
      qty_remaining: 5000,
      ratio: 0,
      source: null,
    },
    active_milestone: {
      id: "proses-cetak",
      seq: 8,
      label: "Proses Cetak",
      state: "in_progress" as const,
      detail: "800 PCS terkonfirmasi",
      timestamp: "2026-11-19T10:00:00Z",
      relevant: true,
    },
    order_complete: false,
    completion_blocked_reason: "Qty terkirim belum memenuhi PO",
  },
  milestones: [
    {
      id: "pesanan-diterima",
      seq: 1,
      label: "Pesanan Diterima",
      state: "completed" as const,
      detail: "Sync Accurate",
      timestamp: "2026-11-10T01:00:00Z",
      relevant: true,
    },
    {
      id: "proses-cetak",
      seq: 8,
      label: "Proses Cetak",
      state: "in_progress" as const,
      detail: "800/5000",
      timestamp: "2026-11-19T10:00:00Z",
      relevant: true,
    },
    {
      id: "barang-diterima",
      seq: 14,
      label: "Barang Diterima",
      state: "pending" as const,
      detail: "Belum ada bukti",
      timestamp: null,
      relevant: true,
    },
  ],
  documents: [
    {
      category: "design",
      label: "Desain",
      status: "waiting" as const,
      items: [],
      reason: "Belum diunggah",
    },
  ],
  links: [
    {
      id: "link-sim-1",
      accurate_po_id: PO_ID,
      po_no: PO_NO,
      customer_name: "PT Contoh Kemasan",
      customer_slug: "pt-contoh-kemasan",
      po_slug: "so-2026-11-001",
      status: "active" as const,
      scopes: ["progress", "documents"],
      created_at: "2026-11-18T00:00:00Z",
      created_by: SIM_ACTOR,
      revoked_at: null,
      expires_at: null,
      access_count: 2,
      last_accessed_at: "2026-11-19T08:00:00Z",
    },
  ],
};

export function resetSimState(): void {
  for (const t of simTasks) {
    if (t.task_type === "design" && t.ref.accurate_po_id === "10002") {
      t.status = "selesai";
      t.qty_confirmed = 1;
      continue;
    }
    if (t.task_type === "cetak") {
      t.status = "sebagian";
      t.qty_confirmed = 800;
      continue;
    }
    t.status = "belum_dikerjakan";
    t.qty_confirmed = 0;
  }
  simConfirmations.clear();
}

export { PO_ID, PO_NO, ALLOC_A, SJ_ID, LINE_CETAK };
