import { useEffect, useMemo, useState } from "react";
import {
  filterByDone,
  getRoleDoneCounts,
  listHelperTasks,
  markHelperDone,
  subscribeTeamTaskWork,
  type DoneFilter,
  type HelperCategory,
  type HelperTask,
} from "../demo/teamTaskWork";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { TeamTaskDoneFilter } from "./TeamTaskDoneFilter";
import { TaskWorkInfoGrid, TaskWorkModalShell } from "./TaskWorkModalShell";

export function HelperTaskPanel() {
  const [tick, setTick] = useState(0);
  const [category, setCategory] = useState<HelperCategory>("prep-cup");
  const [doneFilter, setDoneFilter] = useState<DoneFilter>("all");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [selected, setSelected] = useState<HelperTask | null>(null);
  const [successOpen, setSuccessOpen] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => subscribeTeamTaskWork(() => setTick((t) => t + 1)), []);

  const counts = useMemo(() => {
    void tick;
    return getRoleDoneCounts("helper", category);
  }, [tick, category]);

  const allRows = useMemo(() => {
    void tick;
    let rows = filterByDone(listHelperTasks(category), doneFilter);
    const qq = q.trim().toLowerCase();
    if (qq) {
      rows = rows.filter(
        (r) =>
          r.poNo.toLowerCase().includes(qq) ||
          r.customer.toLowerCase().includes(qq) ||
          r.itemName.toLowerCase().includes(qq),
      );
    }
    return rows;
  }, [tick, category, doneFilter, q]);

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const columns: ColumnDef<HelperTask>[] = useMemo(
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
        defaultWidth: 180,
        getFilterValue: (r) => r.itemName,
        render: (r) => r.itemName,
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

  const catLabel =
    category === "prep-cup" ? "Persiapan Cup" : "QC & Packing";

  return (
    <>
      <DataTable
        key={category}
        tableId={`team-tasks:helper-${category}`}
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
        emptyMessage={`Belum ada tugas Helper · ${catLabel} (DEMO).`}
        filterSlot={(tools) => (
          <div className="ep-filterbar is-compact ep-task-filterwrap">
            <div className="ep-task-donebar" role="tablist" aria-label="Kategori Helper">
              <button
                type="button"
                className={`ep-task-donechip${category === "prep-cup" ? " is-active" : ""}`}
                aria-pressed={category === "prep-cup"}
                onClick={() => {
                  setCategory("prep-cup");
                  setPage(1);
                  setSelected(null);
                }}
              >
                Persiapan Cup
              </button>
              <button
                type="button"
                className={`ep-task-donechip${category === "qc-packing" ? " is-active" : ""}`}
                aria-pressed={category === "qc-packing"}
                onClick={() => {
                  setCategory("qc-packing");
                  setPage(1);
                  setSelected(null);
                }}
              >
                QC & Packing
              </button>
            </div>
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
                placeholder="Cari PO / item…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
                aria-label="Cari helper"
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
        title={selected ? `Helper · ${catLabel}` : "Helper"}
        subtitle={
          selected ? `${selected.poNo} · ${selected.itemName}` : undefined
        }
        onClose={() => {
          if (!successOpen) setSelected(null);
        }}
        overlay={
          <ConfirmationSuccessOverlay
            open={successOpen}
            title={`Pekerjaan ${catLabel} Berhasil`}
            message="Simulasi penyelesaian tugas Helper"
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
                  markHelperDone(selected.id);
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
                ["Kategori", catLabel],
                ["No. PO", selected.poNo],
                ["Pelanggan", selected.customer],
                ["Nama Barang", selected.itemName],
                ["Item ID", selected.itemId],
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
