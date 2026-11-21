type Props = {
  open: boolean;
  poNo: string;
  resolving: boolean;
  error: string | null;
  onClose: () => void;
  onLater: () => void;
  onApprove: () => void;
};

/** Popup otomatis Approval Design — pola panel Live Tracking. */
export function DesignApprovalReminderModal({
  open,
  poNo,
  resolving,
  error,
  onClose,
  onLater,
  onApprove,
}: Props) {
  if (!open) return null;

  return (
    <div className="elt-dap-shade" role="presentation">
      <div
        className="elt-dap-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="elt-dap-title"
      >
        <div className="elt-dap-head">
          <h2 id="elt-dap-title">Approval Design Diperlukan</h2>
          <button
            type="button"
            className="elt-dap-x"
            aria-label="Tutup"
            disabled={resolving}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="elt-dap-body">
          <p className="elt-dap-po">
            PO <strong>{poNo || "—"}</strong>
          </p>
          <p className="elt-dap-status">Menunggu Approval</p>
          <p className="elt-dap-copy">
            Tinjau dan setujui desain untuk PO ini sebelum produksi lanjut.
          </p>
          {error ? (
            <p className="elt-dap-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="elt-dap-foot">
          <button
            type="button"
            className="elt-dap-btn is-primary"
            disabled={resolving}
            onClick={onApprove}
          >
            {resolving ? "Memuat…" : "Approval Design"}
          </button>
          <button
            type="button"
            className="elt-dap-btn is-secondary"
            disabled={resolving}
            onClick={onLater}
          >
            Nanti Saja
          </button>
        </div>
      </div>
    </div>
  );
}
