import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  confirmCoordination,
  confirmDelivered,
  confirmDeliveryFailed,
} from "../api/delivery";
import {
  confirmProductionQty,
  decideSample,
  fetchSamples,
  fetchTaskConfirmations,
  fetchTasks,
  newIdempotencyKey,
  uploadSample,
  type OperationalRole,
  type OperationalTask,
  type ProductionSample,
  type TaskConfirmation,
} from "../api/operational";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { DesignWorkflowPanel } from "./DesignWorkflowPanel";

type Props = {
  role: OperationalRole;
  roleLabel: string;
};

type DoneFilter = "all" | "done" | "open";

function statusLabel(status: OperationalTask["status"]): string {
  if (status === "selesai") return "Selesai";
  if (status === "sebagian") return "Sebagian";
  return "Belum Dikerjakan";
}

function formatQty(n: number | undefined): string {
  if (n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("id-ID");
}

/** Istilah kekurangan Qty mengikuti ketentuan Owner per jenis pekerjaan. */
function shortfallLabel(taskType: string): string {
  if (taskType === "persiapan-cup") return "Belum Disiapkan";
  if (taskType === "qc-packing") return "Belum Di-QC & Packing";
  return "Kekurangan Qty";
}

const SAMPLE_UPLOAD_TASK = "konfirmasi-sample";
const SAMPLE_APPROVAL_TASK = "approval-sampel";
const DRIVER_TASK = "pengiriman";
const DESIGN_TASK = "design";
const COORDINATION_TASKS = new Set([
  "qty-loading-sj-berbeda",
  "pengiriman-gagal-koordinasi",
]);

/** Kanvas tanda tangan penerima → PNG blob. */
function signatureBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

/** Tugas peran dari task engine MongoDB V4 — tanpa fixture DEMO. */
export function TeamTaskApiPanel({ role, roleLabel }: Props) {
  const [tasks, setTasks] = useState<OperationalTask[]>([]);
  const [filter, setFilter] = useState<DoneFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<OperationalTask | null>(null);
  const [history, setHistory] = useState<TaskConfirmation[]>([]);
  const [qtyInput, setQtyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [sampleRows, setSampleRows] = useState<ProductionSample[]>([]);
  const [rejectReason, setRejectReason] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);
  const photosRef = useRef<HTMLInputElement | null>(null);
  const signatureRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const [recipientName, setRecipientName] = useState("");
  const [failureReason, setFailureReason] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchTasks({
        role,
        done: filter === "all" ? undefined : filter === "done",
      });
      setTasks(res.items);
    } catch (err) {
      setTasks([]);
      setError(err instanceof Error ? err.message : "Gagal memuat tugas.");
    } finally {
      setLoading(false);
    }
  }, [role, filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = useMemo(() => {
    const done = tasks.filter((t) => t.status === "selesai").length;
    return { total: tasks.length, done, open: tasks.length - done };
  }, [tasks]);

  const filteredTasks = useMemo(() => {
    const qq = q.trim().toLowerCase();
    if (!qq) return tasks;
    return tasks.filter((t) => {
      const hay = [
        t.label,
        t.task_type,
        t.ref.po_no,
        t.ref.sj_no,
        t.ref.accurate_line_id,
        t.status,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(qq);
    });
  }, [tasks, q]);

  const pageTasks = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredTasks.slice(start, start + pageSize);
  }, [filteredTasks, page, pageSize]);

  const columns: ColumnDef<OperationalTask>[] = useMemo(
    () => [
      {
        id: "taskType",
        label: "Jenis Pekerjaan",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (t) => t.label || t.task_type,
        render: (t) => (
          <span className={t.coordination ? "ep-text-danger" : undefined}>
            {t.label || t.task_type}
          </span>
        ),
      },
      {
        id: "poSj",
        label: "No. PO / SJ",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (t) => t.ref.po_no || t.ref.sj_no || "",
        render: (t) => t.ref.po_no || t.ref.sj_no || "—",
      },
      {
        id: "line",
        label: "Item Line",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (t) => t.ref.accurate_line_id || "",
        render: (t) => t.ref.accurate_line_id || "—",
      },
      {
        id: "qtyTarget",
        label: "Qty Target",
        hideable: true,
        filterType: "number",
        defaultWidth: 100,
        getFilterValue: (t) => String(t.qty_target ?? ""),
        getSortValue: (t) => t.qty_target ?? null,
        render: (t) => formatQty(t.qty_target),
      },
      {
        id: "qtyConfirmed",
        label: "Qty Terkonfirmasi",
        hideable: true,
        filterType: "number",
        defaultWidth: 120,
        getFilterValue: (t) => String(t.qty_confirmed),
        getSortValue: (t) => t.qty_confirmed,
        render: (t) => formatQty(t.qty_confirmed),
      },
      {
        id: "shortfall",
        label: "Selisih Qty",
        hideable: true,
        filterType: "number",
        defaultWidth: 110,
        getFilterValue: (t) =>
          t.qty_target !== undefined
            ? String(Math.max(t.qty_target - t.qty_confirmed, 0))
            : "",
        getSortValue: (t) =>
          t.qty_target !== undefined
            ? Math.max(t.qty_target - t.qty_confirmed, 0)
            : null,
        render: (t) =>
          t.qty_target !== undefined
            ? formatQty(Math.max(t.qty_target - t.qty_confirmed, 0))
            : "—",
      },
      {
        id: "status",
        label: "Status",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (t) => statusLabel(t.status),
        render: (t) => statusLabel(t.status),
      },
      {
        id: "action",
        label: "Aksi",
        hideable: false,
        filterType: "text",
        defaultWidth: 90,
        getFilterValue: () => "",
        render: (t) => (
          <button
            type="button"
            className="ep-btn sm"
            onClick={(e) => {
              e.stopPropagation();
              void openTask(t);
            }}
          >
            Buka
          </button>
        ),
      },
    ],
    [],
  );

  const isSampleTask =
    selected?.task_type === SAMPLE_UPLOAD_TASK ||
    selected?.task_type === SAMPLE_APPROVAL_TASK;

  async function openTask(task: OperationalTask) {
    setSelected(task);
    setQtyInput("");
    setRejectReason("");
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await fetchTaskConfirmations(task.task_key);
      setHistory(res.items);
    } catch {
      setHistory([]);
    }
    const allocationId = task.ref.allocation_id;
    if (
      allocationId &&
      (task.task_type === SAMPLE_UPLOAD_TASK || task.task_type === SAMPLE_APPROVAL_TASK)
    ) {
      try {
        const res = await fetchSamples(allocationId);
        setSampleRows(res.items);
      } catch {
        setSampleRows([]);
      }
    } else {
      setSampleRows([]);
    }
  }

  async function reloadAfterAction(task: OperationalTask) {
    const [listed, hist] = await Promise.all([
      fetchTasks({ role, done: filter === "all" ? undefined : filter === "done" }),
      fetchTaskConfirmations(task.task_key).catch(() => ({ items: [] as TaskConfirmation[] })),
    ]);
    setTasks(listed.items);
    setHistory(hist.items);
    if (task.ref.allocation_id && isSampleTask) {
      const res = await fetchSamples(task.ref.allocation_id).catch(() => ({
        items: [] as ProductionSample[],
      }));
      setSampleRows(res.items);
    }
  }

  async function onUploadSample() {
    const file = fileRef.current?.files?.[0];
    if (!selected?.ref.allocation_id || !file) {
      setActionError("Pilih file sampel terlebih dahulu.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await uploadSample(selected.ref.allocation_id, file);
      setActionNotice(`Sampel versi ${res.sample.version} tersimpan.`);
      if (fileRef.current) fileRef.current.value = "";
      await reloadAfterAction(selected);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Upload sampel gagal.");
    } finally {
      setBusy(false);
    }
  }

  function signaturePos(e: React.PointerEvent<HTMLCanvasElement>) {
    const rect = signatureRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function startSignature(e: React.PointerEvent<HTMLCanvasElement>) {
    const ctx = signatureRef.current?.getContext("2d");
    if (!ctx) return;
    drawingRef.current = true;
    const { x, y } = signaturePos(e);
    ctx.beginPath();
    ctx.moveTo(x, y);
  }

  function moveSignature(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawingRef.current) return;
    const ctx = signatureRef.current?.getContext("2d");
    if (!ctx) return;
    const { x, y } = signaturePos(e);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#17341f";
    ctx.lineTo(x, y);
    ctx.stroke();
  }

  function endSignature() {
    drawingRef.current = false;
  }

  async function onDeliverySuccess() {
    const sjId = selected?.ref.accurate_delivery_note_id;
    const files = Array.from(photosRef.current?.files || []);
    const canvas = signatureRef.current;
    if (!sjId || !canvas) return;
    if (files.length < 2) {
      setActionError("Konfirmasi pengiriman memerlukan minimal 2 foto.");
      return;
    }
    if (!recipientName.trim()) {
      setActionError("Nama penerima wajib diisi.");
      return;
    }
    const signature = await signatureBlob(canvas);
    if (!signature) {
      setActionError("Tanda tangan penerima wajib diisi.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await confirmDelivered(sjId, recipientName.trim(), files, signature);
      setActionNotice(
        `Pengiriman ${res.status} · ${res.photo_evidence_ids.length} foto tersimpan.`,
      );
      if (photosRef.current) photosRef.current.value = "";
      setRecipientName("");
      await reloadAfterAction(selected!);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Konfirmasi pengiriman gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onDeliveryFailed() {
    const sjId = selected?.ref.accurate_delivery_note_id;
    if (!sjId) return;
    if (!failureReason.trim()) {
      setActionError("Alasan pengiriman gagal wajib diisi.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      await confirmDeliveryFailed(sjId, failureReason.trim());
      setActionNotice(
        "Pengiriman ditandai gagal; tugas koordinasi Kepala Produksi dibuat.",
      );
      setFailureReason("");
      await reloadAfterAction(selected!);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Gagal menyimpan kegagalan.");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmCoordination() {
    if (!selected) return;
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      await confirmCoordination(
        selected.task_key,
        newIdempotencyKey(`coord:${selected.task_key}`),
      );
      setActionNotice("Koordinasi administrasi ditandai selesai.");
      await reloadAfterAction(selected);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Konfirmasi koordinasi gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onDecideSample(version: number, approved: boolean) {
    if (!selected?.ref.allocation_id) return;
    if (!approved && !rejectReason.trim()) {
      setActionError("Alasan penolakan wajib diisi.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await decideSample(
        selected.ref.allocation_id,
        version,
        approved,
        approved ? undefined : rejectReason.trim(),
      );
      setActionNotice(
        approved
          ? `Sampel versi ${version} disetujui — cetak massal terbuka.`
          : `Sampel versi ${version} ditolak.`,
      );
      setRejectReason("");
      void res;
      await reloadAfterAction(selected);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Keputusan sampel gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirm() {
    if (!selected) return;
    const qty = Number(qtyInput);
    if (!Number.isFinite(qty) || qty <= 0) {
      setActionError("Qty konfirmasi harus lebih besar dari nol.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await confirmProductionQty(
        selected,
        qty,
        newIdempotencyKey(`${role}:${selected.task_key}`),
      );
      setActionNotice(
        res.duplicate
          ? "Konfirmasi sama sudah tercatat — Qty tidak digandakan."
          : `Qty ${formatQty(qty)} tersimpan di MongoDB V4.`,
      );
      setSelected(res.task);
      setQtyInput("");
      await reloadAfterAction(res.task);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Konfirmasi gagal.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ep-taskpanel" data-testid="team-task-api-panel">
      {error ? (
        <div className="ep-error" role="alert">
          {error}
        </div>
      ) : null}

      <DataTable
        tableId={`po-accurate:tasks-${role}`}
        columns={columns}
        rows={loading ? [] : pageTasks}
        filterSourceRows={filteredTasks}
        rowKey={(t) => t.task_key}
        enableColumnManager
        inlineColumnTools
        page={page}
        pageSize={pageSize}
        total={loading ? 0 : filteredTasks.length}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        onRowActivate={(t) => void openTask(t)}
        emptyMessage={
          loading
            ? `Memuat tugas ${roleLabel} dari MongoDB V4…`
            : `Belum ada tugas ${roleLabel} di MongoDB V4.`
        }
        filterSlot={(tools) => (
          <div className="ep-filterbar is-compact">
            <strong>{roleLabel}</strong>
            <button
              type="button"
              className={filter === "all" ? "ep-btn sm is-active" : "ep-btn sm"}
              onClick={() => {
                setFilter("all");
                setPage(1);
              }}
            >
              Semua ({counts.total})
            </button>
            <button
              type="button"
              className={filter === "done" ? "ep-btn sm is-active" : "ep-btn sm"}
              onClick={() => {
                setFilter("done");
                setPage(1);
              }}
            >
              Sudah ({counts.done})
            </button>
            <button
              type="button"
              className={filter === "open" ? "ep-btn sm is-active" : "ep-btn sm"}
              onClick={() => {
                setFilter("open");
                setPage(1);
              }}
            >
              Belum ({counts.open})
            </button>
            <div className="ep-search">
              <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
                ⌕
              </span>
              <input
                placeholder="Cari PO / SJ / jenis…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
                aria-label="Cari tugas"
              />
            </div>
            <button type="button" className="ep-btn sm" onClick={() => void load()}>
              Muat ulang
            </button>
            <button
              type="button"
              className="ep-btn sm"
              onClick={(e) => tools.openColumns(e.currentTarget)}
            >
              ▥ Kolom Tabel
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

      {selected ? (
        <div
          className="ep-modalshade ep-modalshade-center"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelected(null);
          }}
        >
          <div className="ep-modal" role="dialog" aria-modal="true">
            <header className="ep-modalhead">
              <strong>{selected.label || selected.task_type}</strong>
              <button type="button" className="ep-btn" onClick={() => setSelected(null)}>
                Tutup
              </button>
            </header>
            <div className="ep-modalbody">
              <dl className="ep-kv">
                <dt>No. PO</dt>
                <dd>{selected.ref.po_no || "—"}</dd>
                <dt>Item Line</dt>
                <dd>{selected.ref.accurate_line_id || "—"}</dd>
                <dt>Qty Target</dt>
                <dd>{formatQty(selected.qty_target)}</dd>
                <dt>Qty Terkonfirmasi</dt>
                <dd>{formatQty(selected.qty_confirmed)}</dd>
                {selected.qty_target !== undefined ? (
                  <>
                    <dt>{shortfallLabel(selected.task_type)}</dt>
                    <dd>
                      {formatQty(
                        Math.max(selected.qty_target - selected.qty_confirmed, 0),
                      )}
                    </dd>
                  </>
                ) : null}
                {selected.ref.allocation_id ? (
                  <>
                    <dt>Allocation ID</dt>
                    <dd>{selected.ref.allocation_id}</dd>
                  </>
                ) : null}
                <dt>Status</dt>
                <dd>{statusLabel(selected.status)}</dd>
              </dl>

              {isSampleTask ? (
                <>
                  <p className="ep-sectionlabel">Riwayat Sampel</p>
                  {sampleRows.length === 0 ? (
                    <p className="ep-empty">Belum ada sampel diunggah.</p>
                  ) : (
                    <table className="ep-table">
                      <thead>
                        <tr>
                          <th>Versi</th>
                          <th>File</th>
                          <th>Status</th>
                          <th>Operator</th>
                          <th>Alasan Tolak</th>
                          {selected.task_type === SAMPLE_APPROVAL_TASK ? (
                            <th>Keputusan</th>
                          ) : null}
                        </tr>
                      </thead>
                      <tbody>
                        {sampleRows.map((s) => (
                          <tr key={s.sample_id}>
                            <td>{s.version}</td>
                            <td>{s.filename}</td>
                            <td>{s.status}</td>
                            <td>{s.submitted_by}</td>
                            <td>{s.reject_reason || "—"}</td>
                            {selected.task_type === SAMPLE_APPROVAL_TASK ? (
                              <td>
                                {s.status === "menunggu_approval" ? (
                                  <>
                                    <button
                                      type="button"
                                      className="ep-btn primary"
                                      disabled={busy}
                                      onClick={() => void onDecideSample(s.version, true)}
                                    >
                                      Setujui
                                    </button>
                                    <button
                                      type="button"
                                      className="ep-btn"
                                      disabled={busy}
                                      onClick={() => void onDecideSample(s.version, false)}
                                    >
                                      Tolak
                                    </button>
                                  </>
                                ) : (
                                  "—"
                                )}
                              </td>
                            ) : null}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {selected.task_type === SAMPLE_APPROVAL_TASK ? (
                    <div className="ep-filterbar">
                      <label htmlFor="reject-reason">Alasan tolak</label>
                      <input
                        id="reject-reason"
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                      />
                    </div>
                  ) : (
                    <div className="ep-filterbar">
                      <input
                        ref={fileRef}
                        type="file"
                        aria-label="File sampel"
                        accept="image/png,image/jpeg,image/webp,application/pdf"
                      />
                      <button
                        type="button"
                        className="ep-btn primary"
                        disabled={busy}
                        onClick={() => void onUploadSample()}
                      >
                        {busy ? "Mengunggah…" : "Konfirmasi Upload Sampel"}
                      </button>
                    </div>
                  )}
                </>
              ) : null}

              <p className="ep-sectionlabel">Riwayat Konfirmasi</p>
              {history.length === 0 ? (
                <p className="ep-empty">Belum ada konfirmasi.</p>
              ) : (
                <ul className="ep-history">
                  {history.map((h) => (
                    <li key={h.idempotency_key}>
                      {formatQty(h.qty)} · {h.actor_email} · {h.created_at}
                    </li>
                  ))}
                </ul>
              )}

              {COORDINATION_TASKS.has(selected.task_type) ? (
                <div className="ep-coordination" data-testid="coordination-block">
                  <p className="ep-coordination-message">
                    {String(
                      selected.context?.message ||
                        "Mohon konfirmasi ke administrasi karena qty loading dan SJ berbeda",
                    )}
                  </p>
                  {selected.context?.failure_reason ? (
                    <p>Alasan gagal: {String(selected.context.failure_reason)}</p>
                  ) : null}
                  {Array.isArray(selected.context?.mismatched_items) ? (
                    <table className="ep-table">
                      <thead>
                        <tr>
                          <th>Item Line</th>
                          <th>Qty SJ</th>
                          <th>Qty Tersedia</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(
                          selected.context.mismatched_items as {
                            accurate_line_id: string;
                            qty_sj: number;
                            qty_available: number;
                          }[]
                        ).map((m) => (
                          <tr key={m.accurate_line_id} className="ep-row-danger">
                            <td>{m.accurate_line_id}</td>
                            <td>{formatQty(m.qty_sj)}</td>
                            <td>{formatQty(m.qty_available)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : null}
                  <button
                    type="button"
                    className="ep-btn primary"
                    disabled={busy || selected.status === "selesai"}
                    onClick={() => void onConfirmCoordination()}
                  >
                    Konfirmasi
                  </button>
                </div>
              ) : null}

              {selected.task_type === DRIVER_TASK ? (
                <div data-testid="driver-block">
                  <p className="ep-sectionlabel">Pengiriman Berhasil</p>
                  <div className="ep-filterbar">
                    <label htmlFor="recipient-name">Nama penerima</label>
                    <input
                      id="recipient-name"
                      value={recipientName}
                      onChange={(e) => setRecipientName(e.target.value)}
                    />
                    <input
                      ref={photosRef}
                      type="file"
                      multiple
                      aria-label="Foto bukti pengiriman"
                      accept="image/png,image/jpeg,image/webp"
                    />
                  </div>
                  <canvas
                    ref={signatureRef}
                    width={360}
                    height={140}
                    className="ep-signature"
                    data-testid="delivery-signature"
                    onPointerDown={startSignature}
                    onPointerMove={moveSignature}
                    onPointerUp={endSignature}
                    onPointerLeave={endSignature}
                  />
                  <div className="ep-filterbar">
                    <button
                      type="button"
                      className="ep-btn primary"
                      disabled={busy}
                      onClick={() => void onDeliverySuccess()}
                    >
                      Konfirmasi Pengiriman Berhasil
                    </button>
                  </div>

                  <p className="ep-sectionlabel">Pengiriman Gagal</p>
                  <div className="ep-filterbar">
                    <label htmlFor="failure-reason">Alasan</label>
                    <input
                      id="failure-reason"
                      value={failureReason}
                      onChange={(e) => setFailureReason(e.target.value)}
                    />
                    <button
                      type="button"
                      className="ep-btn"
                      disabled={busy}
                      onClick={() => void onDeliveryFailed()}
                    >
                      Pengiriman Gagal
                    </button>
                  </div>
                </div>
              ) : null}

              {selected.task_type === DESIGN_TASK && selected.ref.accurate_po_id ? (
                <DesignWorkflowPanel
                  accuratePoId={selected.ref.accurate_po_id}
                  poNo={selected.ref.po_no}
                  onChanged={() => void reloadAfterAction(selected)}
                />
              ) : null}

              {selected.qty_target !== undefined &&
              selected.task_type !== DRIVER_TASK &&
              selected.task_type !== DESIGN_TASK &&
              !COORDINATION_TASKS.has(selected.task_type) &&
              !isSampleTask ? (
                <div className="ep-filterbar">
                  <label htmlFor="qty-confirm">Qty</label>
                  <input
                    id="qty-confirm"
                    type="number"
                    min={1}
                    value={qtyInput}
                    onChange={(e) => setQtyInput(e.target.value)}
                  />
                  <button
                    type="button"
                    className="ep-btn primary"
                    disabled={busy}
                    onClick={() => void onConfirm()}
                  >
                    {busy ? "Menyimpan…" : "Konfirmasi"}
                  </button>
                </div>
              ) : null}

              {actionError ? <p className="ep-error">{actionError}</p> : null}
              {actionNotice ? <p className="ep-notice">{actionNotice}</p> : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
