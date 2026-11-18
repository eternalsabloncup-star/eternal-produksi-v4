import { useEffect, useMemo, useState } from "react";
import {
  listAllocations,
  subscribeProductionAlloc,
  type DemoAllocation,
} from "../demo/productionHeadAlloc";
import { formatDateDisplay } from "../lib/formatDate";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

type Filters = { q?: string };

/**
 * Jadwal Produksi dari alokasi Kepala Produksi (single source DEMO).
 * Satu alokasi = satu baris; No. PO & Pelanggan diulang per baris (tanpa rowspan).
 */
export function ScheduleProductionTable() {
  const [tick, setTick] = useState(0);
  const [filters, setFilters] = useState<Filters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);

  useEffect(() => subscribeProductionAlloc(() => setTick((t) => t + 1)), []);

  const allRows = useMemo(() => {
    void tick;
    let rows = listAllocations();
    const q = filters.q?.trim().toLowerCase();
    if (q) {
      rows = rows.filter(
        (r) =>
          r.poNo.toLowerCase().includes(q) ||
          r.customer.toLowerCase().includes(q) ||
          r.itemName.toLowerCase().includes(q) ||
          r.operatorName.toLowerCase().includes(q) ||
          r.screen.toLowerCase().includes(q),
      );
    }
    return rows;
  }, [tick, filters]);

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const columns: ColumnDef<DemoAllocation>[] = useMemo(
    () => [
      {
        id: "poNo",
        label: "No. PO",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        minWidth: 90,
        getFilterValue: (row) => row.poNo,
        render: (row) => <span className="key">{row.poNo}</span>,
      },
      {
        id: "customer",
        label: "Pelanggan",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (row) => row.customer,
        render: (row) => row.customer,
      },
      {
        id: "itemName",
        label: "Nama Barang",
        hideable: true,
        filterType: "text",
        defaultWidth: 180,
        getFilterValue: (row) => row.itemName,
        render: (row) => row.itemName,
      },
      {
        id: "operator",
        label: "Operator",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (row) => row.operatorName,
        render: (row) => row.operatorName,
      },
      {
        id: "qtyScheduled",
        label: "Qty Dijadwalkan",
        hideable: true,
        filterType: "number",
        defaultWidth: 120,
        getFilterValue: (row) => String(row.qty),
        getSortValue: (row) => row.qty,
        render: (row) => <b>{formatQty(row.qty)}</b>,
      },
      {
        id: "screen",
        label: "Screen",
        hideable: true,
        filterType: "text",
        defaultWidth: 90,
        getFilterValue: (row) => row.screen,
        render: (row) => row.screen,
      },
      {
        id: "workDate",
        label: "Tanggal Pengerjaan",
        hideable: true,
        filterType: "date",
        defaultWidth: 130,
        getFilterValue: (row) => row.workDate,
        getSortValue: (row) => row.workDate,
        render: (row) => formatDateDisplay(row.workDate),
      },
    ],
    [],
  );

  return (
    <DataTable
      tableId="po-demo:schedule-alloc"
      columns={columns}
      rows={pageRows}
      filterSourceRows={allRows}
      rowKey={(r) => r.id}
      enableColumnManager
      inlineColumnTools
      page={page}
      pageSize={pageSize}
      total={allRows.length}
      onPageChange={setPage}
      onPageSizeChange={(size) => {
        setPageSize(size);
        setPage(1);
      }}
      emptyMessage="Belum ada alokasi operator pada Jadwal Produksi (DEMO)."
      filterSlot={(tools) => (
        <div className="ep-filterbar is-compact">
          <div className="ep-search">
            <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
              ⌕
            </span>
            <input
              placeholder="Cari PO, pelanggan, operator…"
              value={filters.q || ""}
              onChange={(e) => {
                setFilters({ q: e.target.value });
                setPage(1);
              }}
              aria-label="Cari jadwal"
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
          {tools.prefsHint ? (
            <span
              className={`ep-prefs-hint${tools.saveState === "error" ? " is-error" : ""}`}
              role={tools.saveState === "error" ? "alert" : undefined}
            >
              {tools.prefsHint}
            </span>
          ) : null}
        </div>
      )}
    />
  );
}
