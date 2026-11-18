import type { ReactNode } from "react";

type Props = {
  open: boolean;
  title: string;
  kicker?: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
  overlay?: ReactNode;
};

/** Shell popup seragam Tugas Tim. */
export function TaskWorkModalShell({
  open,
  title,
  kicker = "Tugas Tim · DEMO",
  subtitle,
  onClose,
  children,
  footer,
  overlay,
}: Props) {
  if (!open) return null;
  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ep-modal ep-task-work-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">{kicker}</div>
            <h2>{title}</h2>
            {subtitle ? <div className="ep-sub">{subtitle}</div> : null}
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>
        <div className="ep-modal-body">{children}</div>
        <div className="ep-modal-foot">{footer}</div>
        {overlay}
      </div>
    </div>
  );
}

type InfoGridProps = {
  fields: [string, string][];
};

export function TaskWorkInfoGrid({ fields }: InfoGridProps) {
  return (
    <div className="ep-task-info-grid">
      {fields.map(([label, value]) => (
        <div key={label} className="ep-task-info-cell">
          <div className="lbl">{label}</div>
          <strong>{value || "—"}</strong>
        </div>
      ))}
    </div>
  );
}
