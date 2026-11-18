import { getApiPoSource } from "./ApiPoSource";
import { createDemoPoSeed } from "./fixtures";
import { resolveInboxPoSourceMode } from "./resolveInboxMode";
import type { PaginatedResult, PoDataSource, PoListQuery, PoRow } from "./types";

export type { PoSourceMode, ShellDataMode } from "./resolveInboxMode";
export {
  resolveInboxPoSourceMode,
  resolveShellDataMode,
  shellModeBadgeLabel,
  shellModeFooterHint,
  shellSidefootCopy,
} from "./resolveInboxMode";

function cloneRow(row: PoRow): PoRow {
  return {
    ...row,
    activity: [...row.activity],
    items: row.items.map((it) => ({ ...it })),
  };
}

/** Hanya record origin=demo — tidak dicampur Accurate. */
function assertDemoOnly(rows: PoRow[]): void {
  for (const row of rows) {
    if (row.origin !== "demo") {
      throw new Error(
        `DemoPoSource menolak origin="${row.origin}" (poNo=${row.poNo}).`,
      );
    }
  }
}

function matches(row: PoRow, query: PoListQuery): boolean {
  const q = (query.q || "").toLowerCase().trim();
  if (q) {
    const itemHay = row.items
      .map((it) =>
        [it.code, it.name, it.color, it.process, it.processRaw, it.processKind]
          .filter(Boolean)
          .join(" "),
      )
      .join(" ");
    const hay = [
      row.id,
      row.poNo,
      row.sourcePoId,
      row.customer,
      row.product,
      row.branch,
      row.status,
      row.urgent ? "Urgent" : "Normal",
      row.machine,
      row.seller,
      row.expedition,
      row.shipZone,
      row.recipient,
      itemHay,
    ]
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
    const cloned = seed.map((r) => cloneRow(r));
    assertDemoOnly(cloned);
    this.rows = cloned;
  }

  async list(query: PoListQuery): Promise<PaginatedResult<PoRow>> {
    const filtered = this.rows.filter((r) => matches(r, query));
    const pageSize = query.pageSize || 100;
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    const page = Math.min(Math.max(1, query.page || 1), pages);
    const start = (page - 1) * pageSize;
    return {
      items: filtered.slice(start, start + pageSize).map((r) => cloneRow(r)),
      total: filtered.length,
      page,
      pageSize,
      source: "demo",
    };
  }

  async getById(id: string): Promise<PoRow | null> {
    const row = this.rows.find(
      (r) => r.id === id || r.poNo === id || r.sourcePoId === id,
    );
    return row ? cloneRow(row) : null;
  }

  /** Snapshot identitas tanpa mutasi — untuk kontrak modul berikutnya. */
  peekIdentities(): { sourcePoId: string; poNo: string; lineIds: string[] }[] {
    return this.rows.map((r) => ({
      sourcePoId: r.sourcePoId,
      poNo: r.poNo,
      lineIds: r.items.map((it) => it.lineId),
    }));
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

/**
 * Pemilih sumber Inbox.
 * Production (unset) → api. Development (unset) → demo.
 * Mode API tidak boleh fallback diam-diam ke DEMO saat error.
 */
export function getInboxPoSource(
  envValue: string | undefined = import.meta.env.VITE_INBOX_PO_SOURCE,
): PoDataSource {
  const mode = resolveInboxPoSourceMode(envValue);
  if (mode === "api") return getApiPoSource();
  return getDemoPoSource();
}
