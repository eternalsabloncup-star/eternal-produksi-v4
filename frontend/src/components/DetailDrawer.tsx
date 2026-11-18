import { useEffect, type ReactNode } from "react";

type Props = {
  open: boolean;
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
};

export function DetailDrawer({
  open,
  title,
  subtitle,
  children,
  footer,
  onClose,
}: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="ep-modalshade"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="ep-drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="ep-drawerhead">
          <div>
            <div className="ep-eyebrow">Eternal Production V4</div>
            <h2>{title}</h2>
            {subtitle ? <div className="ep-sub">{subtitle}</div> : null}
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>
        <div className="ep-drawerbody">{children}</div>
        <div className="ep-drawerfoot">
          {footer ?? (
            <button type="button" className="ep-btn" onClick={onClose}>
              Tutup
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
