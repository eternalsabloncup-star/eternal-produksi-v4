import { useEffect, useMemo, useState } from "react";
import {
  listProductionAllocRows,
  subscribeProductionAlloc,
  type ProductionAllocRow,
} from "../demo/productionHeadAlloc";
import {
  getRoleDoneCounts,
  type DoneFilter,
} from "../demo/teamTaskWork";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { ProductionHeadAllocPopup } from "./ProductionHeadAllocPopup";
import { TeamTaskDoneFilter } from "./TeamTaskDoneFilter";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

type Filters = { q?: string };

/** Tabel item Cetak untuk alokasi Kepala Produksi (DEMO). */
export function ProductionHeadAllocTable() {
  const [tick, setTick] = useState(0);
  const [doneFilter, setDoneFilter] = useState<DoneFilter>("all");
  const [filters, setFilters] = useState<Filters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [selected, setSelected] = useState<ProductionAllocRow | null>(null);

  useEffect(() => subscribeProductionAlloc(() => setTick((t) => t + 1)), []);

  const counts = useMemo(() => {
    void tick;
    return getRoleDoneCounts("production-head");
  }, [tick]);

  const allRows = useMemo(() => {
    void tick;
    let rows = listProductionAllocRows();
    if (doneFilter === "done") rows = rows.filter((r) => r.qtyRemaining <= 0);
    if (doneFilter === "open") rows = rows.filter((r) => r.qtyRemaining > 0);
    const q = filters.q?.trim().toLowerCase();
    if (q) {
      rows = rows.filter(
        (r) =>
          r.poNo.toLowerCase().includes(q) ||
          r.customer.toLowerCase().includes(q) ||
          r.itemName.toLowerCase().includes(q) ||
          r.operatorSummary.toLowerCase().includes(q),
      );
    }
    return rows;
  }, [tick, filters, doneFilter]);

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const columns: ColumnDef<ProductionAllocRow>[] = useMemo(
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
        id: "qtyPo",
        label: "Qty PO",
        hideable: true,
        filterType: "number",
        defaultWidth: 96,
        getFilterValue: (row) => String(row.qtyPo),
        getSortValue: (row) => row.qtyPo,
        render: (row) => <b>{formatQty(row.qtyPo)}</b>,
      },
      {
        id: "qtyAllocated",
        label: "Qty Dialokasikan",
        hideable: true,
        filterType: "number",
        defaultWidth: 120,
        getFilterValue: (row) => String(row.qtyAllocated),
        getSortValue: (row) => row.qtyAllocated,
        render: (row) => formatQty(row.qtyAllocated),
      },
      {
        id: "qtyRemaining",
        label: "Sisa Qty",
        hideable: true,
        filterType: "number",
        defaultWidth: 100,
        getFilterValue: (row) => String(row.qtyRemaining),
        getSortValue: (row) => row.qtyRemaining,
        render: (row) => <b>{formatQty(row.qtyRemaining)}</b>,
      },
      {
        id: "operator",
        label: "Operator",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (row) => row.operatorSummary,
        render: (row) => row.operatorSummary,
      },
      {
        id: "aksi",
        label: "Aksi",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: () => "Alokasi",
        render: (row) => (
          <button
            type="button"
            className="ep-btn sm"
            onClick={(e) => {
              e.stopPropagation();
              setSelected(row);
            }}
          >
            Alokasi Qty
          </button>
        ),
      },
    ],
    [],
  );

  return (
    <>
      <DataTable
        tableId="team-tasks:production-head-alloc"
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
        onRowActivate={(r) => setSelected(r)}
        emptyMessage="Belum ada item proses Cetak untuk alokasi (DEMO)."
        filterSlot={(tools) => (
          <div className="ep-filterbar is-compact ep-task-filterwrap">
            <TeamTaskDoneFilter
              value={doneFilter}
              doneCount={counts.done}
              openCount={counts.open}
              onChange={(v) => {
                setDoneFilter(v);
                setPage(1);
              }}
            />
            <div className="ep-search">
              <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
                ⌕
              </span>
              <input
                placeholder="Cari PO, pelanggan, barang…"
                value={filters.q || ""}
                onChange={(e) => {
                  setFilters({ q: e.target.value });
                  setPage(1);
                }}
                aria-label="Cari alokasi"
              />
            </div>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => {
                setDoneFilter("all");
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
      <ProductionHeadAllocPopup
        open={Boolean(selected)}
        row={
          selected
            ? allRows.find((r) => r.id === selected.id) ?? selected
            : null
        }
        onClose={() => setSelected(null)}
      />
    </>
  );
}
