import type { PoRow } from "../demo/types";

/**
 * Identitas PO untuk API Live Tracking.
 * Utamakan Accurate ID; jangan pakai fixture hardcoded.
 */
export function resolveTrackingPoKey(row: Pick<PoRow, "accurateId" | "sourcePoId" | "id" | "poNo">): string | null {
  const accurate = String(row.accurateId || "").trim();
  if (accurate) return accurate;
  const source = String(row.sourcePoId || "").trim();
  if (source) return source;
  const poNo = String(row.poNo || row.id || "").trim();
  return poNo || null;
}
