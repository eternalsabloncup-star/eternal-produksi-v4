/**
 * Pure view helpers for public Live Tracking (customer-facing).
 * No hardcoded customer/PO data from design screenshots.
 */

import type { MilestoneState } from "../../api/tracking";
import { MILESTONE_STATE_LABEL } from "../../api/tracking";

/** Customer card label from order.summary.item_count. */
export function formatItemCountLabel(count: number | null | undefined): string {
  const n =
    typeof count === "number" && Number.isFinite(count)
      ? Math.max(0, Math.floor(count))
      : 0;
  return `${n} Item Pesanan`;
}

/**
 * Estimasi selesai for the PO card.
 * Prefer eta_ship; never invent dates from screenshots.
 */
export function formatEstimasiLabel(
  etaShip: string | null | undefined,
  fallback?: string | null,
): string {
  const raw = (etaShip || "").trim();
  if (!raw) {
    const fb = (fallback || "").trim();
    return fb || "Belum tersedia";
  }
  const ymd = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (ymd) return `${ymd[3]}/${ymd[2]}/${ymd[1]}`;
  const d = new Date(raw);
  if (!Number.isNaN(d.getTime())) {
    return new Intl.DateTimeFormat("id-ID", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      timeZone: "Asia/Jakarta",
    }).format(d);
  }
  return raw;
}

export function milestoneStateLabel(state: MilestoneState | string): string {
  if (state in MILESTONE_STATE_LABEL) {
    return MILESTONE_STATE_LABEL[state as MilestoneState];
  }
  return String(state || "—");
}

/** Strings that must never appear in customer-facing Live Tracking UI. */
export const FORBIDDEN_CUSTOMER_TECHNICAL_SNIPPETS = [
  "dari API",
  "KPI & ringkasan Qty",
  "Catatan Qty:",
  "Gap mapping snake",
  "process_kind",
  "backend",
] as const;
