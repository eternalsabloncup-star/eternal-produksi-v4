/**
 * Adapter Inbox → API Mongo Accurate (`/api/po`).
 * Hanya origin=accurate. Tidak fallback ke DEMO jika gagal.
 */

import type { PaginatedResult, PoDataSource, PoListQuery, PoRow } from "./types";

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

function mapHeaderToPoRow(doc: Record<string, unknown>): PoRow | null {
  const header = (doc.header || doc) as Record<string, unknown>;
  if (!header || typeof header !== "object") return null;
  const poNo = String(header.po_no || header.poNo || doc.po_no || "");
  if (!poNo) return null;
  const itemsRaw = Array.isArray(header.items) ? header.items : [];
  const items = itemsRaw.map((raw) => {
    const it = raw as Record<string, unknown>;
    const processKind = (it.process_kind || it.processKind || null) as
      | "Cetak"
      | "Non-Cetak"
      | null;
    const processRaw = String(it.process_raw || it.processRaw || "");
    return {
      lineId: String(it.line_id || it.lineId || ""),
      accurateLineId: it.accurate_line_id
        ? String(it.accurate_line_id)
        : it.accurateLineId
          ? String(it.accurateLineId)
          : null,
      code: String(it.item_code || it.code || ""),
      name: String(it.item_name || it.name || ""),
      qty: Number(it.qty || 0),
      unit: String(it.unit || "PCS"),
      processRaw,
      processKind,
      process: processKind || processRaw || "—",
      color: String(it.color || ""),
    };
  });
  const accurateId = doc.accurate_id
    ? String(doc.accurate_id)
    : header.accurate_id
      ? String(header.accurate_id)
      : null;
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
    customer: String(header.customer || doc.customer || ""),
    product: items[0]?.name || "",
    qty: items.reduce((s, i) => s + (Number.isFinite(i.qty) ? i.qty : 0), 0),
    printed: 0,
    qc: 0,
    branch: "",
    status: "PO Baru",
    machine: "",
    deadline: "",
    urgent: Boolean(header.urgent),
    owner: String(header.seller || ""),
    sample: false,
    activity: ["PO dari Accurate (Mongo V4)"],
    poDate: String(header.po_date || header.poDate || ""),
    paymentTerms: String(header.payment_terms || ""),
    etaShip: String(header.eta_ship || ""),
    seller: String(header.seller || ""),
    expedition: String(header.expedition || ""),
    shipZone: String(header.ship_zone || ""),
    recipient: String(header.recipient || ""),
    address: String(header.address || ""),
    userName: String(header.user_name || ""),
    bufferLabel: String(header.buffer_label || ""),
    notes: String(header.notes || ""),
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
    const params = new URLSearchParams();
    const page = Math.max(1, query.page || 1);
    const pageSize = query.pageSize || 50;
    params.set("page", String(page));
    params.set("pageSize", String(pageSize));
    if (query.q?.trim()) params.set("q", query.q.trim());
    if (query.urgent) params.set("urgent", "true");
    params.set("sortBy", "updated_at");
    params.set("sortDir", "desc");

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
    const listed = await this.list({ page: 1, pageSize: 500 });
    return {
      total: listed.total,
      baru: listed.total,
      unsched: listed.total,
      qty: listed.items.reduce((s, r) => s + r.qty, 0),
      printed: 0,
      urgent: listed.items.filter((r) => r.urgent).length,
    };
  }
}

let apiSingleton: ApiPoSource | null = null;

export function getApiPoSource(): ApiPoSource {
  if (!apiSingleton) apiSingleton = new ApiPoSource();
  return apiSingleton;
}
