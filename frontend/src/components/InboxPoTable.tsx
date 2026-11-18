import { useEffect, useMemo, useState } from "react";
import {
  getInboxPoSource,
  resolveInboxPoSourceMode,
} from "../demo/DemoPoSource";
import { PoApiError } from "../demo/ApiPoSource";
import type { PoFilters, PoRow } from "../demo/types";
import { formatDateDisplay } from "../lib/formatDate";
import { ColumnFilter } from "./DataTable/ColumnFilter";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { InboxPoDetailModal } from "./InboxPoDetailModal";
import { PoLineItemsDetail } from "./PoLineItemsDetail";
import "../styles/inbox.css";

/** Default ringkas: sembunyikan field panjang / sekunder. */
const INBOX_DEFAULT_HIDDEN = [
  "recipient",
  "address",
  "userName",
  "bufferLabel",
  "notes",
];

type Props = {
  tableId?: string;
};

/** Inbox PO — header Accurate + expand item (DEMO atau API Mongo V4). */
export function InboxPoTable({ tableId }: Props) {
  const sourceMode = resolveInboxPoSourceMode(
    import.meta.env.VITE_INBOX_PO_SOURCE,
  );
  const source = useMemo(() => getInboxPoSource(), []);
  const resolvedTableId =
    tableId ||
    (sourceMode === "api" ? "po-accurate:inbox-v1" : "po-demo:inbox-v2");

  const [filters, setFilters] = useState<PoFilters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [rows, setRows] = useState<PoRow[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<PoRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await source.list({ ...filters, page, pageSize });
        if (cancelled) return;
        setRows(result.items);
        setTotal(result.total);
        if (result.page !== page) setPage(result.page);
      } catch (err) {
        if (cancelled) return;
        setRows([]);
        setTotal(0);
        if (err instanceof PoApiError) {
          setError(err.message);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError("Gagal memuat Inbox PO.");
        }
        // Mode API: jangan fallback diam-diam ke DEMO.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, filters, page, pageSize]);

  const columns: ColumnDef<PoRow>[] = useMemo(
    () => [
      {
        id: "id",
        label: "No. PO",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 108,
        minWidth: 88,
        getFilterValue: (row) => row.id,
        render: (row) => (
          <button
            type="button"
            className="key"
            onClick={(e) => {
              e.stopPropagation();
              setSelected(row);
            }}
          >
            {row.id}
            {row.urgent ? (
              <span style={{ color: "#d45a59", fontSize: 9 }}> ●</span>
            ) : null}
          </button>
        ),
      },
      {
        id: "poDate",
        label: "Tanggal PO",
        hideable: true,
        filterType: "date",
        defaultWidth: 104,
        getFilterValue: (row) => row.poDate,
        getSortValue: (row) => row.poDate,
        render: (row) => formatDateDisplay(row.poDate),
      },
      {
        id: "customer",
        label: "Pelanggan / Kepada",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        minWidth: 110,
        getFilterValue: (row) => row.customer,
        render: (row) => row.customer || "—",
      },
      {
        id: "recipient",
        label: "Nama Penerima",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (row) => row.recipient,
        render: (row) => row.recipient || "—",
      },
      {
        id: "address",
        label: "Alamat",
        hideable: true,
        filterType: "text",
        defaultWidth: 200,
        getFilterValue: (row) => row.address,
        render: (row) => row.address || "—",
      },
      {
        id: "paymentTerms",
        label: "Syarat Pembayaran",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (row) => row.paymentTerms,
        render: (row) => row.paymentTerms || "—",
      },
      {
        id: "userName",
        label: "User",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (row) => row.userName,
        render: (row) => row.userName || "—",
      },
      {
        id: "expedition",
        label: "Ekspedisi",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (row) => row.expedition,
        render: (row) => row.expedition || "—",
      },
      {
        id: "etaShip",
        label: "Estimasi Tanggal Pengiriman",
        hideable: true,
        filterType: "date",
        defaultWidth: 150,
        getFilterValue: (row) => row.etaShip,
        getSortValue: (row) => row.etaShip,
        render: (row) => formatDateDisplay(row.etaShip),
      },
      {
        id: "shipZone",
        label: "Zona Pengiriman",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (row) => row.shipZone,
        render: (row) => row.shipZone || "—",
      },
      {
        id: "seller",
        label: "Penjual",
        hideable: true,
        filterType: "text",
        defaultWidth: 96,
        getFilterValue: (row) => row.seller,
        render: (row) => row.seller || "—",
      },
      {
        id: "orderStatus",
        label: "Status Pesanan",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) => (row.urgent ? "Urgent" : "Normal"),
        getSortValue: (row) => (row.urgent ? 0 : 1),
        render: (row) => (
          <span className="ep-cell-center">
            <span
              className={`ep-pill ep-pill-status ${row.urgent ? "order-urgent" : "order-normal"}`}
            >
              {row.urgent ? "Urgent" : "Normal"}
            </span>
          </span>
        ),
      },
      {
        id: "bufferLabel",
        label: "Buffer / Non-Buffer",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (row) => row.bufferLabel,
        render: (row) => {
          const label = (row.bufferLabel || "").trim();
          if (!label) {
            return <span className="ep-cell-center">—</span>;
          }
          const isBuffer = label.toLowerCase() === "buffer";
          return (
            <span className="ep-cell-center">
              <span
                className={`ep-pill ep-pill-status ${isBuffer ? "buffer-yes" : "buffer-no"}`}
              >
                {label}
              </span>
            </span>
          );
        },
      },
      {
        id: "notes",
        label: "Keterangan",
        hideable: true,
        filterType: "text",
        defaultWidth: 170,
        getFilterValue: (row) => row.notes || "",
        render: (row) => row.notes || "—",
      },
      {
        id: "itemCount",
        label: "Jumlah Item",
        hideable: true,
        filterType: "number",
        defaultWidth: 96,
        getFilterValue: (row) => String(row.itemCount),
        getSortValue: (row) => row.itemCount,
        render: (row) => row.itemCount,
      },
      {
        id: "totalQtyDisplay",
        label: "Total Kuantitas",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (row) => row.totalQtyDisplay,
        render: (row) => <b>{row.totalQtyDisplay || "—"}</b>,
      },
    ],
    [],
  );

  function resetFilters() {
    setFilters({});
    setPage(1);
  }

  const emptyMessage = loading
    ? "Memuat PO…"
    : error
      ? null
      : sourceMode === "api"
        ? "Belum ada PO Accurate di Mongo V4. Jalankan sync manual terlebih dahulu."
        : "Tidak ada PO DEMO.";

  return (
    <>
      <div
        className={`ep-inbox-source-banner is-${sourceMode}`}
        role="status"
      >
        {sourceMode === "api" ? (
          <>
            Sumber data: <strong>Accurate (Mongo V4)</strong> — read-only. Bukan
            DEMO.
          </>
        ) : (
          <>
            Sumber data: <strong>DEMO</strong> — set{" "}
            <code>VITE_INBOX_PO_SOURCE=api</code> untuk Inbox Accurate.
          </>
        )}
      </div>
      {error ? (
        <div className="ep-inbox-error" role="alert">
          {error}
          {sourceMode === "api" ? (
            <span className="ep-inbox-error-hint">
              {" "}
              Inbox tidak beralih ke DEMO.
            </span>
          ) : null}
        </div>
      ) : null}
      <DataTable
        tableId={resolvedTableId}
        columns={columns}
        rows={loading || error ? [] : rows}
        rowKey={(r) => r.sourcePoId || r.accurateId || r.id}
        enableColumnManager
        inlineColumnTools
        defaultHiddenColumns={INBOX_DEFAULT_HIDDEN}
        page={page}
        pageSize={pageSize}
        total={error ? 0 : total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        emptyMessage={emptyMessage}
        onRowActivate={setSelected}
        isRowExpandable={(row) => (row.items?.length ?? 0) > 0}
        renderExpandedRow={(row) => <PoLineItemsDetail row={row} />}
        filterSlot={(tools) => (
          <ColumnFilter
            value={filters}
            onChange={(next) => {
              const cleaned =
                sourceMode === "api"
                  ? {
                      // API: jangan simpan filter yang dinonaktifkan di UI.
                      q: next.q?.trim() ? next.q : undefined,
                      urgent: next.urgent || undefined,
                    }
                  : {
                      ...next,
                      q: next.q?.trim() ? next.q : undefined,
                    };
              setFilters(cleaned);
              setPage(1);
            }}
            onReset={resetFilters}
            showUrgent
            compact
            sourceMode={sourceMode}
            showDemoHint={sourceMode === "demo"}
            trailing={
              <>
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
              </>
            }
          />
        )}
      />
      <InboxPoDetailModal
        open={Boolean(selected)}
        row={selected}
        onClose={() => setSelected(null)}
      />
    </>
  );
}
