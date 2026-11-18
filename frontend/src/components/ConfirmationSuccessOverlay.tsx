import { useEffect } from "react";

type Props = {
  open: boolean;
  title: string;
  message: string;
  /** Tampilkan penanda DEMO di bawah pesan. */
  demo?: boolean;
  demoLabel?: string;
  onClose: () => void;
};

/**
 * Overlay sukses konfirmasi global Eternal V4.
 * Hanya tampilkan setelah aksi sukses; penutupan tidak menjalankan ulang aksi.
 */
export function ConfirmationSuccessOverlay({
  open,
  title,
  message,
  demo = false,
  demoLabel = "Simulasi DEMO · tidak tersimpan ke server",
  onClose,
}: Props) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="ep-confirm-success"
      role="status"
      aria-live="polite"
      aria-label={title}
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          e.stopPropagation();
          onClose();
        }
      }}
      tabIndex={0}
    >
      <div className="ep-confirm-success-inner">
        <div className="ep-confirm-success-mark" aria-hidden>
          <svg viewBox="0 0 64 64" className="ep-confirm-success-svg">
            <circle className="ep-confirm-success-halo" cx="32" cy="32" r="30" />
            <circle className="ep-confirm-success-ring" cx="32" cy="32" r="26" />
            <path
              className="ep-confirm-success-check"
              d="M18 33.5 L28 43 L46 22"
              fill="none"
            />
          </svg>
        </div>
        <div className="ep-confirm-success-title">{title}</div>
        <div className="ep-confirm-success-sub">{message}</div>
        {demo ? (
          <div className="ep-confirm-success-demo">{demoLabel}</div>
        ) : null}
        <div className="ep-confirm-success-hint">
          Ketuk di mana saja untuk menutup
        </div>
      </div>
    </div>
  );
}
