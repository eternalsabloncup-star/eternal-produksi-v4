import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  filterByDone,
  getRoleDoneCounts,
  listDriverTasks,
  markDriverDone,
  subscribeTeamTaskWork,
  type DoneFilter,
  type DriverTask,
} from "../demo/teamTaskWork";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { TeamTaskDoneFilter } from "./TeamTaskDoneFilter";
import { TaskWorkInfoGrid, TaskWorkModalShell } from "./TaskWorkModalShell";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

export function DriverTaskPanel() {
  const [tick, setTick] = useState(0);
  const [doneFilter, setDoneFilter] = useState<DoneFilter>("all");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [selected, setSelected] = useState<DriverTask | null>(null);
  const [successOpen, setSuccessOpen] = useState(false);
  const [photo1, setPhoto1] = useState<string | null>(null);
  const [photo2, setPhoto2] = useState<string | null>(null);
  const [signed, setSigned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);

  useEffect(() => subscribeTeamTaskWork(() => setTick((t) => t + 1)), []);

  const counts = useMemo(() => {
    void tick;
    return getRoleDoneCounts("driver");
  }, [tick]);

  const allRows = useMemo(() => {
    void tick;
    let rows = filterByDone(listDriverTasks(), doneFilter);
    const qq = q.trim().toLowerCase();
    if (qq) {
      rows = rows.filter(
        (r) =>
          r.sjNo.toLowerCase().includes(qq) ||
          r.poNo.toLowerCase().includes(qq) ||
          r.customer.toLowerCase().includes(qq),
      );
    }
    return rows;
  }, [tick, doneFilter, q]);

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const columns: ColumnDef<DriverTask>[] = useMemo(
    () => [
      {
        id: "sjNo",
        label: "No. SJ",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 130,
        getFilterValue: (r) => r.sjNo,
        render: (r) => <span className="key">{r.sjNo}</span>,
      },
      {
        id: "poNo",
        label: "No. PO",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
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
        id: "qtyShip",
        label: "Qty Pengiriman",
        hideable: true,
        filterType: "number",
        defaultWidth: 120,
        getFilterValue: (r) => String(r.qtyShip),
        getSortValue: (r) => r.qtyShip,
        render: (r) => <b>{formatQty(r.qtyShip)}</b>,
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
    if (!selected) {
      setPhoto1(null);
      setPhoto2(null);
      setSigned(false);
      setError(null);
      setSuccessOpen(false);
      return;
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !successOpen) setSelected(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected?.id, successOpen]);

  function readPhoto(
    file: File | undefined,
    setter: (v: string | null) => void,
  ) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("File harus berupa gambar.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setter(typeof reader.result === "string" ? reader.result : null);
      setError(null);
    };
    reader.readAsDataURL(file);
  }

  function clearSignature() {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, c.width, c.height);
    setSigned(false);
  }

  function pointerPos(e: ReactPointerEvent<HTMLCanvasElement>) {
    const c = canvasRef.current!;
    const rect = c.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * c.width,
      y: ((e.clientY - rect.top) / rect.height) * c.height,
    };
  }

  function confirmDriver() {
    if (!selected || selected.done) return;
    const missing: string[] = [];
    if (!photo1) missing.push("Foto bukti 1");
    if (!photo2) missing.push("Foto bukti 2");
    if (!signed) missing.push("Tanda tangan");
    if (missing.length) {
      setError(`Belum lengkap: ${missing.join(", ")}.`);
      return;
    }
    markDriverDone(selected.id);
    setSelected({ ...selected, done: true });
    setSuccessOpen(true);
    setError(null);
  }

  return (
    <>
      <DataTable
        tableId="team-tasks:driver-v2"
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
        emptyMessage="Belum ada Surat Jalan Driver (DEMO)."
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
                placeholder="Cari SJ / PO…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
                aria-label="Cari driver"
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
        title={selected ? `Konfirmasi Pengiriman · ${selected.sjNo}` : "Driver"}
        subtitle={selected ? `${selected.poNo} · ${selected.customer}` : undefined}
        onClose={() => {
          if (!successOpen) setSelected(null);
        }}
        overlay={
          <ConfirmationSuccessOverlay
            open={successOpen}
            title="Konfirmasi Pengiriman Berhasil"
            message="Simulasi konfirmasi SJ — bukti hanya di state sementara"
            demo
            onClose={() => setSuccessOpen(false)}
          />
        }
        footer={
          <>
            <span className="ep-po-confirm-hint">
              Aksi DEMO · foto & tanda tangan tidak diunggah ke server
            </span>
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={() => setSelected(null)}>
                Batal
              </button>
              <button
                type="button"
                className="ep-btn primary"
                disabled={!selected || selected.done || successOpen}
                onClick={confirmDriver}
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
                ["No. SJ", selected.sjNo],
                ["No. PO", selected.poNo],
                ["Pelanggan", selected.customer],
                ["Qty Pengiriman", formatQty(selected.qtyShip)],
                ["Status Kerja", selected.done ? "Sudah Dikerjakan" : "Belum Dikerjakan"],
              ]}
            />

            <div className="ep-driver-proof">
              <label
                className={`ep-alloc-field${error && !photo1 ? " is-invalid" : ""}`}
              >
                <span className="lbl">Foto bukti 1</span>
                <input
                  type="file"
                  accept="image/*"
                  disabled={selected.done}
                  onChange={(e) => readPhoto(e.target.files?.[0], setPhoto1)}
                />
                {photo1 ? (
                  <img src={photo1} alt="Preview foto 1 DEMO" className="ep-driver-preview" />
                ) : (
                  <span className="ep-alloc-date-hint">Belum ada foto 1</span>
                )}
              </label>
              <label
                className={`ep-alloc-field${error && !photo2 ? " is-invalid" : ""}`}
              >
                <span className="lbl">Foto bukti 2</span>
                <input
                  type="file"
                  accept="image/*"
                  disabled={selected.done}
                  onChange={(e) => readPhoto(e.target.files?.[0], setPhoto2)}
                />
                {photo2 ? (
                  <img src={photo2} alt="Preview foto 2 DEMO" className="ep-driver-preview" />
                ) : (
                  <span className="ep-alloc-date-hint">Belum ada foto 2</span>
                )}
              </label>
            </div>

            <div
              className={`ep-driver-sign${error && !signed ? " is-invalid" : ""}`}
            >
              <div className="lbl">Tanda tangan</div>
              <canvas
                ref={canvasRef}
                width={420}
                height={140}
                className="ep-driver-canvas"
                onPointerDown={(e) => {
                  if (selected.done) return;
                  drawing.current = true;
                  const ctx = canvasRef.current?.getContext("2d");
                  if (!ctx) return;
                  const p = pointerPos(e);
                  ctx.strokeStyle = "#123d30";
                  ctx.lineWidth = 2.2;
                  ctx.lineCap = "round";
                  ctx.beginPath();
                  ctx.moveTo(p.x, p.y);
                  (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
                }}
                onPointerMove={(e) => {
                  if (!drawing.current || selected.done) return;
                  const ctx = canvasRef.current?.getContext("2d");
                  if (!ctx) return;
                  const p = pointerPos(e);
                  ctx.lineTo(p.x, p.y);
                  ctx.stroke();
                  setSigned(true);
                }}
                onPointerUp={() => {
                  drawing.current = false;
                }}
              />
              <button
                type="button"
                className="ep-btn sm"
                disabled={selected.done}
                onClick={clearSignature}
              >
                Hapus tanda tangan
              </button>
            </div>

            {error ? (
              <div className="ep-alloc-error" role="alert">
                {error}
              </div>
            ) : null}
          </>
        ) : null}
      </TaskWorkModalShell>
    </>
  );
}
