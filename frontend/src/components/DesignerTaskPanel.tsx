import { useEffect, useMemo, useState } from "react";
import {
  filterByDone,
  getRoleDoneCounts,
  listDesignerTasks,
  markDesignerDone,
  subscribeTeamTaskWork,
  type DesignerTask,
  type DoneFilter,
} from "../demo/teamTaskWork";
import { formatDateDisplay } from "../lib/formatDate";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { PdfViewerModal } from "./PdfViewerModal";
import { TeamTaskDoneFilter } from "./TeamTaskDoneFilter";
import { TaskWorkInfoGrid, TaskWorkModalShell } from "./TaskWorkModalShell";

export function DesignerTaskPanel() {
  const [tick, setTick] = useState(0);
  const [doneFilter, setDoneFilter] = useState<DoneFilter>("all");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [selected, setSelected] = useState<DesignerTask | null>(null);
  const [successOpen, setSuccessOpen] = useState(false);
  const [pdfOpen, setPdfOpen] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => subscribeTeamTaskWork(() => setTick((t) => t + 1)), []);

  const counts = useMemo(() => {
    void tick;
    return getRoleDoneCounts("designer");
  }, [tick]);

  const allRows = useMemo(() => {
    void tick;
    let rows = filterByDone(listDesignerTasks(), doneFilter);
    const qq = q.trim().toLowerCase();
    if (qq) {
      rows = rows.filter(
        (r) =>
          r.poNo.toLowerCase().includes(qq) ||
          r.customer.toLowerCase().includes(qq) ||
          r.designStatus.toLowerCase().includes(qq),
      );
    }
    return rows;
  }, [tick, doneFilter, q]);

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const columns: ColumnDef<DesignerTask>[] = useMemo(
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
        defaultWidth: 160,
        getFilterValue: (r) => r.customer,
        render: (r) => r.customer,
      },
      {
        id: "designStatus",
        label: "Status Desain",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.designStatus,
        render: (r) => r.designStatus,
      },
      {
        id: "target",
        label: "Target",
        hideable: true,
        filterType: "date",
        defaultWidth: 110,
        getFilterValue: (r) => r.target,
        render: (r) => formatDateDisplay(r.target),
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
      if (e.key === "Escape" && !successOpen && !pdfOpen) setSelected(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, successOpen, pdfOpen]);

  function confirmWork() {
    if (!selected || selected.done) return;
    markDesignerDone(selected.id);
    setSelected((s) => (s ? { ...s, done: true } : s));
    setSuccessOpen(true);
  }

  return (
    <>
      <DataTable
        tableId="team-tasks:designer-v2"
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
        emptyMessage="Belum ada tugas Designer (DEMO)."
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
                placeholder="Cari PO / pelanggan…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
                aria-label="Cari designer"
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
        title={selected ? `Designer · ${selected.poNo}` : "Designer"}
        subtitle={selected?.customer}
        onClose={() => {
          if (!successOpen) setSelected(null);
        }}
        overlay={
          <ConfirmationSuccessOverlay
            open={successOpen}
            title="Pekerjaan Designer Berhasil"
            message="Simulasi penyelesaian tugas Designer"
            demo
            onClose={() => setSuccessOpen(false)}
          />
        }
        footer={
          <>
            <span className="ep-po-confirm-hint">Aksi DEMO · tidak permanen</span>
            <div className="ep-modal-foot-actions">
              <button
                type="button"
                className="ep-btn"
                onClick={() => setSelected(null)}
                disabled={successOpen}
              >
                Batal
              </button>
              <button
                type="button"
                className="ep-btn sm"
                onClick={() => setPdfOpen(true)}
                disabled={!selected}
              >
                Buka Design
              </button>
              <button
                type="button"
                className="ep-btn primary"
                disabled={!selected || selected.done || successOpen}
                onClick={confirmWork}
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
                ["Status Desain", selected.designStatus],
                ["Target", formatDateDisplay(selected.target)],
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

      <PdfViewerModal
        open={pdfOpen}
        title={selected ? `Design · ${selected.poNo}` : "Design"}
        subtitle={selected?.customer}
        src={selected?.pdfUrl ?? null}
        onClose={() => setPdfOpen(false)}
      />
    </>
  );
}
