import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDashboard, type DashboardMetrics } from "../api/analytics";
import { KpiSummary, type KpiItem } from "../components/KpiSummary";

function qtyByUnit(map: Record<string, number> | undefined): string {
  const entries = Object.entries(map || {});
  if (entries.length === 0) return "0";
  return entries
    .map(([unit, value]) => `${value.toLocaleString("id-ID")} ${unit}`)
    .join(" + ");
}

/** Dashboard operasional — KPI dari backend, tanpa angka simulasi. */
export function DashboardPage() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMetrics(await fetchDashboard());
    } catch (err) {
      setMetrics(null);
      setError(err instanceof Error ? err.message : "Gagal memuat Dashboard.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const kpis = useMemo((): KpiItem[] => {
    if (!metrics) return [];
    return [
      { label: "PO Masuk", value: metrics.po_total, foot: "Accurate · MongoDB V4" },
      {
        label: "Menunggu Gate Design",
        value: metrics.po_waiting_design_gate,
        foot: "PO belum approved desain",
        kind: "warn",
      },
      {
        label: "Menunggu Gate Payment",
        value: metrics.po_waiting_payment_gate,
        foot: "termasuk status review",
        kind: "warn",
      },
      {
        label: "Item Siap Dijadwalkan",
        value: metrics.lines_ready_to_schedule,
        foot: "kedua gate terbuka",
      },
      {
        label: "Tugas Belum Dikerjakan",
        value: metrics.tasks_open,
        foot: "seluruh role",
      },
      {
        label: "Qty Cetak Terkonfirmasi",
        value: qtyByUnit(metrics.qty_printed_by_unit),
        foot: "per satuan",
      },
      {
        label: "Qty QC & Packing",
        value: qtyByUnit(metrics.qty_packed_by_unit),
        foot: "per satuan",
      },
      {
        label: "SJ Sudah Loading",
        value: `${metrics.sj_loaded}/${metrics.sj_total}`,
        foot: "Surat Jalan Accurate",
      },
      {
        label: "Pengiriman Berhasil",
        value: metrics.sj_delivered_success,
        foot: `${metrics.sj_delivered_failed} gagal`,
      },
    ];
  }, [metrics]);

  if (loading) {
    return <p>Memuat Dashboard dari MongoDB V4…</p>;
  }

  if (error) {
    return (
      <div className="ep-placeholder">
        <h3>Dashboard tidak dapat dimuat</h3>
        <p>{error}</p>
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Coba lagi
        </button>
      </div>
    );
  }

  return (
    <div data-testid="dashboard-api">
      <KpiSummary items={kpis} />
      <div className="ep-filterbar">
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Muat ulang
        </button>
      </div>
      {metrics?.limitations?.length ? (
        <div className="ep-notice">
          <strong>Keterbatasan definisi KPI:</strong>
          <ul>
            {metrics.limitations.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
