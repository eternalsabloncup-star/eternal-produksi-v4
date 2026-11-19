import { useEffect, useMemo, useState } from "react";
import {
  filterByDone,
  getRoleDoneCounts,
  listOperatorTasks,
  markOperatorDone,
  subscribeTeamTaskWork,
  type DoneFilter,
  type OperatorTaskRow,
} from "../demo/teamTaskWork";
import { formatDateDisplay } from "../lib/formatDate";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { TeamTaskDoneFilter } from "./TeamTaskDoneFilter";
import { TaskWorkInfoGrid, TaskWorkModalShell } from "./TaskWorkModalShell";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

export function OperatorTaskPanel() {
  const [tick, setTick] = useState(0);
  const [doneFilter, setDoneFilter] = useState<DoneFilter>("all");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [selected, setSelected] = useState<OperatorTaskRow | null>(null);
  const [successOpen, setSuccessOpen] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => subscribeTeamTaskWork(() => setTick((t) => t + 1)), []);

  const counts = useMemo(() => {
    void tick;
    return getRoleDoneCounts("operator");
  }, [tick]);

  const allRows = useMemo(() => {
    void tick;
    let rows = filterByDone(listOperatorTasks(), doneFilter);
    const qq = q.trim().toLowerCase();
    if (qq) {
      rows = rows.filter(
        (r) =>
          r.poNo.toLowerCase().includes(qq) ||
          r.customer.toLowerCase().includes(qq) ||
          r.itemName.toLowerCase().includes(qq) ||
          r.operatorName.toLowerCase().includes(qq),
      );
    }
    return rows;
  }, [tick, doneFilter, q]);

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const columns: ColumnDef<OperatorTaskRow>[] = useMemo(
    () => [
      {
        id: "poNo",
        label: "No. PO",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.poNo,
        render: (r) => <span className="key">{r.poNo}</span>,
      },
      {
        id: "customer",
        label: "Pelanggan",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 150,
        getFilterValue: (r) => r.customer,
        render: (r) => r.customer,
      },
      {
        id: "itemName",
        label: "Nama Barang",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (r) => r.itemName,
        render: (r) => r.itemName,
      },
      {
        id: "operator",
        label: "Operator",
        hideable: true,
        filterType: "text",
        defaultWidth: 130,
        getFilterValue: (r) => r.operatorName,
        render: (r) => r.operatorName,
      },
      {
        id: "qty",
        label: "Qty Dijadwalkan",
        hideable: true,
        filterType: "number",
        defaultWidth: 110,
        getFilterValue: (r) => String(r.qty),
        getSortValue: (r) => r.qty,
        render: (r) => <b>{formatQty(r.qty)}</b>,
      },
      {
        id: "screen",
        label: "Screen",
        hideable: true,
        filterType: "text",
        defaultWidth: 90,
        getFilterValue: (r) => r.screen,
        render: (r) => r.screen,
      },
      {
        id: "workDate",
        label: "Tanggal Pengerjaan",
        hideable: true,
        filterType: "date",
        defaultWidth: 130,
        getFilterValue: (r) => r.workDate,
        render: (r) => formatDateDisplay(r.workDate),
      },
      {
        id: "done",
        label: "Status Kerja",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => (r.done ? "Sudah" : "Belum"),
        render: (r) => (r.done ? "Sudah Dikerjakan" : "Belum Dikerjakan"),
      },
    ],
    [],
  );

  useEffect(() => {
    if (!selected) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !successOpen) setSelected(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, successOpen]);

  return (
    <>
      <DataTable
        tableId="team-tasks:operator-v2"
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
        onRowActivate={setSelected}
        emptyMessage="Belum ada alokasi operator (DEMO)."
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
                placeholder="Cari PO / operator…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
                aria-label="Cari operator"
              />
            </div>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => {
                setDoneFilter("all");
                setQ("");
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
            <button type="button" className="ep-btn sm" onClick={() => void tools.resetPrefs()}>
              Reset tampilan
            </button>
          </div>
        )}
      />

      <TaskWorkModalShell
        open={Boolean(selected)}
        title={selected ? `Operator · ${selected.operatorName}` : "Operator"}
        subtitle={selected ? `${selected.poNo} · ${selected.itemName}` : undefined}
        onClose={() => {
          if (!successOpen) setSelected(null);
        }}
        overlay={
          <ConfirmationSuccessOverlay
            open={successOpen}
            title="Pekerjaan Operator Berhasil"
            message="Simulasi penyelesaian alokasi operator"
            demo
            onClose={() => setSuccessOpen(false)}
          />
        }
        footer={
          <>
            <span className="ep-po-confirm-hint">Aksi DEMO · tidak permanen</span>
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={() => setSelected(null)}>
                Batal
              </button>
              <button
                type="button"
                className="ep-btn primary"
                disabled={!selected || selected.done || successOpen}
                onClick={() => {
                  if (!selected) return;
                  markOperatorDone(selected.id);
                  setSelected({ ...selected, done: true });
                  setSuccessOpen(true);
                }}
              >
                Konfirmasi (Demo)
              </button>
            </div>
          </>
        }
      >
        {selected ? (
          <>
            <TaskWorkInfoGrid
              fields={[
                ["No. PO", selected.poNo],
                ["Pelanggan", selected.customer],
                ["Nama Barang", selected.itemName],
                ["Operator", selected.operatorName],
                ["Qty Dijadwalkan", formatQty(selected.qty)],
                ["Screen", selected.screen],
                ["Tanggal Pengerjaan", formatDateDisplay(selected.workDate)],
                ["Status Kerja", selected.done ? "Sudah Dikerjakan" : "Belum Dikerjakan"],
              ]}
            />
            <label className="ep-alloc-field">
              <span className="lbl">Catatan DEMO</span>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Opsional…"
                disabled={selected.done}
              />
            </label>
          </>
        ) : null}
      </TaskWorkModalShell>
    </>
  );
}
