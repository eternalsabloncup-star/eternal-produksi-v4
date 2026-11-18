import { useEffect, useMemo } from "react";
import type { DeliveryScheduleRow } from "../api/delivery";
import type { ShipDateFilterValue } from "../demo/deliverySjDemo";
import { formatShipDateFilterLabel } from "../demo/deliverySjDemo";
import {
  deliveryStatusLabel,
  display,
  formatQtyByUnit,
  loadingStatusLabel,
  rowTotalQtyDisplay,
  scheduleMetrics,
} from "../lib/deliverySj";
import { formatDateDisplay } from "../lib/formatDate";
import { KpiSummary, type KpiItem } from "./KpiSummary";

type Props = {
  open: boolean;
  rows: DeliveryScheduleRow[];
  dateFilter: ShipDateFilterValue;
  onClose: () => void;
};

/** Popup Rincian Pengiriman periode — ringkasan baris yang sedang tampil. */
export function DeliveryPeriodModal({ open, rows, dateFilter, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const periodLabel = useMemo(
    () => formatShipDateFilterLabel(dateFilter, formatDateDisplay),
    [dateFilter],
  );
  const metrics = useMemo(() => scheduleMetrics(rows), [rows]);

  const kpis = useMemo(
    (): KpiItem[] => [
      {
        label: "Jumlah SJ",
        value: metrics.sjCount,
        foot: "Surat Jalan Accurate pada filter aktif",
      },
      {
        label: "Total Kuantitas",
        value: formatQtyByUnit(metrics.qtyByUnit),
        foot: "per satuan, tanpa konversi",
      },
      {
        label: "Jumlah Tujuan",
        value: metrics.destinationCount,
        foot: "alamat kirim unik",
      },
      {
        label: "Sudah Loading",
        value: metrics.loadedCount,
        foot: `${metrics.needsReloadingCount} perlu Loading ulang`,
      },
    ],
    [metrics],
  );

  if (!open) return null;

  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ep-modal ep-delivery-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Rincian Pengiriman"
        data-testid="delivery-period-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">Jadwal Pengiriman · Accurate</div>
            <h2>Rincian Pengiriman</h2>
            <div className="ep-sub">{periodLabel}</div>
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>

        <div className="ep-modal-body">
          <KpiSummary items={kpis} />

          {rows.length === 0 ? (
            <div className="ep-delivery-empty" role="status">
              Tidak ada Surat Jalan pada filter aktif.
            </div>
          ) : (
            <div className="ep-delivery-detail-scroll">
              <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
                <thead>
                  <tr>
                    <th>No. SJ</th>
                    <th>No. PO</th>
                    <th>Outlet</th>
                    <th>Alamat</th>
                    <th>Zona Kirim</th>
                    <th>Tanggal Kirim</th>
                    <th>Total Kuantitas</th>
                    <th>Loading</th>
                    <th>Pengiriman</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.accurate_sj_id}>
                      <td>
                        <span className="key">{row.sj_no}</span>
                      </td>
                      <td>{display(row.po_no)}</td>
                      <td>{display(row.customer_name)}</td>
                      <td>{display(row.destination)}</td>
                      <td>{display(row.ship_zone)}</td>
                      <td>{formatDateDisplay(row.ship_date)}</td>
                      <td>
                        <b>{rowTotalQtyDisplay(row)}</b>
                      </td>
                      <td>{loadingStatusLabel(row)}</td>
                      <td>{deliveryStatusLabel(row.delivery_status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="ep-modal-foot">
          <span className="ep-po-confirm-hint">
            Ringkasan dari sumber yang sama dengan tabel utama
          </span>
          <div className="ep-modal-foot-actions">
            <button type="button" className="ep-btn primary" onClick={onClose}>
              Tutup
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
