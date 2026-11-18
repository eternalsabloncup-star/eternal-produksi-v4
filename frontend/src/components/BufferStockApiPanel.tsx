import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchBufferStock,
  NOT_CONFIGURED,
  type BufferStock,
} from "../api/analytics";
import { KpiSummary, type KpiItem } from "./KpiSummary";

function num(value: number | string): string {
  return typeof value === "number" ? value.toLocaleString("id-ID") : value;
}

/** Buffer Stock — hanya PO beratribut Buffer dari Accurate. */
export function BufferStockApiPanel() {
  const [data, setData] = useState<BufferStock | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchBufferStock());
    } catch (err) {
      setData(null);
      setError(err instanceof Error ? err.message : "Gagal memuat Buffer Stock.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const kpis = useMemo((): KpiItem[] => {
    const kpi = data?.kpi;
    return [
      {
        label: "Total Buffer Dipesan",
        value: num(kpi?.total_buffer_ordered ?? 0),
        foot: "PO beratribut Buffer",
      },
      {
        label: "Sudah Selesai Cetak",
        value: num(kpi?.qty_printed ?? 0),
        foot: "ledger cetak terkonfirmasi",
      },
      {
        label: "Sisa Belum Dicetak",
        value: num(kpi?.qty_remaining ?? 0),
        foot: "Qty PO − Qty cetak",
        kind: "warn",
      },
      {
        label: "Ready Gudang",
        value: num(kpi?.qty_ready_warehouse ?? NOT_CONFIGURED),
        foot: "rumus mutasi belum diputuskan",
      },
    ];
  }, [data]);

  const rows = data?.items || [];

  return (
    <div data-testid="buffer-stock-api-panel">
      <KpiSummary items={kpis} />

      <div className="ep-filterbar">
        <strong>Buffer Stock · MongoDB V4</strong>
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Muat ulang
        </button>
      </div>

      {error ? <p className="ep-error">{error}</p> : null}
      {data && data.not_configured.length > 0 ? (
        <p className="ep-notice">
          Kolom berikut menunggu keputusan Owner: {data.not_configured.join(", ")}.
        </p>
      ) : null}

      <div className="ep-tblscroll">
        <table className="ep-table">
          <thead>
            <tr>
              <th>Nomor PO Buffer</th>
              <th>Outlet/Gudang</th>
              <th>Kode Barang</th>
              <th>Nama Barang</th>
              <th>Warna</th>
              <th>Total Buffer Dipesan</th>
              <th>Sudah Selesai Cetak</th>
              <th>Sisa Belum Dicetak</th>
              <th>Ready Gudang</th>
              <th>Sudah Diambil/Terkirim</th>
              <th>Status Buffer</th>
              <th>Sales PIC</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={12}>Memuat Buffer Stock dari MongoDB V4…</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={12} className="ep-empty">
                  Belum ada PO beratribut Buffer di MongoDB V4.
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={`${row.accurate_po_id}-${row.accurate_line_id}`}>
                  <td>{row.po_no}</td>
                  <td>{row.outlet_warehouse}</td>
                  <td>{row.item_code}</td>
                  <td>{row.item_name}</td>
                  <td>{row.color || "—"}</td>
                  <td>
                    {num(row.qty_ordered)} {row.unit}
                  </td>
                  <td>{num(row.qty_printed)}</td>
                  <td>{num(row.qty_remaining)}</td>
                  <td>{num(row.qty_ready_warehouse)}</td>
                  <td>{num(row.qty_shipped)}</td>
                  <td>{row.buffer_status}</td>
                  <td>{row.sales_pic}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
