import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
      <div className="ep-filterbar">
        <strong>{roleLabel}</strong>
        <button
          type="button"
          className={filter === "all" ? "ep-btn is-active" : "ep-btn"}
          onClick={() => setFilter("all")}
        >
          Semua ({counts.total})
        </button>
        <button
          type="button"
          className={filter === "done" ? "ep-btn is-active" : "ep-btn"}
          onClick={() => setFilter("done")}
        >
          Sudah Dikerjakan ({counts.done})
        </button>
        <button
          type="button"
          className={filter === "open" ? "ep-btn is-active" : "ep-btn"}
          onClick={() => setFilter("open")}
        >
          Belum Dikerjakan ({counts.open})
        </button>
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Muat ulang
        </button>
      </div>

      {error ? (
        <div className="ep-error" role="alert">
          {error}
        </div>
      ) : null}

      <div className="ep-tblscroll">
        <table className="ep-table">
          <thead>
            <tr>
              <th>Jenis Pekerjaan</th>
              <th>No. PO / SJ</th>
              <th>Item Line</th>
              <th>Qty Target</th>
              <th>Qty Terkonfirmasi</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7}>Memuat tugas dari MongoDB V4…</td>
              </tr>
            ) : tasks.length === 0 ? (
              <tr>
                <td colSpan={7} className="ep-empty">
                  Belum ada tugas {roleLabel} di MongoDB V4.
                </td>
              </tr>
            ) : (
              tasks.map((t) => (
                <tr
                  key={t.task_key}
                  className={t.coordination ? "ep-row-danger" : undefined}
                  onClick={() => void openTask(t)}
                >
                  <td>{t.label || t.task_type}</td>
                  <td>{t.ref.po_no || t.ref.sj_no || "—"}</td>
                  <td>{t.ref.accurate_line_id || "—"}</td>
                  <td>{formatQty(t.qty_target)}</td>
                  <td>{formatQty(t.qty_confirmed)}</td>
                  <td>{statusLabel(t.status)}</td>
                  <td>
                    <button
                      type="button"
                      className="ep-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        void openTask(t);
                      }}
                    >
                      Buka
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

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

              {selected.qty_target !== undefined || selected.task_type === "screen" ? (
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
