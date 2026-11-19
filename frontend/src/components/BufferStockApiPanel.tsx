import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchBufferStock,
  NOT_CONFIGURED,
  type BufferStock,
  type BufferStockRow,
} from "../api/analytics";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { KpiSummary, type KpiItem } from "./KpiSummary";

function num(value: number | string): string {
  return typeof value === "number" ? value.toLocaleString("id-ID") : String(value);
}

type Filters = { q?: string };

/** Buffer Stock — DataTable API; Ready Gudang tidak disimulasikan. */
export function BufferStockApiPanel() {
  const [data, setData] = useState<BufferStock | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchBufferStock());
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "Gagal memuat Buffer Stock.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const kpis = useMemo((): KpiItem[] => {
    const kpi = data?.kpi;
    return [
      {
        label: "Total Buffer Dipesan",
        value: num(kpi?.total_buffer_ordered ?? 0),
        foot: "PO beratribut Buffer",
      },
      {
        label: "Sudah Selesai Cetak",
        value: num(kpi?.qty_printed ?? 0),
        foot: "ledger cetak terkonfirmasi",
      },
      {
        label: "Sisa Belum Dicetak",
        value: num(kpi?.qty_remaining ?? 0),
        foot: "Qty PO − Qty cetak",
        kind: "warn",
      },
      {
        label: "Ready Gudang",
        value: num(kpi?.qty_ready_warehouse ?? NOT_CONFIGURED),
        foot: "NOT_CONFIGURED · bukan qty cetak/QC/Loading",
        kind: "warn",
      },
    ];
  }, [data]);

  const allRows = useMemo(() => {
    const rows = data?.items || [];
    const q = filters.q?.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.po_no.toLowerCase().includes(q) ||
        r.outlet_warehouse.toLowerCase().includes(q) ||
        r.item_code.toLowerCase().includes(q) ||
        r.item_name.toLowerCase().includes(q) ||
        (r.color || "").toLowerCase().includes(q) ||
        (r.sales_pic || "").toLowerCase().includes(q) ||
        (r.buffer_status || "").toLowerCase().includes(q),
    );
  }, [data, filters]);

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const columns: ColumnDef<BufferStockRow>[] = useMemo(
    () => [
      {
        id: "poNo",
        label: "Nomor PO Buffer",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.po_no,
        render: (r) => <span className="key">{r.po_no}</span>,
      },
      {
        id: "outlet",
        label: "Outlet/Gudang",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (r) => r.outlet_warehouse,
        render: (r) => r.outlet_warehouse,
      },
      {
        id: "itemCode",
        label: "Kode Barang",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (r) => r.item_code,
        render: (r) => r.item_code,
      },
      {
        id: "itemName",
        label: "Nama Barang",
        hideable: true,
        filterType: "text",
        defaultWidth: 180,
        getFilterValue: (r) => r.item_name,
        render: (r) => r.item_name,
      },
      {
        id: "color",
        label: "Warna",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (r) => r.color || "",
        render: (r) => r.color || "—",
      },
      {
        id: "qtyOrdered",
        label: "Total Buffer Dipesan",
        hideable: true,
        filterType: "number",
        defaultWidth: 130,
        getFilterValue: (r) => String(r.qty_ordered),
        getSortValue: (r) => r.qty_ordered,
        render: (r) => (
          <>
            {num(r.qty_ordered)} {r.unit}
          </>
        ),
      },
      {
        id: "qtyPrinted",
        label: "Sudah Selesai Cetak",
        hideable: true,
        filterType: "number",
        defaultWidth: 120,
        getFilterValue: (r) => String(r.qty_printed),
        getSortValue: (r) => r.qty_printed,
        render: (r) => num(r.qty_printed),
      },
      {
        id: "qtyRemaining",
        label: "Sisa Belum Dicetak",
        hideable: true,
        filterType: "number",
        defaultWidth: 120,
        getFilterValue: (r) => String(r.qty_remaining),
        getSortValue: (r) => r.qty_remaining,
        render: (r) => num(r.qty_remaining),
      },
      {
        id: "qtyReady",
        label: "Ready Gudang",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (r) => String(r.qty_ready_warehouse),
        render: (r) => {
          const v = r.qty_ready_warehouse;
          if (v === NOT_CONFIGURED || typeof v === "string") {
            return (
              <span className="ep-muted" title="Kontrak mutasi gudang belum diputuskan Owner">
                {String(v)}
              </span>
            );
          }
          return num(v);
        },
      },
      {
        id: "qtyShipped",
        label: "Sudah Diambil/Terkirim",
        hideable: true,
        filterType: "number",
        defaultWidth: 130,
        getFilterValue: (r) => String(r.qty_shipped),
        getSortValue: (r) => r.qty_shipped,
        render: (r) => num(r.qty_shipped),
      },
      {
        id: "status",
        label: "Status Buffer",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.buffer_status,
        render: (r) => r.buffer_status,
      },
      {
        id: "salesPic",
        label: "Sales PIC",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.sales_pic || "",
        render: (r) => r.sales_pic || "—",
      },
    ],
    [],
  );

  return (
    <div data-testid="buffer-stock-api-panel">
      <KpiSummary items={kpis} />
      {error ? <p className="ep-error">{error}</p> : null}
      {data && data.not_configured.length > 0 ? (
        <p className="ep-notice">
          Kolom berikut menunggu keputusan Owner: {data.not_configured.join(", ")}. Ready
          Gudang tidak disimulasikan.
        </p>
      ) : null}

      <DataTable
        tableId="po-accurate:buffer-stock-v1"
        columns={columns}
        rows={loading ? [] : pageRows}
        filterSourceRows={allRows}
        rowKey={(r) => `${r.accurate_po_id}-${r.accurate_line_id}`}
        enableColumnManager
        inlineColumnTools
        page={page}
        pageSize={pageSize}
        total={loading ? 0 : allRows.length}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        emptyMessage={
          loading
            ? "Memuat Buffer Stock dari MongoDB V4…"
            : "Belum ada PO beratribut Buffer di MongoDB V4."
        }
        filterSlot={(tools) => (
          <div className="ep-filterbar is-compact">
            <strong>Buffer Stock · MongoDB V4</strong>
            <div className="ep-search">
              <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
                ⌕
              </span>
              <input
                placeholder="Cari PO, outlet, barang…"
                value={filters.q || ""}
                onChange={(e) => {
                  setFilters({ q: e.target.value });
                  setPage(1);
                }}
                aria-label="Cari buffer stock"
              />
            </div>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => {
                setFilters({});
                setPage(1);
              }}
            >
              Reset filter
            </button>
            <button type="button" className="ep-btn sm" onClick={() => void load()}>
              Muat ulang
            </button>
            <button
              type="button"
              className="ep-btn sm"
              onClick={(e) => tools.openColumns(e.currentTarget)}
            >
              ▥ Kolom Tabel
            </button>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => void tools.resetPrefs()}
            >
              Reset tampilan
            </button>
          </div>
        )}
      />
    </div>
  );
}
