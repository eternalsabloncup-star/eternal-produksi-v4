/**
 * Adapter Inbox → API Mongo Accurate (`/api/po`).
 * Hanya origin=accurate. Tidak fallback ke DEMO jika gagal.
 */

import { displayAccurateLabel, formatAccurateLabel } from "./formatAccurateLabel";
import { buildInboxApiListParams } from "./inboxApiFilters";
import type { PaginatedResult, PoDataSource, PoListQuery, PoRow } from "./types";

export { buildInboxApiListParams, inboxFilterEnabled } from "./inboxApiFilters";

export class PoApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status = 0, code = "po_api_error") {
    super(message);
    this.name = "PoApiError";
    this.status = status;
    this.code = code;
  }
}

function resolveInboxStatus(
  doc: Record<string, unknown>,
  header: Record<string, unknown>,
): string {
  const operational =
    doc.operational && typeof doc.operational === "object" && !Array.isArray(doc.operational)
      ? (doc.operational as Record<string, unknown>)
      : {};
  for (const key of ["status", "stage", "inbox_status"] as const) {
    const raw = operational[key] ?? header[key];
    const label = formatAccurateLabel(raw);
    if (label) return label;
  }
  // Status awal jujur — bukan progres produksi/QC/jadwal.
  return "PO Baru";
}

export function mapHeaderToPoRow(doc: Record<string, unknown>): PoRow | null {
  const header = (doc.header || doc) as Record<string, unknown>;
  if (!header || typeof header !== "object") return null;
  const poNo = formatAccurateLabel(header.po_no || header.poNo || doc.po_no);
  if (!poNo) return null;
  const itemsRaw = Array.isArray(header.items) ? header.items : [];
  const items = itemsRaw.map((raw) => {
    const it = raw as Record<string, unknown>;
    const processKind = (it.process_kind || it.processKind || null) as
      | "Cetak"
      | "Non-Cetak"
      | null;
    const processRaw = formatAccurateLabel(it.process_raw || it.processRaw);
    const unit = formatAccurateLabel(it.unit) || "PCS";
    const qtyNum = Number(it.qty || 0);
    return {
      lineId: String(it.line_id || it.lineId || ""),
      accurateLineId: it.accurate_line_id
        ? String(it.accurate_line_id)
        : it.accurateLineId
          ? String(it.accurateLineId)
          : null,
      code: formatAccurateLabel(it.item_code || it.code),
      name: formatAccurateLabel(it.item_name || it.name),
      qty: Number.isFinite(qtyNum) ? qtyNum : 0,
      unit,
      processRaw,
      processKind,
      process: processKind || processRaw || "—",
      color: formatAccurateLabel(it.color),
    };
  });
  const accurateId = doc.accurate_id
    ? String(doc.accurate_id)
    : header.accurate_id
      ? String(header.accurate_id)
      : null;
  const customer = formatAccurateLabel(header.customer || doc.customer);
  const paymentTerms = formatAccurateLabel(header.payment_terms || header.paymentTerms);
  const expedition = formatAccurateLabel(header.expedition || header.shipment);
  const poDate = formatAccurateLabel(header.po_date || header.poDate);
  return {
    id: poNo,
    poNo,
    sourcePoId: String(
      header.source_po_id ||
        doc.source_po_id ||
        (accurateId ? `accurate:${accurateId}` : `accurate:${poNo}`),
    ),
    accurateId,
    origin: "accurate",
    customer,
    product: items[0]?.name || "",
    qty: items.reduce((s, i) => s + (Number.isFinite(i.qty) ? i.qty : 0), 0),
    // Modul cetak/QC belum terhubung — jangan mengarang progres.
    printed: 0,
    qc: 0,
    branch: "",
    status: resolveInboxStatus(doc, header),
    machine: "",
    deadline: "",
    urgent: Boolean(header.urgent),
    owner: formatAccurateLabel(header.seller),
    sample: false,
    activity: ["PO dari Accurate (Mongo V4)"],
    poDate,
    paymentTerms,
    etaShip: formatAccurateLabel(header.eta_ship || header.etaShip),
    seller: formatAccurateLabel(header.seller),
    expedition,
    shipZone: formatAccurateLabel(header.ship_zone || header.shipZone),
    recipient: formatAccurateLabel(header.recipient),
    address: formatAccurateLabel(header.address),
    userName: formatAccurateLabel(header.user_name || header.userName),
    bufferLabel: formatAccurateLabel(header.buffer_label || header.bufferLabel),
    notes: formatAccurateLabel(header.notes),
    addressPicName: formatAccurateLabel(
      header.address_pic_name || header.addressPicName,
    ),
    printUserName: formatAccurateLabel(
      header.print_user_name || header.printUserName,
    ),
    createdById: formatAccurateLabel(
      header.created_by_id || header.createdById,
    ),
    priorityLabel: formatAccurateLabel(
      header.priority_label || header.priorityLabel,
    ),
    totalQtyDisplay: items
      .map((i) => `${i.qty.toLocaleString("id-ID")} ${i.unit}`)
      .join(" + "),
    itemCount: items.length,
    items,
  };
}

async function readJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/**
 * Sumber API — hanya origin=accurate dari Mongo V4.
 * Aktifkan via VITE_INBOX_PO_SOURCE=api (tanpa fallback DEMO diam-diam).
 */
export class ApiPoSource implements PoDataSource {
  readonly kind = "api" as const;

  constructor(private readonly baseUrl = "/api/po") {}

  async list(query: PoListQuery): Promise<PaginatedResult<PoRow>> {
    const page = Math.max(1, query.page || 1);
    const pageSize = query.pageSize || 50;
    // Hanya q + urgent — branch/status/mesin tidak dikirim (belum didukung Accurate).
    const params = buildInboxApiListParams(query);

    let res: Response;
    try {
      res = await fetch(`${this.baseUrl}?${params.toString()}`, {
        credentials: "include",
      });
    } catch {
      throw new PoApiError(
        "Jaringan gagal memuat PO Accurate dari server.",
        0,
        "network_error",
      );
    }
    if (res.status === 401) {
      throw new PoApiError(
        "Sesi berakhir — login ulang untuk memuat Inbox Accurate.",
        401,
        "unauthorized",
      );
    }
    if (!res.ok) {
      throw new PoApiError(
        `Gagal memuat PO Accurate (HTTP ${res.status})`,
        res.status,
      );
    }
    const body = (await readJson(res)) as {
      items?: Array<Record<string, unknown>>;
      total?: number;
      page?: number;
      pageSize?: number;
      source?: string;
    } | null;
    if (!body || !Array.isArray(body.items)) {
      throw new PoApiError("Respons API PO tidak valid.", res.status, "bad_payload");
    }
    const rows = body.items
      .map((item) => mapHeaderToPoRow(item))
      .filter((r): r is PoRow => Boolean(r));
    for (const row of rows) {
      if (row.origin !== "accurate") {
        throw new PoApiError(
          `API mengembalikan origin tidak valid: ${row.origin}`,
          res.status,
          "origin_mismatch",
        );
      }
    }
    return {
      items: rows,
      total: typeof body.total === "number" ? body.total : rows.length,
      page: typeof body.page === "number" ? body.page : page,
      pageSize: typeof body.pageSize === "number" ? body.pageSize : pageSize,
      source: "api",
    };
  }

  async getById(id: string): Promise<PoRow | null> {
    // Nomor PO Accurate memakai '/' — jangan encode jadi %2F (proxy/router 404).
    const pathKey = String(id || "")
      .split("/")
      .map((part) => encodeURIComponent(part))
      .join("/");
    let res: Response;
    try {
      res = await fetch(`${this.baseUrl}/${pathKey}`, {
        credentials: "include",
      });
    } catch {
      throw new PoApiError(
        "Jaringan gagal memuat detail PO Accurate.",
        0,
        "network_error",
      );
    }
    if (res.status === 404) return null;
    if (res.status === 401) {
      throw new PoApiError(
        "Sesi berakhir — login ulang untuk memuat detail PO.",
        401,
        "unauthorized",
      );
    }
    if (!res.ok) {
      throw new PoApiError(`Gagal memuat PO ${id} (HTTP ${res.status})`, res.status);
    }
    const doc = (await readJson(res)) as Record<string, unknown> | null;
    if (!doc) throw new PoApiError("Detail PO tidak valid.", res.status);
    const row = mapHeaderToPoRow(doc);
    if (!row) return null;
    if (row.origin !== "accurate") {
      throw new PoApiError("Detail PO bukan origin accurate.", res.status);
    }
    return row;
  }

  async metrics(): Promise<Record<string, number>> {
    // Agregasi server-side — mencakup seluruh PO, bukan sample ≤500 list.
    let res: Response;
    try {
      res = await fetch(`${this.baseUrl}/metrics`, {
        credentials: "include",
      });
    } catch {
      throw new PoApiError(
        "Jaringan gagal memuat KPI Accurate dari server.",
        0,
        "network_error",
      );
    }
    if (res.status === 401) {
      throw new PoApiError(
        "Sesi berakhir — login ulang untuk memuat KPI Accurate.",
        401,
        "unauthorized",
      );
    }
    if (!res.ok) {
      throw new PoApiError(
        `Gagal memuat KPI Accurate (HTTP ${res.status})`,
        res.status,
      );
    }
    const body = (await readJson(res)) as Record<string, unknown> | null;
    if (!body || typeof body.total !== "number") {
      throw new PoApiError("Respons KPI PO tidak valid.", res.status, "bad_payload");
    }
    return {
      total: Number(body.total) || 0,
      baru: Number(body.baru ?? body.total) || 0,
      qty: Number(body.qty) || 0,
      lines: Number(body.lines) || 0,
      urgent: Number(body.urgent) || 0,
      printed: 0,
    };
  }
}

let apiSingleton: ApiPoSource | null = null;

export function getApiPoSource(): ApiPoSource {
  if (!apiSingleton) apiSingleton = new ApiPoSource();
  return apiSingleton;
}

/** Dipakai tes/unit — pastikan label tidak [object Object]. */
export function formatPoFieldForDisplay(value: unknown): string {
  return displayAccurateLabel(value);
}
