import { useEffect, useState } from "react";
import type { PoRow } from "../demo/types";
import { formatDateDisplay } from "../lib/formatDate";

type Props = {
  open: boolean;
  row: PoRow | null;
  busy: boolean;
  canConfirm: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
};

/** Popup konfirmasi Bypass Payment — alasan wajib, identitas PO dari baris Inbox. */
export function InboxBypassPaymentModal({
  open,
  row,
  busy,
  canConfirm,
  onClose,
  onConfirm,
}: Props) {
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (!open) return;
    setReason("");
  }, [open, row?.accurateId, row?.id]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) {
        e.stopPropagation();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, busy, onClose]);

  if (!open || !row) return null;

  const trimmed = reason.trim();
  const reasonValid = trimmed.length >= 3 && trimmed.length <= 500;
  const confirmDisabled = busy || !canConfirm || !reasonValid;

  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="ep-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Konfirmasi Bypass Payment"
        data-testid="inbox-bypass-payment-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">Payment Gate · Pengecualian</div>
            <h2>Konfirmasi Bypass Payment</h2>
            <div className="ep-sub">
              {row.poNo || row.id}
              <span className="ep-flow-meta-sep" aria-hidden>
                ·
              </span>
              {row.customer || "—"}
            </div>
          </div>
          <button
            type="button"
            className="ep-x"
            onClick={onClose}
            disabled={busy}
            aria-label="Tutup"
          >
            ×
          </button>
        </div>

        <div className="ep-modal-body">
          <div className="ep-po-info-grid">
            <div className="ep-po-info-cell">
              <div className="lbl">Nomor PO</div>
              <strong>{row.poNo || row.id}</strong>
            </div>
            <div className="ep-po-info-cell">
              <div className="lbl">Nama Customer</div>
              <strong>{row.customer || "—"}</strong>
            </div>
            <div className="ep-po-info-cell">
              <div className="lbl">Tanggal PO</div>
              <strong>{formatDateDisplay(row.poDate)}</strong>
            </div>
          </div>

          <p className="ep-po-confirm-hint" data-testid="inbox-bypass-warning">
            Bypass membuka Payment Gate sebagai pengecualian berizin. Ini{" "}
            <strong>bukan</strong> menandai pembayaran lunas, tidak membuat
            receipt, dan tidak mengubah Accurate.
          </p>

          <label htmlFor="inbox-bypass-reason" className="ep-sectionlabel">
            Alasan Bypass *
          </label>
          <textarea
            id="inbox-bypass-reason"
            data-testid="inbox-bypass-reason"
            aria-label="Alasan Bypass"
            rows={3}
            maxLength={500}
            value={reason}
            disabled={busy}
            placeholder="Minimal 3 karakter — wajib diisi"
            style={{ width: "100%", boxSizing: "border-box", resize: "vertical" }}
            onChange={(e) => setReason(e.target.value)}
          />
          <p className="ep-muted" style={{ marginTop: 4, fontSize: 11 }}>
            {trimmed.length}/500
            {!reasonValid && trimmed.length > 0
              ? " · minimal 3 karakter"
              : null}
          </p>
        </div>

        <div className="ep-modal-foot">
          <span className="ep-po-confirm-hint">
            Aksi tercatat di audit · memerlukan izin Bypass Payment
          </span>
          <div className="ep-modal-foot-actions">
            <button
              type="button"
              className="ep-btn"
              disabled={busy}
              onClick={onClose}
            >
              Batal
            </button>
            <button
              type="button"
              className={`ep-btn primary${busy ? " is-busy" : ""}`}
              disabled={confirmDisabled}
              aria-busy={busy}
              data-testid="inbox-bypass-confirm"
              onClick={() => {
                if (confirmDisabled) return;
                onConfirm(trimmed);
              }}
            >
              {busy ? (
                <>
                  <span className="ep-btn-spinner" aria-hidden />
                  Memproses...
                </>
              ) : (
                "Konfirmasi"
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
