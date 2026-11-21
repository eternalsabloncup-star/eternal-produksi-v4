import { useEffect, useState } from "react";
import type {
  DeliveryDetail,
  DeliveryScheduleRow,
  LoadingValidation,
} from "../api/delivery";
import {
  deliveryStatusLabel,
  display,
  formatQtyByUnit,
  loadingEventStatusLabel,
  loadingStatusLabel,
  qtyByUnit,
  qtyText,
  revisionStatusLabel,
} from "../lib/deliverySj";
import { SjApiLineItems } from "./SjApiLineItems";

function qtyOrDash(value: number | null | undefined): string {
  return value == null ? "—" : qtyText(value);
}

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

/** Popup Detail Surat Jalan — rincian barang dan status operasional. */
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
  const revisions = detail?.revisions || [];
  const loadings = detail?.loadings || [];
  const events = detail?.events || [];
  const photos = detail?.evidence?.photos || [];
  const signatures = detail?.evidence?.signatures || [];
  const lastEvent = events.length > 0 ? events[events.length - 1] : null;
  const cancelledLoadings = loadings.filter((l) => l.status === "dibatalkan");
  const [openValidationReasons, setOpenValidationReasons] = useState<
    Record<string, boolean>
  >({});
  const [openLoadingExtras, setOpenLoadingExtras] = useState<
    Record<string, boolean>
  >({});
  const [openDeliveryExtras, setOpenDeliveryExtras] = useState<
    Record<string, boolean>
  >({});

  const revisionRows = revisions.flatMap((rev) =>
    rev.changed_items.map((change) => ({
      key: `${rev.version}-${change.sj_line_id}`,
      version: rev.version,
      accurate_line_id: change.accurate_line_id,
      qty_before: change.qty_before,
      qty_after: change.qty_after,
      change: change.change,
    })),
  );

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

          <div className="ep-sectionlabel ep-sj-items-section-label">
            <span className="ep-sj-items-section-desktop">
              Rincian Barang · {items.length} baris · Total{" "}
              {formatQtyByUnit(qtyByUnit(items))}
            </span>
            <span className="ep-sj-items-section-mobile">
              Rincian Barang — {items.length} Baris
            </span>
          </div>
          <SjApiLineItems items={items} />

          <div className="ep-sectionlabel">Operasional</div>
          <div
            className="ep-po-info-grid ep-sj-ops"
            data-testid="sj-detail-operational"
          >
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
              <div className="ep-sj-tbl-scroll ep-sj-desk-only">
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
                    {revisionRows.map((row) => (
                      <tr key={row.key}>
                        <td>{row.version}</td>
                        <td>{display(row.accurate_line_id)}</td>
                        <td>{qtyOrDash(row.qty_before)}</td>
                        <td>{qtyOrDash(row.qty_after)}</td>
                        <td>{row.change}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul
                className="ep-sj-mobile-list ep-sj-rev-list"
                data-testid="sj-revision-list-mobile"
              >
                {revisionRows.map((row) => (
                  <li key={row.key} className="ep-sj-mobile-row">
                    <div className="ep-sj-mobile-row-main">
                      <div className="ep-sj-mobile-row-copy">
                        <div className="ep-sj-mobile-row-title">
                          Line {display(row.accurate_line_id)} · Versi{" "}
                          {row.version}
                        </div>
                        <div className="ep-sj-mobile-row-meta">
                          Qty: {qtyOrDash(row.qty_before)} →{" "}
                          {qtyOrDash(row.qty_after)}
                        </div>
                        <div className="ep-sj-mobile-row-meta ep-sj-mobile-row-wrap">
                          Perubahan: {row.change}
                        </div>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {loadings.length > 0 ? (
            <>
              <div className="ep-sectionlabel">Riwayat Loading</div>
              <div className="ep-sj-tbl-scroll ep-sj-desk-only">
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
              </div>
              <ul
                className="ep-sj-mobile-list ep-sj-loading-list"
                data-testid="sj-loading-list-mobile"
              >
                {loadings.map((event) => {
                  const cancelBy = (event.cancelled_by || "").trim();
                  const cancelReason = (event.cancel_reason || "").trim();
                  const hasExtra = Boolean(cancelBy || cancelReason);
                  const open = Boolean(openLoadingExtras[event.loading_id]);
                  return (
                    <li
                      key={event.loading_id}
                      className={`ep-sj-mobile-row${
                        event.status === "dibatalkan" ? " is-muted" : ""
                      }`}
                    >
                      <div className="ep-sj-mobile-row-main">
                        <div className="ep-sj-mobile-row-copy">
                          <div className="ep-sj-mobile-row-title">
                            Loading {event.sequence} · Versi SJ {event.sj_version}
                          </div>
                          <div className="ep-sj-mobile-row-meta ep-sj-mobile-row-wrap">
                            Status: {loadingEventStatusLabel(event.status)}
                          </div>
                          <div className="ep-sj-mobile-row-meta ep-sj-mobile-row-wrap">
                            Petugas: {display(event.actor_email)}
                          </div>
                        </div>
                      </div>
                      {hasExtra ? (
                        <>
                          <button
                            type="button"
                            className="ep-sj-item-detail-toggle"
                            aria-expanded={open}
                            onClick={() =>
                              setOpenLoadingExtras((prev) => ({
                                ...prev,
                                [event.loading_id]: !prev[event.loading_id],
                              }))
                            }
                          >
                            {open ? "Sembunyikan Detail" : "Detail Pembatalan"}
                          </button>
                          {open ? (
                            <div className="ep-sj-item-extra">
                              <div className="ep-sj-item-extra-row">
                                <span className="lbl">Dibatalkan oleh</span>
                                <strong>{display(event.cancelled_by)}</strong>
                              </div>
                              <div className="ep-sj-item-extra-row">
                                <span className="lbl">Alasan</span>
                                <strong className="ep-sj-mobile-row-wrap">
                                  {display(event.cancel_reason)}
                                </strong>
                              </div>
                            </div>
                          ) : null}
                        </>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
          ) : null}

          {events.length > 0 ? (
            <>
              <div className="ep-sectionlabel">Riwayat Pengiriman &amp; Bukti</div>
              <div className="ep-sj-tbl-scroll ep-sj-desk-only">
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
              </div>
              <ul
                className="ep-sj-mobile-list ep-sj-delivery-list"
                data-testid="sj-delivery-list-mobile"
              >
                {events.map((event) => {
                  const fail = (event.failure_reason || "").trim();
                  const open = Boolean(openDeliveryExtras[String(event.attempt)]);
                  return (
                    <li key={event.attempt} className="ep-sj-mobile-row">
                      <div className="ep-sj-mobile-row-main">
                        <div className="ep-sj-mobile-row-copy">
                          <div className="ep-sj-mobile-row-title">
                            Percobaan {event.attempt} ·{" "}
                            {deliveryStatusLabel(event.status)}
                          </div>
                          <div className="ep-sj-mobile-row-meta ep-sj-mobile-row-wrap">
                            Driver: {display(event.courier_email)}
                          </div>
                          <div className="ep-sj-mobile-row-meta ep-sj-mobile-row-wrap">
                            Penerima: {display(event.recipient_name)}
                          </div>
                          <div className="ep-sj-mobile-row-meta">
                            Foto: {event.photo_evidence_ids.length}
                          </div>
                        </div>
                      </div>
                      {fail ? (
                        <>
                          <button
                            type="button"
                            className="ep-sj-item-detail-toggle"
                            aria-expanded={open}
                            onClick={() =>
                              setOpenDeliveryExtras((prev) => ({
                                ...prev,
                                [String(event.attempt)]:
                                  !prev[String(event.attempt)],
                              }))
                            }
                          >
                            {open ? "Sembunyikan Alasan" : "Alasan Gagal"}
                          </button>
                          {open ? (
                            <div className="ep-sj-item-extra">
                              <div className="ep-sj-item-extra-row">
                                <span className="lbl">Alasan Gagal</span>
                                <strong className="ep-sj-mobile-row-wrap">
                                  {fail}
                                </strong>
                              </div>
                            </div>
                          ) : null}
                        </>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
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
                          "Alasan soft-delete bukti (wajib, min. 3 karakter). File & audit tetap tersimpan.",
                        );
                        if (!reason || reason.trim().length < 3) {
                          window.alert(
                            "Soft-delete dibatalkan: alasan wajib minimal 3 karakter.",
                          );
                          return;
                        }
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
            <>
              <div className="ep-sj-tbl-scroll ep-sj-desk-only">
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
              </div>
              <ul
                className="ep-sj-mobile-list ep-sj-val-list"
                data-testid="sj-validation-list-mobile"
              >
                {validation.items.map((item) => {
                  const reason = (item.reason || "").trim();
                  const open = Boolean(openValidationReasons[item.sj_line_id]);
                  return (
                    <li
                      key={item.sj_line_id}
                      className={`ep-sj-mobile-row${
                        item.matched ? " is-ok" : " is-bad"
                      }`}
                    >
                      <div className="ep-sj-mobile-row-main">
                        <div className="ep-sj-mobile-row-copy">
                          <div className="ep-sj-mobile-row-title">
                            Line {display(item.accurate_line_id)}{" "}
                            <span
                              className={`ep-sj-val-status${
                                item.matched ? " is-ok" : " is-bad"
                              }`}
                            >
                              {item.matched ? "Cocok" : "Tidak Cocok"}
                            </span>
                          </div>
                          <div className="ep-sj-mobile-row-meta">
                            {display(item.process_kind)}
                          </div>
                          <div className="ep-sj-mobile-row-meta">
                            Qty SJ: {qtyText(item.qty)}
                            {item.unit ? ` ${item.unit}` : ""} · Qty Tersedia:{" "}
                            {qtyText(item.qty_available)}
                            {item.unit ? ` ${item.unit}` : ""}
                          </div>
                        </div>
                      </div>
                      {reason ? (
                        <>
                          <button
                            type="button"
                            className="ep-sj-item-detail-toggle"
                            aria-expanded={open}
                            onClick={() =>
                              setOpenValidationReasons((prev) => ({
                                ...prev,
                                [item.sj_line_id]: !prev[item.sj_line_id],
                              }))
                            }
                          >
                            {open ? "Sembunyikan Keterangan" : "Keterangan"}
                          </button>
                          {open ? (
                            <div className="ep-sj-item-extra">
                              <div className="ep-sj-item-extra-row">
                                <span className="lbl">Keterangan</span>
                                <strong>{reason}</strong>
                              </div>
                            </div>
                          ) : null}
                        </>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
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
