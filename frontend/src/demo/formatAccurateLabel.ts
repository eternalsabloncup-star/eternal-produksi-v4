/**
 * Format field Accurate (sering object nested) → label bisnis terbaca.
 * Tidak mengarang nilai: kosong → "".
 */

const DICT_NAME_KEYS = [
  "name",
  "paymentTermName",
  "shipmentName",
  "customerName",
  "unitName",
  "no",
  "number",
  "code",
  "title",
] as const;

function labelFromRecord(record: Record<string, unknown>): string {
  for (const key of DICT_NAME_KEYS) {
    const hit = record[key];
    if (hit == null) continue;
    if (typeof hit === "object") continue;
    const text = String(hit).trim();
    if (text && text !== "[object Object]") return text;
  }
  return "";
}

/** Coba parse string representasi dict Python/JSON yang pernah tersimpan dari str(dict). */
function labelFromDictRepr(text: string): string {
  const trimmed = text.trim();
  if (!(trimmed.startsWith("{") && trimmed.endsWith("}"))) return "";
  try {
    const normalized = trimmed.replace(/'/g, '"');
    const parsed = JSON.parse(normalized) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return labelFromRecord(parsed as Record<string, unknown>);
    }
  } catch {
    // bukan JSON valid — jangan menebak
  }
  return "";
}

/**
 * Ubah nilai API/Mongo menjadi label tampilan.
 * Object/dict → name; list → gabungan label; kosong → "".
 */
export function formatAccurateLabel(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") {
    const text = value.trim();
    if (!text || text === "[object Object]") return "";
    const fromRepr = labelFromDictRepr(text);
    if (fromRepr) return fromRepr;
    return text;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => formatAccurateLabel(item))
      .filter(Boolean)
      .join(", ");
  }
  if (typeof value === "object") {
    return labelFromRecord(value as Record<string, unknown>);
  }
  return "";
}

/** Label tabel: kosong → em dash. */
export function displayAccurateLabel(value: unknown): string {
  return formatAccurateLabel(value) || "—";
}
