import { useEffect } from "react";
import type {
  DeliveryDetail,
  DeliveryScheduleRow,
  LoadingValidation,
} from "../api/delivery";
import {
  deliveryStatusLabel,
  display,
  displayBoolean,
  formatQtyByUnit,
  loadingEventStatusLabel,
  loadingStatusLabel,
  qtyByUnit,
  qtyText,
  revisionStatusLabel,
} from "../lib/deliverySj";
import { formatDateDisplay, formatDateTimeDisplay } from "../lib/formatDate";
import { SjApiLineItems } from "./SjApiLineItems";

type Props = {
  row: DeliveryScheduleRow;
  detail: DeliveryDetail | null;
  validation: LoadingValidation | null;
  busy: boolean;
  error: string | null;
  notice: string | null;
  cancelReason: string;
  onCancelReasonChange: (value: string) => void;
  onConfirmLoading: () => void;
  onCancelLoading: () => void;
  onDiscrepancy: () => void;
  /** Soft-delete bukti (audit tetap). */
  onSoftDeleteEvidence?: (evidenceId: string, reason: string) => void;
  onClose: () => void;
};

/** Popup Detail Surat Jalan — header, rincian barang, dan status operasional. */
export function DeliverySjDetailModal({
  row,
  detail,
  validation,
  busy,
  error,
  notice,
  cancelReason,
  onCancelReasonChange,
  onConfirmLoading,
  onCancelLoading,
  onDiscrepancy,
  onSoftDeleteEvidence,
  onClose,
}: Props) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const note = detail?.delivery_note ?? row;
  const items = note.items || [];
  const header: [string, string][] = [
    ["Nomor SJ", display(note.sj_no)],
    ["No. PO (Sales Order Accurate)", display(note.po_no)],
    ["No. PO Pelanggan", display(note.customer_po_no)],
    ["Outlet / Pelanggan", display(note.customer_name)],
    ["ID Pelanggan", display(note.customer_no)],
    ["Alamat Lengkap", display(note.destination)],
    ["Kontak", display(note.contact_info)],
    ["Telepon Kontak", display(note.contact_phone)],
    ["Penjual", display(note.salesman_name)],
    ["Pembuat Data", display(note.created_by_user_name)],
    ["Ekspedisi", display(note.shipment)],
    ["Zona Pengiriman", display(note.ship_zone)],
    ["Cabang", display(note.branch_name)],
    ["Tanggal SJ", formatDateDisplay(note.sj_date || note.ship_date)],
    ["Tanggal Kirim", formatDateDisplay(note.ship_date)],
    ["Dicetak/Email oleh", display(note.printed_by_user)],
    ["Waktu Cetak/Email", formatDateTimeDisplay(note.printed_time)],
    ["No. Pengepakan", display(note.packing_number)],
    ["Status Accurate", display(note.accurate_status_name)],
    ["Memiliki Dokumen", displayBoolean(note.attachment_exist)],
    ["Keterangan", display(note.description)],
  ];

  const absentFields = note.source_meta?.absent_fields || [];
  const revisions = detail?.revisions || [];
  const loadings = detail?.loadings || [];
  const events = detail?.events || [];
  const photos = detail?.evidence?.photos || [];
  const signatures = detail?.evidence?.signatures || [];
  const lastEvent = events.length > 0 ? events[events.length - 1] : null;
  const cancelledLoadings = loadings.filter((l) => l.status === "dibatalkan");

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
        aria-label={`Detail Surat Jalan ${note.sj_no}`}
        data-testid="sj-detail-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">Detail Surat Jalan · Accurate</div>
            <h2>{note.sj_no}</h2>
            <div className="ep-sub">
              {display(note.customer_name)}
              <span className="ep-flow-meta-sep" aria-hidden>
                ·
              </span>
              <span data-testid="revision-status">
                {revisionStatusLabel(
                  validation?.revision_status || note.revision_status,
                )}
              </span>
            </div>
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>

        <div className="ep-modal-body">
          {error ? <p className="ep-error">{error}</p> : null}
          {notice ? <p className="ep-notice">{notice}</p> : null}
          {validation?.needs_reloading ? (
            <p className="ep-error" data-testid="needs-reloading">
              Surat Jalan direvisi setelah Loading. Batalkan Loading lama agar Qty
              QC kembali, lalu konfirmasi Loading ulang.
            </p>
          ) : null}

          <div className="ep-sectionlabel">Header Surat Jalan</div>
          <div className="ep-po-info-grid" data-testid="sj-detail-header">
            {header.map(([label, value]) => (
              <div key={label} className="ep-po-info-cell">
                <div className="lbl">{label}</div>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          {absentFields.length > 0 ? (
            <p className="ep-note" data-testid="sj-absent-fields">
              Tidak dikirim Accurate pada SJ ini: {absentFields.join(", ")}.
              Nilainya ditampilkan kosong, bukan diisi asumsi.
            </p>
          ) : null}
          {note.source_meta?.char_field3_zone_unproven ? (
            <p className="ep-note">
              Zona Pengiriman berasal dari <code>charField3</code> Accurate;
              semantik nilainya belum diverifikasi Owner.
            </p>
          ) : null}

          <div className="ep-sectionlabel">
            Rincian Barang · {items.length} baris · Total{" "}
            {formatQtyByUnit(qtyByUnit(items))}
          </div>
          <SjApiLineItems items={items} />

          <div className="ep-sectionlabel">Operasional</div>
          <div className="ep-po-info-grid" data-testid="sj-detail-operational">
            <div className="ep-po-info-cell">
              <div className="lbl">Status Loading</div>
              <strong>{loadingStatusLabel(row)}</strong>
            </div>
            <div className="ep-po-info-cell">
              <div className="lbl">Status Pengiriman</div>
              <strong>{deliveryStatusLabel(row.delivery_status)}</strong>
            </div>
            <div className="ep-po-info-cell">
              <div className="lbl">Driver</div>
              <strong>{display(lastEvent?.courier_email || row.driver_email)}</strong>
            </div>
            <div className="ep-po-info-cell">
              <div className="lbl">Penerima</div>
              <strong>
                {display(lastEvent?.recipient_name || row.recipient_name)}
              </strong>
            </div>
            <div className="ep-po-info-cell">
              <div className="lbl">Versi SJ</div>
              <strong>{validation?.sj_version ?? note.sj_version ?? "—"}</strong>
            </div>
            <div className="ep-po-info-cell">
              <div className="lbl">Status Pembatalan / Loading Ulang</div>
              <strong>
                {validation?.needs_reloading
                  ? "Perlu Loading ulang"
                  : cancelledLoadings.length > 0
                    ? `${cancelledLoadings.length} Loading dibatalkan`
                    : "Tidak ada pembatalan"}
              </strong>
            </div>
          </div>

          {revisions.length > 0 ? (
            <>
              <div className="ep-sectionlabel">Riwayat Revisi SJ</div>
              <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
                <thead>
                  <tr>
                    <th>Versi</th>
                    <th>Item Line</th>
                    <th>Qty Sebelum</th>
                    <th>Qty Sesudah</th>
                    <th>Perubahan</th>
                  </tr>
                </thead>
                <tbody>
                  {revisions.flatMap((rev) =>
                    rev.changed_items.map((change) => (
                      <tr key={`${rev.version}-${change.sj_line_id}`}>
                        <td>{rev.version}</td>
                        <td>{display(change.accurate_line_id)}</td>
                        <td>{change.qty_before ?? "—"}</td>
                        <td>{change.qty_after ?? "—"}</td>
                        <td>{change.change}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </>
          ) : null}

          {loadings.length > 0 ? (
            <>
              <div className="ep-sectionlabel">Riwayat Loading</div>
              <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
                <thead>
                  <tr>
                    <th>Loading</th>
                    <th>Versi SJ</th>
                    <th>Status</th>
                    <th>Petugas</th>
                    <th>Dibatalkan oleh</th>
                    <th>Alasan</th>
                  </tr>
                </thead>
                <tbody>
                  {loadings.map((event) => (
                    <tr
                      key={event.loading_id}
                      className={
                        event.status === "dibatalkan" ? "ep-row-muted" : undefined
                      }
                    >
                      <td>{event.sequence}</td>
                      <td>{event.sj_version}</td>
                      <td>{loadingEventStatusLabel(event.status)}</td>
                      <td>{display(event.actor_email)}</td>
                      <td>{display(event.cancelled_by)}</td>
                      <td>{display(event.cancel_reason)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}

          {events.length > 0 ? (
            <>
              <div className="ep-sectionlabel">Riwayat Pengiriman &amp; Bukti</div>
              <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
                <thead>
                  <tr>
                    <th>Percobaan</th>
                    <th>Status</th>
                    <th>Driver</th>
                    <th>Penerima</th>
                    <th>Foto</th>
                    <th>Alasan Gagal</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => (
                    <tr key={event.attempt}>
                      <td>{event.attempt}</td>
                      <td>{deliveryStatusLabel(event.status)}</td>
                      <td>{display(event.courier_email)}</td>
                      <td>{display(event.recipient_name)}</td>
                      <td>{event.photo_evidence_ids.length}</td>
                      <td>{display(event.failure_reason)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="ep-po-confirm-hint">
                Bukti aktif: {photos.length} foto · {signatures.length} tanda tangan
              </p>
              {onSoftDeleteEvidence && (photos.length > 0 || signatures.length > 0) ? (
                <div className="ep-filterbar" data-testid="soft-delete-evidence">
                  <span className="ep-po-confirm-hint">
                    Soft delete menyembunyikan bukti dari daftar aktif; file &amp; audit
                    tetap tersimpan.
                  </span>
                  {[...photos, ...signatures].map((ev) => (
                    <button
                      key={ev.evidence_id}
                      type="button"
                      className="ep-btn sm"
                      disabled={busy}
                      title={ev.filename || ev.evidence_id}
                      onClick={() => {
                        const reason = window.prompt(
                          "Alasan soft-delete bukti (wajib, min. 3 karakter)",
                        );
                        if (!reason || reason.trim().length < 3) return;
                        onSoftDeleteEvidence(ev.evidence_id, reason.trim());
                      }}
                    >
                      Soft-delete {ev.filename || ev.evidence_id.slice(0, 8)}
                    </button>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}

          <div className="ep-sectionlabel">Validasi Qty Loading</div>
          {validation === null ? (
            <p className="ep-empty">Memuat validasi…</p>
          ) : (
            <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
              <thead>
                <tr>
                  <th>Item Line</th>
                  <th>Proses</th>
                  <th>Qty SJ</th>
                  <th>Qty Tersedia</th>
                  <th>Cocok</th>
                  <th>Keterangan</th>
                </tr>
              </thead>
              <tbody>
                {validation.items.map((item) => (
                  <tr
                    key={item.sj_line_id}
                    className={item.matched ? undefined : "ep-row-danger"}
                  >
                    <td>{item.accurate_line_id}</td>
                    <td>{display(item.process_kind)}</td>
                    <td>{qtyText(item.qty)}</td>
                    <td>{qtyText(item.qty_available)}</td>
                    <td>{item.matched ? "Ya" : "Tidak"}</td>
                    <td>{display(item.reason)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {validation?.already_loaded && !validation.delivered ? (
            <div className="ep-filterbar" data-testid="cancel-loading-block">
              <label htmlFor="cancel-reason">Alasan pembatalan</label>
              <input
                id="cancel-reason"
                value={cancelReason}
                onChange={(e) => onCancelReasonChange(e.target.value)}
              />
              <button
                type="button"
                className="ep-btn"
                disabled={busy}
                onClick={onCancelLoading}
              >
                Batalkan Loading
              </button>
            </div>
          ) : null}
        </div>

        <div className="ep-modal-foot">
          <span className="ep-po-confirm-hint">
            Seluruh nilai berasal dari snapshot Accurate dan ledger operasional V4
          </span>
          <div className="ep-modal-foot-actions">
            {validation && !validation.all_matched ? (
              <button
                type="button"
                className="ep-btn"
                disabled={busy}
                onClick={onDiscrepancy}
              >
                Perbedaan Qty
              </button>
            ) : null}
            <button type="button" className="ep-btn" onClick={onClose}>
              Tutup
            </button>
            <button
              type="button"
              className="ep-btn primary"
              disabled={busy || !validation?.all_matched || row.loaded}
              title={
                row.loaded
                  ? "SJ ini sudah di-Loading"
                  : validation?.all_matched
                    ? "Konfirmasi Loading untuk seluruh item SJ"
                    : "Qty item belum cocok dengan SJ"
              }
              onClick={onConfirmLoading}
            >
              Konfirmasi Loading
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
