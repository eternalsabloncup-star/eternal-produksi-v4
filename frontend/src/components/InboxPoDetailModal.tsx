import { useEffect, useState } from "react";
import type { PoRow } from "../demo/types";
import { formatDateDisplay } from "../lib/formatDate";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

type Props = {
  row: PoRow | null;
  open: boolean;
  onClose: () => void;
};

/** Popup rincian PO Inbox + Konfirmasi DEMO (local state saja). */
export function InboxPoDetailModal({ row, open, onClose }: Props) {
  const [demoConfirmed, setDemoConfirmed] = useState(false);
  const [successOpen, setSuccessOpen] = useState(false);

  useEffect(() => {
    if (!open) {
      setDemoConfirmed(false);
      setSuccessOpen(false);
      return;
    }
    setDemoConfirmed(false);
    setSuccessOpen(false);
  }, [open, row?.id]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (successOpen) return; // overlay menangani Escape
      onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, successOpen]);

  if (!open || !row) return null;

  const orderStatus = row.urgent ? "Urgent" : "Normal";
  const fields: [string, string][] = [
    ["Nomor PO", row.poNo || row.id],
    ["Tanggal PO", formatDateDisplay(row.poDate)],
    ["Pelanggan", row.customer || "—"],
    ["Nama Penerima", row.recipient || "—"],
    ["PIC Alamat", row.addressPicName || "—"],
    ["Alamat", row.address || "—"],
    ["Syarat Pembayaran", row.paymentTerms || "—"],
    ["User Pembuat", row.userName || "—"],
    ["User Cetak", row.printUserName || "—"],
    ["ID Pembuat", row.createdById || "—"],
    ["Ekspedisi", row.expedition || "—"],
    ["Estimasi Pengiriman", formatDateDisplay(row.etaShip)],
    ["Zona Pengiriman", row.shipZone || "—"],
    ["Penjual", row.seller || "—"],
    ["Prioritas", row.priorityLabel || "—"],
    ["Status Pesanan", orderStatus],
    ["Buffer/Non-Buffer", row.bufferLabel || "—"],
    ["Keterangan", row.notes || "—"],
    ["Jumlah Item", String(row.itemCount ?? row.items?.length ?? 0)],
    ["Total Kuantitas", row.totalQtyDisplay || "—"],
    ["Asal Data", row.origin === "accurate" ? "Accurate" : "DEMO"],
  ];

  const items = row.items || [];

  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ep-modal"
        role="dialog"
        aria-modal="true"
        aria-label={
          demoConfirmed ? "PO Berhasil Dikonfirmasi" : `Rincian ${row.id}`
        }
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">
              Rincian PO · {row.origin === "accurate" ? "Accurate" : "DEMO"}
            </div>
            <h2>{row.id}</h2>
            <div className="ep-sub">
              {row.customer}
              <span className="ep-flow-meta-sep" aria-hidden>
                ·
              </span>
              <span
                className={`ep-pill ep-pill-status ${row.urgent ? "order-urgent" : "order-normal"}`}
              >
                {orderStatus}
              </span>
            </div>
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>

        <div className="ep-modal-body">
          <div className="ep-po-info-grid">
            {fields.map(([label, value]) => (
              <div key={label} className="ep-po-info-cell">
                <div className="lbl">{label}</div>
                <strong>{value}</strong>
              </div>
            ))}
          </div>

          <div className="ep-sectionlabel">Item line</div>
          <div className="ep-po-detail-scroll ep-po-modal-items">
            <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
              <thead>
                <tr>
                  <th>Kode Barang</th>
                  <th>Nama Barang</th>
                  <th>Kts.</th>
                  <th>Satuan</th>
                  <th>Proses</th>
                  <th>Warna</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <div className="ep-empty">Tidak ada item (DEMO).</div>
                    </td>
                  </tr>
                ) : (
                  items.map((it) => (
                    <tr key={it.lineId || `${row.sourcePoId}:${it.code}`}>
                      <td>{it.code}</td>
                      <td>{it.name}</td>
                      <td>
                        <b>{formatQty(it.qty)}</b>
                      </td>
                      <td>{it.unit}</td>
                      <td>{it.processKind ?? it.process}</td>
                      <td>{it.color}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="ep-modal-foot">
          <span className="ep-po-confirm-hint">
            {row.origin === "accurate"
              ? "Accurate read-only · tidak ada mutasi dari Inbox"
              : "Aksi DEMO · tidak permanen"}
          </span>
          <div className="ep-modal-foot-actions">
            <button type="button" className="ep-btn" onClick={onClose}>
              Tutup
            </button>
            {row.origin === "demo" ? (
              <button
                type="button"
                className="ep-btn primary"
                disabled={demoConfirmed}
                onClick={() => {
                  setDemoConfirmed(true);
                  setSuccessOpen(true);
                }}
              >
                Konfirmasi PO
              </button>
            ) : null}
          </div>
        </div>

        {row.origin === "demo" ? (
          <ConfirmationSuccessOverlay
            open={successOpen}
            title="PO Berhasil Dikonfirmasi"
            message="Simulasi konfirmasi berhasil"
            demo
            onClose={() => setSuccessOpen(false)}
          />
        ) : null}
      </div>
    </div>
  );
}
