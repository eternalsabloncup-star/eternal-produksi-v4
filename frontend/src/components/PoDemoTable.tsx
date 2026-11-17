import { useEffect, useMemo, useState } from "react";
import { getDemoPoSource } from "../demo/DemoPoSource";
import type { PoFilters, PoRow } from "../demo/types";
import { ColumnFilter } from "./DataTable/ColumnFilter";
import { ColumnVisibility } from "./DataTable/ColumnVisibility";
import { DataTable } from "./DataTable/DataTable";
import type { ColumnDef } from "./DataTable/types";
import { DetailDrawer } from "./DetailDrawer";
import { StatusBadge } from "./StatusBadge";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

type Props = {
  title: string;
  showMachineFilter?: boolean;
  showUrgentFilter?: boolean;
};

export function PoDemoTable({
  title,
  showMachineFilter = false,
  showUrgentFilter = false,
}: Props) {
  const source = useMemo(() => getDemoPoSource(), []);
  const [filters, setFilters] = useState<PoFilters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const [rows, setRows] = useState<PoRow[]>([]);
  const [total, setTotal] = useState(0);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [colsOpen, setColsOpen] = useState(false);
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
        render: (row) => row.customer,
      },
      {
        id: "product",
        label: "Produk",
        hideable: true,
        render: (row) => row.product,
      },
      {
        id: "qty",
        label: "Qty PO",
        hideable: true,
        render: (row) => <b>{formatQty(row.qty)}</b>,
      },
      {
        id: "printed",
        label: "Sudah Cetak",
        hideable: true,
        render: (row) => formatQty(row.printed),
      },
      {
        id: "branch",
        label: "Cabang",
        hideable: true,
        render: (row) => row.branch,
      },
      {
        id: "machine",
        label: "Mesin",
        hideable: true,
        render: (row) => row.machine || <span style={{ color: "#acb7c4" }}>—</span>,
      },
      {
        id: "deadline",
        label: "Deadline",
        hideable: true,
        render: (row) => {
          const late =
            row.deadline < "2026-11-18" &&
            !["Terkirim", "Siap Kirim"].includes(row.status);
          return (
            <span style={{ color: late ? "#bd4254" : "inherit" }}>{row.deadline}</span>
          );
        },
      },
      {
        id: "status",
        label: "Status",
        hideable: true,
        render: (row) => <StatusBadge status={row.status} />,
      },
      {
        id: "progress",
        label: "Progres",
        hideable: true,
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
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        hiddenColumns={hidden}
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        toolbar={
          <div className="ep-panelhead">
            <div>
              <h3>{title}</h3>
              <span className="meta">
                Sticky PO &amp; pelanggan · scroll di dalam tabel · pagination siap
                server-side nanti
              </span>
            </div>
            <div className="ep-tools">
              <button type="button" className="ep-btn sm" onClick={() => setColsOpen(true)}>
                ▥ Kolom Tabel
              </button>
            </div>
          </div>
        }
        filterSlot={
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
          />
        }
      />

      <ColumnVisibility
        columns={columns}
        hidden={hidden}
        onChange={setHidden}
        open={colsOpen}
        onClose={() => setColsOpen(false)}
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
                ["Deadline", selected.deadline],
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
