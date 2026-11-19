import { useEffect, useMemo } from "react";
import {
  formatQtyByUnit,
  formatShipDateFilterLabel,
  getDeliverySjMetrics,
  rowTotalQtyDisplay,
  type DeliverySjRow,
  type ShipDateFilterValue,
} from "../demo/deliverySjDemo";
import { formatDateDisplay } from "../lib/formatDate";
import { KpiSummary, type KpiItem } from "./KpiSummary";

type Props = {
  open: boolean;
  rows: DeliverySjRow[];
  dateFilter: ShipDateFilterValue;
  onClose: () => void;
};

/** Popup Rincian Pengiriman — KPI + daftar SJ dari sumber yang sama dengan tabel. */
export function DeliveryDetailModal({ open, rows, dateFilter, onClose }: Props) {
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

  const metrics = useMemo(() => getDeliverySjMetrics(rows), [rows]);

  const kpis = useMemo((): KpiItem[] => {
    return [
      {
        label: "Jumlah SJ",
        value: metrics.sjCount,
        foot: "Surat Jalan unik (DEMO)",
      },
      {
        label: "Total Kuantitas",
        value: formatQtyByUnit(metrics.qtyByUnit),
        foot: "Per satuan — tidak digabung (DEMO)",
      },
      {
        label: "Jumlah Tujuan",
        value: metrics.destinationCount,
        foot: "Tujuan unik fixture (DEMO)",
      },
    ];
  }, [metrics]);

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
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">Jadwal Pengiriman · DEMO</div>
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
              Tidak ada Surat Jalan pada tanggal yang dipilih.
            </div>
          ) : (
            <div className="ep-delivery-detail-scroll">
              <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
                <thead>
                  <tr>
                    <th>No. SJ</th>
                    <th>No. PO</th>
                    <th>Pelanggan</th>
                    <th>Alamat Pengiriman</th>
                    <th>Zona Kirim</th>
                    <th>Tanggal Kirim</th>
                    <th>Total Kuantitas</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <span className="key">{r.sjNo}</span>
                      </td>
                      <td>{r.poNo}</td>
                      <td>{r.customer}</td>
                      <td>{r.address}</td>
                      <td>{r.shipZone}</td>
                      <td>{formatDateDisplay(r.shipDate)}</td>
                      <td>
                        <b>{rowTotalQtyDisplay(r)}</b>
                      </td>
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
