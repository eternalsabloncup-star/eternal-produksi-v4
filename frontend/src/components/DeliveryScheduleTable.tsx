import { useMemo, useState } from "react";
import {
  filterDeliverySjRows,
  itemCount,
  rowTotalQtyDisplay,
  type DeliverySjRow,
  type ShipDateFilterValue,
} from "../demo/deliverySjDemo";
import { formatDateDisplay } from "../lib/formatDate";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { DeliveryDetailModal } from "./DeliveryDetailModal";
import { ShipDateFilter } from "./ShipDateFilter";
import { SjLineItemsDetail } from "./SjLineItemsDetail";

const DEFAULT_HIDDEN = ["address", "notes", "createdBy", "printedAt"];

/** Tabel Surat Jalan — Jadwal Pengiriman (DEMO). */
export function DeliveryScheduleTable() {
  const [dateFilter, setDateFilter] = useState<ShipDateFilterValue>({
    kind: "all",
  });
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [detailOpen, setDetailOpen] = useState(false);

  const allRows = useMemo(
    () => filterDeliverySjRows(dateFilter, q),
    [dateFilter, q],
  );

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const columns: ColumnDef<DeliverySjRow>[] = useMemo(
    () => [
      {
        id: "sjNo",
        label: "No. SJ",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.sjNo,
        render: (r) => <span className="key">{r.sjNo}</span>,
      },
      {
        id: "poNo",
        label: "No. PO",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (r) => r.poNo,
        render: (r) => r.poNo,
      },
      {
        id: "customer",
        label: "Pelanggan",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (r) => r.customer,
        render: (r) => r.customer,
      },
      {
        id: "address",
        label: "Alamat Pengiriman",
        hideable: true,
        filterType: "text",
        defaultWidth: 200,
        getFilterValue: (r) => r.address,
        render: (r) => r.address,
      },
      {
        id: "seller",
        label: "Nama Penjual",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (r) => r.seller,
        render: (r) => r.seller,
      },
      {
        id: "expedition",
        label: "Ekspedisi",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (r) => r.expedition,
        render: (r) => r.expedition,
      },
      {
        id: "createdBy",
        label: "Pembuat Data",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (r) => r.createdBy,
        render: (r) => r.createdBy,
      },
      {
        id: "shipZone",
        label: "Zona Kirim",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (r) => r.shipZone,
        render: (r) => r.shipZone,
      },
      {
        id: "printedAt",
        label: "Tanggal SJ Dicetak",
        hideable: true,
        filterType: "date",
        defaultWidth: 130,
        getFilterValue: (r) => r.printedAt,
        getSortValue: (r) => r.printedAt,
        render: (r) => formatDateDisplay(r.printedAt),
      },
      {
        id: "shipDate",
        label: "Tanggal Kirim",
        hideable: true,
        filterType: "date",
        defaultWidth: 120,
        getFilterValue: (r) => r.shipDate,
        getSortValue: (r) => r.shipDate,
        render: (r) => formatDateDisplay(r.shipDate),
      },
      {
        id: "itemCount",
        label: "Jumlah Barang",
        hideable: true,
        filterType: "number",
        defaultWidth: 110,
        getFilterValue: (r) => String(itemCount(r)),
        getSortValue: (r) => itemCount(r),
        render: (r) => itemCount(r),
      },
      {
        id: "totalQty",
        label: "Total Kuantitas",
        hideable: true,
        filterType: "text",
        defaultWidth: 150,
        getFilterValue: (r) => rowTotalQtyDisplay(r),
        render: (r) => <b>{rowTotalQtyDisplay(r)}</b>,
      },
      {
        id: "notes",
        label: "Keterangan",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (r) => r.notes,
        render: (r) => r.notes || "—",
      },
    ],
    [],
  );

  function resetFilters() {
    setDateFilter({ kind: "all" });
    setQ("");
    setPage(1);
  }

  return (
    <>
      <DataTable
        tableId="delivery:sj-schedule-v1"
        columns={columns}
        rows={pageRows}
        filterSourceRows={allRows}
        rowKey={(r) => r.id}
        enableColumnManager
        inlineColumnTools
        defaultHiddenColumns={DEFAULT_HIDDEN}
        page={page}
        pageSize={pageSize}
        total={allRows.length}
        onPageChange={setPage}
        onPageSizeChange={(s) => {
          setPageSize(s);
          setPage(1);
        }}
        isRowExpandable={(row) => (row.items?.length ?? 0) > 0}
        renderExpandedRow={(row) => <SjLineItemsDetail row={row} />}
        emptyMessage="Tidak ada Surat Jalan pada filter aktif (DEMO)."
        filterSlot={(tools) => (
          <div className="ep-filterbar is-compact">
            <div className="ep-search">
              <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
                ⌕
              </span>
              <input
                placeholder="Cari No. SJ, PO, pelanggan…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
                aria-label="Cari Surat Jalan"
              />
            </div>
            <ShipDateFilter
              value={dateFilter}
              onChange={(next) => {
                setDateFilter(next);
                setPage(1);
              }}
            />
            <button type="button" className="ep-btn sm" onClick={resetFilters}>
              Reset filter
            </button>
            <button
              type="button"
              className="ep-btn sm primary"
              onClick={() => setDetailOpen(true)}
            >
              Rincian Pengiriman
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

      <DeliveryDetailModal
        open={detailOpen}
        rows={allRows}
        dateFilter={dateFilter}
        onClose={() => setDetailOpen(false)}
      />
    </>
  );
}
