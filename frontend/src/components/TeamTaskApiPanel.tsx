import { useCallback, useEffect, useMemo, useState } from "react";
import {
  confirmTaskQty,
  fetchTaskConfirmations,
  fetchTasks,
  newIdempotencyKey,
  type OperationalRole,
  type OperationalTask,
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

  async function openTask(task: OperationalTask) {
    setSelected(task);
    setQtyInput("");
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await fetchTaskConfirmations(task.task_key);
      setHistory(res.items);
    } catch {
      setHistory([]);
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
      const res = await confirmTaskQty(
        selected.task_key,
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
      const [listed, hist] = await Promise.all([
        fetchTasks({ role, done: filter === "all" ? undefined : filter === "done" }),
        fetchTaskConfirmations(res.task.task_key),
      ]);
      setTasks(listed.items);
      setHistory(hist.items);
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
                <dt>Status</dt>
                <dd>{statusLabel(selected.status)}</dd>
              </dl>

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

              {selected.qty_target !== undefined ? (
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
