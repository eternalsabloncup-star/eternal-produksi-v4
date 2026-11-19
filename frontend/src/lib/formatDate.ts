/**
 * Formatter tanggal tampilan UI — DD/MM/YYYY.
 * Tidak mengubah nilai untuk API/storage/sorting.
 * Tidak mengarang tanggal dari nomor PO.
 */

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})/;
/** Accurate / locale ID sering memakai dd/MM/yyyy. */
const DMY_DATE = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/;
/** dd/MM/yyyy dengan waktu opsional (sumber Accurate). */
const DMY_DATETIME =
  /^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$/;
/** YYYY-MM-DD dengan waktu ISO opsional. */
const ISO_DATETIME =
  /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function isValidYmd(y: number, m: number, d: number): boolean {
  if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) {
    return false;
  }
  if (m < 1 || m > 12 || d < 1 || d > 31) return false;
  const dt = new Date(y, m - 1, d);
  return (
    dt.getFullYear() === y && dt.getMonth() === m - 1 && dt.getDate() === d
  );
}

/**
 * Format nilai tanggal kalender ke DD/MM/YYYY.
 * Menerima:
 * - `YYYY-MM-DD` (+ optional waktu ISO)
 * - `DD/MM/YYYY` (+ optional waktu)
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

    const iso = trimmed.match(ISO_DATETIME) || trimmed.match(ISO_DATE);
    if (iso) {
      const y = Number(iso[1]);
      const mo = Number(iso[2]);
      const d = Number(iso[3]);
      if (!isValidYmd(y, mo, d)) return emptyLabel;
      return `${pad2(d)}/${pad2(mo)}/${y}`;
    }

    const dmy = trimmed.match(DMY_DATETIME) || trimmed.match(DMY_DATE);
    if (dmy) {
      const d = Number(dmy[1]);
      const mo = Number(dmy[2]);
      const y = Number(dmy[3]);
      if (!isValidYmd(y, mo, d)) return emptyLabel;
      return `${pad2(d)}/${pad2(mo)}/${y}`;
    }

    return emptyLabel;
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

/**
 * Tanggal kalender → `YYYY-MM-DD` untuk perbandingan filter dan sorting.
 * Kosong/invalid → "" agar baris tanpa tanggal tidak ikut rentang apa pun.
 */
export function toIsoDateString(value: string | null | undefined): string {
  if (!value) return "";
  const trimmed = value.trim();
  if (!trimmed) return "";

  const iso = trimmed.match(ISO_DATETIME) || trimmed.match(ISO_DATE);
  if (iso) {
    const y = Number(iso[1]);
    const mo = Number(iso[2]);
    const d = Number(iso[3]);
    return isValidYmd(y, mo, d) ? `${y}-${pad2(mo)}-${pad2(d)}` : "";
  }

  const dmy = trimmed.match(DMY_DATETIME) || trimmed.match(DMY_DATE);
  if (dmy) {
    const d = Number(dmy[1]);
    const mo = Number(dmy[2]);
    const y = Number(dmy[3]);
    return isValidYmd(y, mo, d) ? `${y}-${pad2(mo)}-${pad2(d)}` : "";
  }

  return "";
}

/**
 * Tanggal + jam sumber Accurate → `DD/MM/YYYY HH:mm`.
 * Nilai tanpa jam tetap tampil sebagai tanggal saja.
 */
export function formatDateTimeDisplay(
  value: string | null | undefined,
  emptyLabel = "—",
): string {
  if (!value) return emptyLabel;
  const trimmed = value.trim();
  if (!trimmed) return emptyLabel;

  const date = formatDateDisplay(trimmed, emptyLabel);
  if (date === emptyLabel) return emptyLabel;

  const match = trimmed.match(ISO_DATETIME) || trimmed.match(DMY_DATETIME);
  const hour = match?.[4];
  const minute = match?.[5];
  if (hour == null || minute == null) return date;
  return `${date} ${pad2(Number(hour))}:${minute}`;
}
