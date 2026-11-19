/**
 * Display mapping: Owner production-flow snake labels ← backend customer milestones.
 * Explicit & verifiable — bukan persamaan urutan 1:1.
 * Backend `tracking_milestones.py` / API tidak diubah.
 */

import type { MilestoneState, TrackingMilestone } from "../../api/tracking";

export type SnakeVisualState = "done" | "active" | "wait" | "blocked" | "na";

export type SnakeStageDef = {
  /** Urutan visual 1–15 sesuai screenshot Owner. */
  seq: number;
  label: string;
  /**
   * Sumber milestone API (id). Null = tidak ada kontrak customer-facing
   * → selalu wait + gap.
   */
  sourceIds: readonly string[] | null;
  gap?: string;
};

/**
 * 15 tahap visual Owner (bukan label customer-brief).
 * Mapping ke id milestone publik yang benar-benar dikembalikan API.
 */
export const OWNER_SNAKE_STAGES: readonly SnakeStageDef[] = [
  {
    seq: 1,
    label: "Order Masuk",
    sourceIds: ["order_received"],
  },
  {
    seq: 2,
    label: "Design",
    sourceIds: ["design_prep"],
  },
  {
    seq: 3,
    label: "Approval Design",
    sourceIds: ["design_approved"],
  },
  {
    seq: 4,
    label: "Payment",
    sourceIds: null,
    gap: "Gate payment tidak diekspos ke view customer publik; tetap Menunggu.",
  },
  {
    seq: 5,
    label: "Approval Payment",
    sourceIds: null,
    gap: "Approval payment tidak ada di milestone publik; tetap Menunggu.",
  },
  {
    seq: 6,
    label: "Penjadwalan",
    sourceIds: ["production_scheduled"],
  },
  {
    seq: 7,
    label: "Screener",
    sourceIds: ["print_prep"],
  },
  {
    seq: 8,
    label: "Persiapan",
    sourceIds: ["material_prep"],
  },
  {
    seq: 9,
    label: "Operator",
    sourceIds: null,
    gap: "Tidak ada milestone customer 'operator'; jangan dikarang dari Cetak.",
  },
  {
    seq: 10,
    label: "Konfirmasi Sample",
    sourceIds: null,
    gap: "Approval sampel belum ada di milestone publik tracking.",
  },
  {
    seq: 11,
    label: "Cetak",
    sourceIds: ["printing"],
  },
  {
    seq: 12,
    label: "Packing",
    sourceIds: ["quality_check"],
  },
  {
    seq: 13,
    label: "Surat Jalan",
    sourceIds: ["shipping_prep"],
  },
  {
    seq: 14,
    label: "Loading",
    sourceIds: ["ready_to_ship"],
  },
  {
    seq: 15,
    label: "Pengiriman",
    // Gabungan semantik pengiriman: transit / diterima (bukan order_complete).
    sourceIds: ["in_transit", "delivered"],
  },
] as const;

export type SnakeStageView = {
  seq: number;
  label: string;
  visual: SnakeVisualState;
  detail: string;
  timestamp: string | null;
  gap: string | null;
  sourceIds: readonly string[] | null;
};

function byId(milestones: TrackingMilestone[]): Map<string, TrackingMilestone> {
  return new Map(milestones.map((m) => [m.id, m]));
}

function toVisual(state: MilestoneState): SnakeVisualState {
  switch (state) {
    case "completed":
      return "done";
    case "in_progress":
      return "active";
    case "attention_required":
      return "blocked";
    case "not_applicable":
      return "na";
    case "unknown":
      return "wait";
    default:
      return "wait";
  }
}

function mergeStates(states: MilestoneState[]): MilestoneState {
  if (states.some((s) => s === "attention_required")) return "attention_required";
  if (states.some((s) => s === "completed") && states.every((s) => s === "completed" || s === "not_applicable"))
    return "completed";
  if (states.some((s) => s === "completed" || s === "in_progress")) {
    if (states.every((s) => s === "completed" || s === "not_applicable")) return "completed";
    return "in_progress";
  }
  if (states.length > 0 && states.every((s) => s === "not_applicable")) return "not_applicable";
  return "pending";
}

/** Bangun 15 node snake dari payload milestones API. */
export function buildSnakeStages(milestones: TrackingMilestone[]): SnakeStageView[] {
  const map = byId(milestones);
  return OWNER_SNAKE_STAGES.map((def) => {
    if (!def.sourceIds || def.sourceIds.length === 0) {
      return {
        seq: def.seq,
        label: def.label,
        visual: "wait" as const,
        detail: "Belum tersedia — sumber backend belum terpapar ke tracking publik.",
        timestamp: null,
        gap: def.gap ?? null,
        sourceIds: def.sourceIds,
      };
    }

    const hits = def.sourceIds
      .map((id) => map.get(id))
      .filter((m): m is TrackingMilestone => Boolean(m));

    if (hits.length === 0) {
      return {
        seq: def.seq,
        label: def.label,
        visual: "wait",
        detail: "Belum tersedia.",
        timestamp: null,
        gap: `Milestone sumber ${def.sourceIds.join(",")} tidak ada di respons API.`,
        sourceIds: def.sourceIds,
      };
    }

    // Pengiriman: delivered completed menang; else in_transit.
    if (def.sourceIds.includes("delivered") && def.sourceIds.includes("in_transit")) {
      const delivered = map.get("delivered");
      const transit = map.get("in_transit");
      if (delivered?.state === "completed") {
        return {
          seq: def.seq,
          label: def.label,
          visual: "done",
          detail: delivered.detail,
          timestamp: delivered.timestamp,
          gap: null,
          sourceIds: def.sourceIds,
        };
      }
      const chosen = transit ?? delivered!;
      return {
        seq: def.seq,
        label: def.label,
        visual: toVisual(chosen.state),
        detail: chosen.detail,
        timestamp: chosen.timestamp,
        gap: null,
        sourceIds: def.sourceIds,
      };
    }

    const merged = mergeStates(hits.map((h) => h.state));
    const primary =
      hits.find((h) => h.state === "in_progress" || h.state === "attention_required") ||
      hits.find((h) => h.state === "completed") ||
      hits[0];

    return {
      seq: def.seq,
      label: def.label,
      visual: toVisual(merged),
      detail: primary.detail,
      timestamp: primary.timestamp,
      gap: def.gap ?? null,
      sourceIds: def.sourceIds,
    };
  });
}

export type OwnerDocSlot = {
  key: string;
  title: string;
  apiCategories: string[];
  gap?: string;
};

/** Dokumen Owner 3+2 ← kategori API (tanpa URL palsu). */
export const OWNER_DOC_SLOTS: OwnerDocSlot[] = [
  {
    key: "design",
    title: "Design",
    apiCategories: [],
    gap: "Dokumen desain belum ada di kontrak dokumen tracking publik.",
  },
  {
    key: "payment",
    title: "Pembayaran",
    apiCategories: ["sales_receipt"],
  },
  {
    key: "invoice",
    title: "Faktur Penjualan",
    apiCategories: ["sales_invoice"],
  },
  {
    key: "sj",
    title: "Surat Jalan",
    apiCategories: ["delivery_note"],
  },
  {
    key: "ship",
    title: "Pengiriman",
    apiCategories: ["delivery_proof"],
  },
];

export function formatItemLine(
  items: Array<{ item_name: string; color?: string; qty?: number; unit?: string }>,
): string {
  if (!items.length) return "—";
  const first = items[0];
  const color = (first.color || "").trim();
  const base = color ? `${first.item_name} · ${color}` : first.item_name;
  if (items.length === 1) return base;
  return `${base} (+${items.length - 1})`;
}
