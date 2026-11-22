/**
 * Eternal Mobile Compact Work Sheet — presentational helpers.
 * Tidak mengubah permission / handler bisnis.
 */

import { useId, useState, type ReactNode } from "react";

type CollapsibleProps = {
  title: string;
  children: ReactNode;
  /** Default collapsed on mobile worksheets. */
  defaultOpen?: boolean;
  testId?: string;
  className?: string;
  count?: number;
};

/** Riwayat / detail sekunder — default tertutup. */
export function CollapsibleTaskSection({
  title,
  children,
  defaultOpen = false,
  testId,
  className = "",
  count,
}: CollapsibleProps) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  return (
    <div
      className={`ep-ws-collapse${open ? " is-open" : ""} ${className}`.trim()}
      data-testid={testId}
    >
      <button
        type="button"
        className="ep-ws-collapse-toggle"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="ep-ws-collapse-title">
          {title}
          {count != null ? (
            <span className="ep-ws-collapse-count">{count}</span>
          ) : null}
        </span>
        <span className="ep-ws-collapse-chevron" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open ? (
        <div className="ep-ws-collapse-body" id={panelId}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

type SegmentOption = {
  id: string;
  label: string;
  /** Destructive tone when selected (e.g. Gagal). */
  tone?: "default" | "danger";
};

type SegmentProps = {
  options: SegmentOption[];
  value: string;
  onChange: (id: string) => void;
  testId?: string;
};

/** Segmented control (Driver Berhasil/Gagal). */
export function WorkSheetSegment({
  options,
  value,
  onChange,
  testId,
}: SegmentProps) {
  return (
    <div className="ep-ws-segment" role="tablist" data-testid={testId}>
      {options.map((opt) => {
        const active = value === opt.id;
        const danger = opt.tone === "danger";
        return (
          <button
            key={opt.id}
            type="button"
            role="tab"
            aria-selected={active}
            className={`ep-ws-segment-btn${active ? " is-active" : ""}${
              danger ? " is-danger" : ""
            }`}
            onClick={() => onChange(opt.id)}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

type StackRowProps = {
  label: string;
  value: ReactNode;
  emphasize?: boolean;
  danger?: boolean;
  /** Primary KPI figure (larger tabular value). */
  kpi?: boolean;
};

/**
 * Label / colon / value — 3-column grid for worksheet info rows.
 * Not for buttons, pills, or form field labels.
 */
export function WorkSheetStackRow({
  label,
  value,
  emphasize,
  danger,
  kpi,
}: StackRowProps) {
  return (
    <div
      className={`ep-ws-stack-row${emphasize ? " is-emphasize" : ""}${
        danger ? " is-danger" : ""
      }${kpi ? " is-kpi" : ""}`}
    >
      <span className="ep-ws-stack-lbl">{label}</span>
      <span className="ep-ws-stack-colon" aria-hidden="true">
        :
      </span>
      <span className="ep-ws-stack-val">{value}</span>
    </div>
  );
}

type CardProps = {
  children: ReactNode;
  className?: string;
  testId?: string;
};

export function WorkSheetCard({ children, className = "", testId }: CardProps) {
  return (
    <div className={`ep-ws-card ${className}`.trim()} data-testid={testId}>
      {children}
    </div>
  );
}
