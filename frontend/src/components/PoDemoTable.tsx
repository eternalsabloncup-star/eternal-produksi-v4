import { useEffect, useMemo, useState } from "react";
import { getDemoPoSource } from "../demo/DemoPoSource";
import type { PoFilters, PoRow } from "../demo/types";
import { formatDateDisplay } from "../lib/formatDate";
import { ColumnFilter } from "./DataTable/ColumnFilter";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { DetailDrawer } from "./DetailDrawer";
import { StatusBadge } from "./StatusBadge";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

type Props = {
  /** Disimpan untuk kompatibilitas pemanggil; tidak ditampilkan sebagai judul panel. */
  title?: string;
  /** Identitas tabel stabil untuk preferensi per pengguna. Opsional agar pemanggil lama aman. */
  tableId?: string;
  showMachineFilter?: boolean;
  showUrgentFilter?: boolean;
};

export function PoDemoTable({
  title,
  tableId,
  showMachineFilter = false,
  showUrgentFilter = false,
}: Props) {
  const resolvedTableId =
    tableId ||
    (title ? `po-demo:${title.toLowerCase().replace(/\s+/g, "-")}` : "po-demo:default");
  const source = useMemo(() => getDemoPoSource(), []);
  const [filters, setFilters] = useState<PoFilters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [rows, setRows] = useState<PoRow[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<PoRow | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const result = await source.list({ ...filters, page, pageSize });
      if (cancelled) return;
      setRows(result.items);
      setTotal(result.total);
      if (result.page !== page) setPage(result.page);
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
        defaultWidth: 110,
        minWidth: 88,
        getFilterValue: (row) => row.id,
        render: (row) => (
          <button type="button" className="key" onClick={() => setSelected(row)}>
            {row.id}
            {row.urgent ? (
              <span style={{ color: "#d45a59", fontSize: 9 }}> ●</span>
            ) : null}
          </button>
        ),
      },
      {
        id: "customer",
        label: "Pelanggan",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        minWidth: 100,
        getFilterValue: (row) => row.customer,
        render: (row) => row.customer,
      },
      {
        id: "product",
        label: "Produk",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (row) => row.product,
        render: (row) => row.product,
      },
      {
        id: "qty",
        label: "Qty PO",
        hideable: true,
        filterType: "number",
        defaultWidth: 88,
        getFilterValue: (row) => String(row.qty),
        getSortValue: (row) => row.qty,
        render: (row) => <b>{formatQty(row.qty)}</b>,
      },
      {
        id: "printed",
        label: "Sudah Cetak",
        hideable: true,
        filterType: "number",
        defaultWidth: 96,
        getFilterValue: (row) => String(row.printed),
        getSortValue: (row) => row.printed,
        render: (row) => formatQty(row.printed),
      },
      {
        id: "branch",
        label: "Cabang",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) => row.branch,
        render: (row) => row.branch,
      },
      {
        id: "machine",
        label: "Mesin",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (row) => row.machine || "",
        render: (row) => row.machine || <span style={{ color: "#acb7c4" }}>—</span>,
      },
      {
        id: "deadline",
        label: "Deadline",
        hideable: true,
        filterType: "date",
        defaultWidth: 100,
        getFilterValue: (row) => row.deadline,
        getSortValue: (row) => row.deadline,
        render: (row) => {
          const late =
            row.deadline < "2026-11-18" &&
            !["Terkirim", "Siap Kirim"].includes(row.status);
          return (
            <span style={{ color: late ? "#bd4254" : "inherit" }}>
              {formatDateDisplay(row.deadline)}
            </span>
          );
        },
      },
      {
        id: "status",
        label: "Status",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) => row.status,
        render: (row) => <StatusBadge status={row.status} />,
      },
      {
        id: "progress",
        label: "Progres",
        hideable: true,
        filterType: "number",
        defaultWidth: 110,
        getFilterValue: (row) =>
          String(Math.round((row.printed / row.qty) * 100)),
        getSortValue: (row) => Math.round((row.printed / row.qty) * 100),
        render: (row) => {
          const pct = Math.round((row.printed / row.qty) * 100);
          return (
            <>
              <span className="ep-progress">
                <span style={{ width: `${Math.min(100, pct)}%` }} />
              </span>
              <span style={{ fontSize: 10 }}>{pct}%</span>
            </>
          );
        },
      },
      {
        id: "actions",
        label: "Aksi",
        hideable: false,
        filterable: false,
        defaultWidth: 88,
        minWidth: 72,
        render: (row) => (
          <button type="button" className="ep-btn sm" onClick={() => setSelected(row)}>
            Detail ↗
          </button>
        ),
      },
    ],
    [],
  );

  function resetFilters() {
    setFilters({});
    setPage(1);
  }

  const pct = selected
    ? Math.round((selected.printed / selected.qty) * 100)
    : 0;

  return (
    <>
      <DataTable
        tableId={resolvedTableId}
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        enableColumnManager
        inlineColumnTools
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        filterSlot={(tools) => (
          <ColumnFilter
            value={filters}
            onChange={(next) => {
              setFilters({
                ...next,
                q: next.q?.trim() ? next.q : undefined,
              });
              setPage(1);
            }}
            onReset={resetFilters}
            showMachine={showMachineFilter}
            showUrgent={showUrgentFilter}
            compact
            showDemoHint={false}
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

      <DetailDrawer
        open={Boolean(selected)}
        title={selected?.id || ""}
        subtitle={selected ? `${selected.customer} · ${selected.branch}` : undefined}
        onClose={() => setSelected(null)}
      >
        {selected ? (
          <>
            <div className="ep-infogrid">
              {[
                ["Pelanggan", selected.customer],
                ["Cabang", selected.branch],
                ["Produk", selected.product],
                ["Sales/PIC", selected.owner],
                ["Qty PO", `${formatQty(selected.qty)} pcs`],
                ["Deadline", formatDateDisplay(selected.deadline)],
                ["Mesin", selected.machine || "Belum dialokasikan"],
              ].map(([k, v]) => (
                <div key={k}>
                  <div className="lbl">{k}</div>
                  <strong>{v}</strong>
                </div>
              ))}
              <div>
                <div className="lbl">Status</div>
                <strong>
                  <StatusBadge status={selected.status} />
                </strong>
              </div>
            </div>
            <div className="ep-sectionlabel">Progres Produksi (DEMO)</div>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <strong>
                {formatQty(selected.printed)} / {formatQty(selected.qty)} pcs
              </strong>
              <strong>{pct}%</strong>
            </div>
            <div className="ep-progress" style={{ width: "100%", height: 9 }}>
              <span style={{ width: `${pct}%` }} />
            </div>
            <div className="ep-note" style={{ marginTop: 16 }}>
              Detail ini hanya membaca fixture DEMO di browser. Tidak ada mutasi status
              bisnis, booking kapasitas, atau penulisan MongoDB pada Task #004.
            </div>
          </>
        ) : null}
      </DetailDrawer>
    </>
  );
}
