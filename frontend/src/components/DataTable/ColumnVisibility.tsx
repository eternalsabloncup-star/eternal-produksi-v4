import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ColumnDef } from "./types";

type Props<T> = {
  columns: ColumnDef<T>[];
  hidden: Set<string>;
  onChange: (hidden: Set<string>) => void;
  open: boolean;
  onClose: () => void;
  /** Elemen pemicu (tombol Kolom Tabel) untuk posisi popover. */
  anchorEl: HTMLElement | null;
  /** ID kolom yang disembunyikan pada tampilan default. */
  defaultHidden?: string[];
  onResetDefault?: () => void;
};

export function ColumnVisibility<T>({
  columns,
  hidden,
  onChange,
  open,
  onClose,
  anchorEl,
  defaultHidden = [],
  onResetDefault,
}: Props<T>) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number }>({
    top: 0,
    left: 0,
  });

  const hideable = columns.filter((c) => c.hideable !== false);
  const activeCount = hideable.filter((c) => !hidden.has(c.id)).length;
  const allVisible = activeCount === hideable.length;

  useLayoutEffect(() => {
    if (!open || !anchorEl || !panelRef.current) return;
    const margin = 8;
    const rect = anchorEl.getBoundingClientRect();
    const panel = panelRef.current;
    const pw = panel.offsetWidth || 280;
    const ph = panel.offsetHeight || 320;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let left = rect.left;
    if (left + pw > vw - margin) left = Math.max(margin, vw - margin - pw);
    let top = rect.bottom + 6;
    if (top + ph > vh - margin) {
      top = Math.max(margin, rect.top - 6 - ph);
    }
    setPos({ top, left });
  }, [open, anchorEl, hidden, activeCount]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    function onPointer(e: MouseEvent) {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || anchorEl?.contains(t)) return;
      onClose();
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open, onClose, anchorEl]);

  if (!open || typeof document === "undefined") return null;

  function selectAll() {
    onChange(new Set());
  }

  function hideAll() {
    onChange(new Set(hideable.map((c) => c.id)));
  }

  return createPortal(
    <div
      ref={panelRef}
      className="ep-col-pop"
      role="dialog"
      aria-label="Atur kolom tabel"
      style={{ top: pos.top, left: pos.left }}
    >
      <div className="ep-col-pop-head">
        <div>
          <strong>Kolom Tabel</strong>
          <div className="ep-col-pop-count">
            {activeCount}/{hideable.length} aktif
          </div>
        </div>
        <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
          ×
        </button>
      </div>

      <div className="ep-col-pop-actions">
        <button
          type="button"
          className="ep-btn sm"
          onClick={allVisible ? hideAll : selectAll}
        >
          {allVisible ? "Sembunyikan semua" : "Pilih semua"}
        </button>
        <button
          type="button"
          className="ep-btn sm"
          onClick={() => {
            if (onResetDefault) onResetDefault();
            else onChange(new Set(defaultHidden));
          }}
        >
          Reset default
        </button>
      </div>

      <div className="ep-col-pop-list">
        {hideable.map((col) => {
          const checked = !hidden.has(col.id);
          return (
            <label key={col.id} className="ep-col-pop-item">
              <input
                type="checkbox"
                className="ep-checkbox"
                checked={checked}
                onChange={(e) => {
                  const next = new Set(hidden);
                  if (e.target.checked) next.delete(col.id);
                  else next.add(col.id);
                  onChange(next);
                }}
              />
              <span>{col.label}</span>
            </label>
          );
        })}
      </div>
    </div>,
    document.body,
  );
}
