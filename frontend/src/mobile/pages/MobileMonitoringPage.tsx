import { useEffect, useMemo, useState } from "react";
import {
  fetchTasks,
  type OperationalTask,
} from "../../api/operational";
import {
  filterTasksForActor,
  formatCountBadge,
  TASK_LIST_FETCH_LIMIT,
} from "../../components/teamTaskUi";
import { useAuth } from "../../auth";
import { isSuperAdmin } from "../../roles";
import { useMobileOps } from "../MobileOpsContext";
import { buildMonitoringSummary } from "../monitoringSummary";
import { toOperationalRole } from "../resolveOpsRole";

/**
 * Monitoring role-aware read-only.
 * Bukan jalur konfirmasi; angka dari task engine yang sama dengan Tugas Saya.
 */
export function MobileMonitoringPage() {
  const { user, isSuperAdmin: authSuper } = useAuth();
  const ops = useMobileOps();
  const elevated = authSuper || isSuperAdmin(user?.role);
  const [tasks, setTasks] = useState<OperationalTask[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ops?.role) {
      setTasks([]);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const res = await fetchTasks({
          role: toOperationalRole(ops.role!),
          limit: TASK_LIST_FETCH_LIMIT,
        });
        if (cancelled) return;
        const scoped = filterTasksForActor(res.items, {
          isSuperAdmin: elevated,
          actorEmail: user?.email,
          operationalRole: ops.role!.id,
        });
        setTasks(scoped);
        setTruncated(res.items.length >= TASK_LIST_FETCH_LIMIT);
      } catch (err) {
        if (cancelled) return;
        setTasks([]);
        setError(
          err instanceof Error ? err.message : "Gagal memuat ringkasan.",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ops?.role, elevated, user?.email]);

  const summary = useMemo(() => buildMonitoringSummary(tasks), [tasks]);

  if (!ops?.role) {
    return (
      <div className="ep-m-locked" data-testid="mobile-monitoring-locked">
        <h2>Monitoring</h2>
        <p>Peran operasional belum di-assign — ringkasan tidak tersedia.</p>
      </div>
    );
  }

  return (
    <div className="ep-m-monitoring" data-testid="mobile-monitoring">
      <h2>Monitoring — {ops.role.label}</h2>
      <p className="ep-m-monitoring-hint">
        Ringkasan read-only dari tugas yang sama dengan tab Tugas Saya. Bukan
        jalur konfirmasi.
      </p>
      {loading ? <p className="muted">Memuat…</p> : null}
      {error ? (
        <p className="ep-text-danger" role="alert">
          {error}
        </p>
      ) : null}
      {!loading && !error ? (
        <ul className="ep-m-mon-list">
          <li>
            <span>Tugas (Semua)</span>
            <strong>{formatCountBadge(summary.all, truncated)}</strong>
          </li>
          <li>
            <span>Belum Dikerjakan</span>
            <strong>{formatCountBadge(summary.belum, truncated)}</strong>
          </li>
          <li>
            <span>Sebagian</span>
            <strong>{formatCountBadge(summary.sebagian, truncated)}</strong>
          </li>
          <li>
            <span>Selesai / Sudah Dikerjakan</span>
            <strong>{formatCountBadge(summary.selesai, truncated)}</strong>
          </li>
          <li className="is-coord">
            <span>Perlu Koordinasi</span>
            <strong>{formatCountBadge(summary.coordination, truncated)}</strong>
          </li>
          {summary.qtyShortfall != null ? (
            <li>
              <span>Selisih Qty (agregat)</span>
              <strong>{summary.qtyShortfall.toLocaleString("id-ID")}</strong>
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
