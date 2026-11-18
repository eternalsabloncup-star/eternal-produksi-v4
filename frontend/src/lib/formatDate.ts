/**
 * Formatter tanggal tampilan UI — DD/MM/YYYY.
 * Tidak mengubah nilai ISO untuk API/storage/sorting.
 */

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})/;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * Format nilai tanggal kalender ke DD/MM/YYYY.
 * String `YYYY-MM-DD` diparse sebagai komponen kalender (tanpa offset timezone).
 * Kosong/invalid → emptyLabel (default "—"), bukan tanggal palsu.
 */
export function formatDateDisplay(
  value: string | Date | null | undefined,
  emptyLabel = "—",
): string {
  if (value == null) return emptyLabel;

  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return emptyLabel;
    const m = trimmed.match(ISO_DATE);
    if (!m) return emptyLabel;
    const [, y, mo, d] = m;
    return `${d}/${mo}/${y}`;
  }

  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return emptyLabel;
    return `${pad2(value.getDate())}/${pad2(value.getMonth() + 1)}/${value.getFullYear()}`;
  }

  return emptyLabel;
}

/** Apakah string tampak seperti tanggal ISO YYYY-MM-DD (untuk helper UI). */
export function isIsoDateString(value: string): boolean {
  return ISO_DATE.test(value.trim());
}
