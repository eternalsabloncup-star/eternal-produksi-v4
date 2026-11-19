import { useEffect, useState } from "react";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";

type Props = {
  open: boolean;
  poNo: string;
  itemName?: string;
  onClose: () => void;
};

/** Simulasi UI pembuatan link approval — bukan endpoint/token nyata. */
export function ApprovalLinkDemoModal({ open, poNo, itemName, onClose }: Props) {
  const [copied, setCopied] = useState(false);
  const [successOpen, setSuccessOpen] = useState(false);
  const demoLink = `https://demo.eternal.local/approval-sim/${encodeURIComponent(poNo)}?sim=1`;

  useEffect(() => {
    if (!open) {
      setCopied(false);
      setSuccessOpen(false);
      return;
    }
    setCopied(false);
    setSuccessOpen(false);
  }, [open, poNo]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (successOpen) return;
      onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, successOpen]);

  if (!open) return null;

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(demoLink);
      setCopied(true);
      setSuccessOpen(true);
    } catch {
      setCopied(false);
      setSuccessOpen(false);
    }
  }

  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ep-modal ep-approval-link-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Buat Link Approval DEMO"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">Simulasi · DEMO</div>
            <h2>Buat Link Approval</h2>
            <div className="ep-sub">
              {poNo}
              {itemName ? ` · ${itemName}` : ""}
            </div>
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>
        <div className="ep-modal-body">
          <p className="ep-approval-link-note">
            Ini hanya simulasi frontend. Link di bawah tidak aktif, tidak berisi token
            akses nyata, dan tidak membuka approval publik.
          </p>
          <label className="ep-approval-link-label" htmlFor="ep-demo-approval-link">
            Link DEMO
          </label>
          <input
            id="ep-demo-approval-link"
            className="ep-approval-link-input"
            readOnly
            value={demoLink}
          />
        </div>
        <div className="ep-modal-foot">
          <span className="ep-po-confirm-hint">
            {copied ? "Tersalin (DEMO)" : "Aksi DEMO · tidak permanen"}
          </span>
          <div className="ep-modal-foot-actions">
            <button type="button" className="ep-btn" onClick={onClose}>
              Tutup
            </button>
            <button type="button" className="ep-btn primary" onClick={() => void copyLink()}>
              Salin Link
            </button>
          </div>
        </div>

        <ConfirmationSuccessOverlay
          open={successOpen}
          title="Link Approval Berhasil"
          message="Link simulasi siap disalin — bukan approval publik aktif"
          demo
          onClose={() => setSuccessOpen(false)}
        />
      </div>
    </div>
  );
}
