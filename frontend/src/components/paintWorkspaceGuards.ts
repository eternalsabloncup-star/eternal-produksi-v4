/** Pure guards for Manajemen Cat UX — stocktake stages + conversion mass status. */

export type StocktakeLineInput = {
  paint_id: string;
  counted_g: string;
};

export type ConversionMassStatus = "incomplete" | "balanced" | "unbalanced";

/** Empty field ≠ zero. "0" / "0.0" are valid physical counts. */
export function isPhysicalCountFilled(counted_g: string): boolean {
  return counted_g.trim() !== "";
}

export function parsePhysicalCount(counted_g: string): number | null {
  if (!isPhysicalCountFilled(counted_g)) return null;
  const n = Number(counted_g);
  if (!Number.isFinite(n) || n < 0) return null;
  return n;
}

export function allRequiredCounted(lines: StocktakeLineInput[]): boolean {
  return lines.length > 0 && lines.every((l) => parsePhysicalCount(l.counted_g) !== null);
}

export function stocktakeCountsFingerprint(lines: StocktakeLineInput[]): string {
  return lines
    .map((l) => `${l.paint_id}:${l.counted_g.trim()}`)
    .sort()
    .join("|");
}

export function canEnterStocktakePreview(
  branchId: string,
  lines: StocktakeLineInput[],
): boolean {
  return Boolean(branchId) && allRequiredCounted(lines);
}

/** Session creation requires a successful preview of the current counts. */
export function canCreateStocktakeSession(opts: {
  branchId: string;
  lines: StocktakeLineInput[];
  preview: Record<string, unknown> | null;
  previewFingerprint: string | null;
}): boolean {
  if (!opts.branchId || !allRequiredCounted(opts.lines)) return false;
  if (!opts.preview || opts.preview.persisted !== false) return false;
  const fp = stocktakeCountsFingerprint(opts.lines);
  return Boolean(opts.previewFingerprint) && opts.previewFingerprint === fp;
}

export function canFinalizeStocktake(opts: {
  stocktakeId: string | null;
  lines: StocktakeLineInput[];
}): boolean {
  return Boolean(opts.stocktakeId) && allRequiredCounted(opts.lines);
}

export function isPreviewStale(
  lines: StocktakeLineInput[],
  previewFingerprint: string | null,
): boolean {
  if (!previewFingerprint) return false;
  return stocktakeCountsFingerprint(lines) !== previewFingerprint;
}

export function conversionMassStatus(opts: {
  sourcesReady: boolean;
  resultReady: boolean;
  sourceTotal: number;
  destG: number;
  wasteG: number;
  eps?: number;
}): ConversionMassStatus {
  const eps = opts.eps ?? 1e-9;
  if (!opts.sourcesReady || !opts.resultReady || !(opts.sourceTotal > 0)) {
    return "incomplete";
  }
  if (opts.destG + opts.wasteG <= opts.sourceTotal + eps) return "balanced";
  return "unbalanced";
}

/** Withdrawal user must match a backend-returned user email (not free-typed). */
export function resolvePaintUserEmail(
  typed: string,
  users: Array<{ email: string; display_name?: string }>,
): string | null {
  const q = typed.trim().toLowerCase();
  if (!q) return null;
  const byEmail = users.find((u) => u.email.toLowerCase() === q);
  if (byEmail) return byEmail.email;
  const byName = users.filter(
    (u) => (u.display_name || "").trim().toLowerCase() === q,
  );
  if (byName.length === 1) return byName[0].email;
  return null;
}
