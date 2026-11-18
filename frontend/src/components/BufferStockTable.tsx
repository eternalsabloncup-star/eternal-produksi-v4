import { useMemo, useState } from "react";
import {
  listBufferStockRows,
  type BufferStatus,
  type BufferStockRow,
} from "../demo/bufferStockDemo";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

function statusClass(status: BufferStatus): string {
  if (status === "ANTREAN MESIN") return "is-queue";
  if (status === "READY GUDANG") return "is-ready";
  return "is-done";
}

type Filters = { q?: string };

/** Tabel item line Buffer Stock (DEMO) — satu baris per item. */
export function BufferStockTable() {
  const [filters, setFilters] = useState<Filters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);

  const allRows = useMemo(() => {
    let rows = listBufferStockRows();
    const q = filters.q?.trim().toLowerCase();
    if (q) {
      rows = rows.filter(
        (r) =>
          r.poNo.toLowerCase().includes(q) ||
          r.outlet.toLowerCase().includes(q) ||
          r.itemCode.toLowerCase().includes(q) ||
          r.itemName.toLowerCase().includes(q) ||
          r.color.toLowerCase().includes(q) ||
          r.salesPic.toLowerCase().includes(q) ||
          r.status.toLowerCase().includes(q),
      );
    }
    return rows;
  }, [filters]);

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
        getFilterValue: (r) => r.poNo,
        render: (r) => <span className="key">{r.poNo}</span>,
      },
      {
        id: "outlet",
        label: "Nama Outlet / Gudang",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (r) => r.outlet,
        render: (r) => r.outlet,
      },
      {
        id: "itemCode",
        label: "Kode Barang",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (r) => r.itemCode,
        render: (r) => r.itemCode,
      },
      {
        id: "itemName",
        label: "Nama Barang",
        hideable: true,
        filterType: "text",
        defaultWidth: 180,
        getFilterValue: (r) => r.itemName,
        render: (r) => r.itemName,
      },
      {
        id: "color",
        label: "Warna",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (r) => r.color,
        render: (r) => r.color,
      },
      {
        id: "qtyOrdered",
        label: "Total Buffer Dipesan",
        hideable: true,
        filterType: "number",
        defaultWidth: 130,
        getFilterValue: (r) => String(r.qtyOrdered),
        getSortValue: (r) => r.qtyOrdered,
        render: (r) => <b>{formatQty(r.qtyOrdered)}</b>,
      },
      {
        id: "qtyPrinted",
        label: "Sudah Selesai Cetak",
        hideable: true,
        filterType: "number",
        defaultWidth: 130,
        getFilterValue: (r) => String(r.qtyPrinted),
        getSortValue: (r) => r.qtyPrinted,
        render: (r) => formatQty(r.qtyPrinted),
      },
      {
        id: "qtyRemaining",
        label: "Sisa Belum Dicetak",
        hideable: true,
        filterType: "number",
        defaultWidth: 130,
        getFilterValue: (r) => String(r.qtyRemaining),
        getSortValue: (r) => r.qtyRemaining,
        render: (r) => formatQty(r.qtyRemaining),
      },
      {
        id: "qtyReady",
        label: "Ready Gudang (Siap Ambil)",
        hideable: true,
        filterType: "number",
        defaultWidth: 150,
        getFilterValue: (r) => String(r.qtyReady),
        getSortValue: (r) => r.qtyReady,
        render: (r) => formatQty(r.qtyReady),
      },
      {
        id: "qtyTaken",
        label: "Sudah Diambil / Terkirim",
        hideable: true,
        filterType: "number",
        defaultWidth: 150,
        getFilterValue: (r) => String(r.qtyTaken),
        getSortValue: (r) => r.qtyTaken,
        render: (r) => formatQty(r.qtyTaken),
      },
      {
        id: "status",
        label: "Status Buffer",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (r) => r.status,
        render: (r) => (
          <span className={`ep-buffer-status ${statusClass(r.status)}`}>
            {r.status}
          </span>
        ),
      },
      {
        id: "salesPic",
        label: "Sales PIC",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (r) => r.salesPic,
        render: (r) => r.salesPic,
      },
    ],
    [],
  );

  return (
    <DataTable
      tableId="buffer-stock:main-v1"
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
      onPageSizeChange={(s) => {
        setPageSize(s);
        setPage(1);
      }}
      emptyMessage="Tidak ada item Buffer Stock (DEMO)."
      filterSlot={(tools) => (
        <div className="ep-filterbar is-compact">
          <div className="ep-search">
            <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
              ⌕
            </span>
            <input
              placeholder="Cari PO, outlet, barang, warna…"
              value={filters.q || ""}
              onChange={(e) => {
                setFilters({ q: e.target.value });
                setPage(1);
              }}
              aria-label="Cari Buffer Stock"
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
        </div>
      )}
    />
  );
}
