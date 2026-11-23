/**
 * Canonical PWA information placement helpers.
 * Dedupes by semantic identity — not string equality alone.
 */

import type { OperationalTask } from "../api/operational";

/** Header already owns task-level SJ / PO document ids on PWA worksheets. */
export function pwaBodyShouldRepeatDocumentRef(
  pwaOps: boolean,
  task: Pick<OperationalTask, "ref">,
): boolean {
  if (!pwaOps) return true;
  const hasSj = Boolean(
    task.ref.sj_no || task.ref.accurate_delivery_note_id,
  );
  const hasPo = Boolean(task.ref.po_no || task.ref.accurate_po_id);
  // Body must not restate the same document id already in the sticky header.
  return !(hasSj || hasPo);
}

/** Allocation cards need item identity only when multiple lines share the sheet. */
export function allocCardNeedsItemIdentity(lineCount: number): boolean {
  return lineCount > 1;
}

/**
 * Qty summary "Sisa" belongs next to the confirm control on PWA.
 * Item summary keeps Qty + Qty Terkonfirmasi only.
 */
export function pwaItemSummaryShowsRemaining(pwaOps: boolean): boolean {
  return !pwaOps;
}
