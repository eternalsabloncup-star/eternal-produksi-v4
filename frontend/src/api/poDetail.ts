/**
 * Detail PO Accurate untuk popup Tugas Tim (Designer).
 * Sumber: GET /api/po/{accurate_id|po_no} — sama kontrak Inbox/ApiPoSource.
 */

import { getCsrfToken } from "../api";

export type PoPrintLine = {
  accurateLineId: string;
  itemCode: string;
  itemName: string;
  qty: number;
  unit: string;
};

export type PoDetailSummary = {
  accurateId: string | null;
  poNo: string;
  /** null = field customer tidak ada di respons. */
  customer: string | null;
  /**
   * true/false dari header.urgent; null bila field absen
   * (tampilkan "Belum tersedia").
   */
  urgent: boolean | null;
  itemCount: number;
  printLines: PoPrintLine[];
};

export class PoDetailApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "PoDetailApiError";
    this.status = status;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function label(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

/** Encode path PO yang boleh mengandung '/' (selaras ApiPoSource.getById). */
export function poDetailPathKey(id: string): string {
  return String(id || "")
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

/** Pure — ekstrak customer/urgent/item Cetak dari dokumen GET /api/po. */
export function summarizePoDetailDoc(
  doc: Record<string, unknown>,
): PoDetailSummary {
  const header = asRecord(doc.header) || {};
  const poNo =
    label(doc.po_no) || label(header.po_no) || label(header.poNo) || "";
  const accurateId =
    doc.accurate_id != null
      ? String(doc.accurate_id)
      : header.accurate_id != null
        ? String(header.accurate_id)
        : null;

  // Kontrak API: top-level customer dari header.customer (bukan customer_name).
  const customerRaw = doc.customer ?? header.customer;
  const customerLabel = label(customerRaw);
  const customer =
    customerRaw === undefined || customerRaw === null
      ? null
      : customerLabel || null;

  const urgent =
    Object.prototype.hasOwnProperty.call(header, "urgent")
      ? Boolean(header.urgent)
      : null;

  const itemsRaw = Array.isArray(header.items) ? header.items : [];
  const printLines: PoPrintLine[] = [];
  for (const raw of itemsRaw) {
    const it = asRecord(raw);
    if (!it) continue;
    const kind = label(it.process_kind || it.processKind);
    if (kind !== "Cetak") continue;
    const qtyNum = Number(it.qty);
    printLines.push({
      accurateLineId: String(
        it.accurate_line_id || it.accurateLineId || it.line_id || it.lineId || "",
      ),
      itemCode: label(it.item_code || it.code),
      itemName: label(it.item_name || it.name) || "—",
      qty: Number.isFinite(qtyNum) ? qtyNum : 0,
      unit: label(it.unit) || "PCS",
    });
  }

  return {
    accurateId,
    poNo,
    customer,
    urgent,
    itemCount: itemsRaw.length,
    printLines,
  };
}

export function urgencyLabel(urgent: boolean | null): string {
  if (urgent === true) return "Urgent";
  if (urgent === false) return "Tidak Urgent";
  return "Belum tersedia";
}

export async function fetchPoDetail(
  accuratePoIdOrPoNo: string,
): Promise<PoDetailSummary> {
  const key = accuratePoIdOrPoNo.trim();
  if (!key) {
    throw new PoDetailApiError("Identitas PO kosong.", 422);
  }
  // CSRF tidak wajib GET; tetap sertakan bila ada (konsisten klien lain).
  void getCsrfToken;
  let res: Response;
  try {
    res = await fetch(`/api/po/${poDetailPathKey(key)}`, {
      credentials: "include",
    });
  } catch (err) {
    throw new PoDetailApiError(
      err instanceof Error ? err.message : "Koneksi gagal memuat PO.",
      0,
    );
  }
  if (res.status === 404) {
    throw new PoDetailApiError("PO tidak ditemukan.", 404);
  }
  if (!res.ok) {
    throw new PoDetailApiError(`Gagal memuat PO (HTTP ${res.status}).`, res.status);
  }
  const doc = (await res.json()) as Record<string, unknown>;
  return summarizePoDetailDoc(doc);
}
