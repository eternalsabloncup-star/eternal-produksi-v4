import { useEffect } from "react";
import { createPortal } from "react-dom";

type Props = {
  open: boolean;
  title: string;
  message: string;
  /** Tampilkan penanda DEMO di bawah pesan. */
  demo?: boolean;
  demoLabel?: string;
  /** success (default) | error — pola visual gagal memakai overlay yang sama. */
  tone?: "success" | "error";
  /** Auto-tutup (ms). 0 = manual saja. */
  autoCloseMs?: number;
  /** Render fixed ke document.body (satu-klik Inbox). Default: absolute di parent. */
  global?: boolean;
  onClose: () => void;
};

/**
 * Overlay feedback konfirmasi global Eternal V4.
 * Hanya tampilkan setelah aksi selesai; penutupan tidak menjalankan ulang aksi.
 */
export function ConfirmationSuccessOverlay({
  open,
  title,
  message,
  demo = false,
  demoLabel = "Simulasi DEMO · tidak tersimpan ke server",
  tone = "success",
  autoCloseMs = 0,
  global = false,
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

  useEffect(() => {
    if (!open || !autoCloseMs || autoCloseMs <= 0) return;
    const t = window.setTimeout(() => onClose(), autoCloseMs);
    return () => window.clearTimeout(t);
  }, [open, autoCloseMs, onClose]);

  if (!open) return null;

  const isError = tone === "error";

  const node = (
    <div
      className={`ep-confirm-success${isError ? " is-error" : ""}${global ? " is-global" : ""}`}
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
      aria-label={title}
      data-tone={tone}
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
            {isError ? (
              <path
                className="ep-confirm-success-check"
                d="M22 22 L42 42 M42 22 L22 42"
                fill="none"
              />
            ) : (
              <path
                className="ep-confirm-success-check"
                d="M18 33.5 L28 43 L46 22"
                fill="none"
              />
            )}
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

  if (global && typeof document !== "undefined") {
    return createPortal(node, document.body);
  }
  return node;
}
