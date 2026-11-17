import { createDemoPoSeed } from "./fixtures";
import type { PaginatedResult, PoDataSource, PoListQuery, PoRow } from "./types";

function matches(row: PoRow, query: PoListQuery): boolean {
  const q = (query.q || "").toLowerCase().trim();
  if (q) {
    const hay = [row.id, row.customer, row.product, row.branch, row.status, row.machine]
      .join(" ")
      .toLowerCase();
    if (!hay.includes(q)) return false;
  }
  if (query.status && row.status !== query.status) return false;
  if (query.branch && row.branch !== query.branch) return false;
  if (query.machine && row.machine !== query.machine) return false;
  if (query.urgent && !row.urgent) return false;
  return true;
}

/** In-memory demo source. Never writes MongoDB or business APIs. */
export class DemoPoSource implements PoDataSource {
  readonly kind = "demo" as const;
  private rows: PoRow[];

  constructor(seed: PoRow[] = createDemoPoSeed()) {
    this.rows = seed.map((r) => ({ ...r, activity: [...r.activity] }));
  }

  async list(query: PoListQuery): Promise<PaginatedResult<PoRow>> {
    const filtered = this.rows.filter((r) => matches(r, query));
    const pageSize = query.pageSize || 15;
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    const page = Math.min(Math.max(1, query.page || 1), pages);
    const start = (page - 1) * pageSize;
    return {
      items: filtered.slice(start, start + pageSize).map((r) => ({ ...r })),
      total: filtered.length,
      page,
      pageSize,
      source: "demo",
    };
  }

  async getById(id: string): Promise<PoRow | null> {
    const row = this.rows.find((r) => r.id === id);
    return row ? { ...row, activity: [...row.activity] } : null;
  }

  async metrics(): Promise<Record<string, number>> {
    const data = this.rows;
    return {
      total: data.length,
      baru: data.filter((x) => x.status === "PO Baru").length,
      unsched: data.filter((x) =>
        ["PO Baru", "Belum Terjadwal"].includes(x.status),
      ).length,
      produksi: data.filter((x) => x.status === "Produksi").length,
      sample: data.filter((x) => x.status === "Menunggu Sampel").length,
      ready: data.filter((x) => x.status === "Siap Kirim").length,
      late: data.filter(
        (x) =>
          x.deadline < "2026-11-18" &&
          !["Terkirim", "Siap Kirim"].includes(x.status),
      ).length,
      qty: data.reduce((s, x) => s + x.qty, 0),
      printed: data.reduce((s, x) => s + x.printed, 0),
      scheduledQty: data
        .filter((x) => x.machine)
        .reduce((s, x) => s + x.qty, 0),
      urgent: data.filter((x) => x.urgent).length,
    };
  }
}

let singleton: DemoPoSource | null = null;

export function getDemoPoSource(): DemoPoSource {
  if (!singleton) singleton = new DemoPoSource();
  return singleton;
}
