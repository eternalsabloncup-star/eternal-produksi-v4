import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import type { ShipDateFilterValue } from "../demo/deliverySjDemo";
import { formatDateDisplay } from "../lib/formatDate";

type Props = {
  value: ShipDateFilterValue;
  onChange: (next: ShipDateFilterValue) => void;
};

type DraftMode = "single" | "range";

const POP_WIDTH = 300;
const GAP = 6;
const VIEW_MARGIN = 8;

function summarize(value: ShipDateFilterValue): string {
  if (value.kind === "all") return "Semua Tanggal";
  if (value.kind === "single") return formatDateDisplay(value.date);
  return `${formatDateDisplay(value.start)} – ${formatDateDisplay(value.end)}`;
}

/** Satu kontrol Tanggal Kirim: mode satu tanggal atau rentang. */
export function ShipDateFilter({ value, onChange }: Props) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<DraftMode>(
    value.kind === "range" ? "range" : "single",
  );
  const [single, setSingle] = useState(
    value.kind === "single" ? value.date : "",
  );
  const [start, setStart] = useState(value.kind === "range" ? value.start : "");
  const [end, setEnd] = useState(value.kind === "range" ? value.end : "");
  const [error, setError] = useState<string | null>(null);
  const [pos, setPos] = useState<CSSProperties>({
    position: "fixed",
    top: 0,
    left: 0,
    width: POP_WIDTH,
    zIndex: 60,
    visibility: "hidden",
  });

  useEffect(() => {
    if (!open) return;
    setMode(value.kind === "range" ? "range" : "single");
    setSingle(value.kind === "single" ? value.date : "");
    setStart(value.kind === "range" ? value.start : "");
    setEnd(value.kind === "range" ? value.end : "");
    setError(null);
  }, [open, value]);

  const updatePosition = useCallback(() => {
    const btn = btnRef.current;
    const panel = panelRef.current;
    if (!btn || !panel) return;
    const rect = btn.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(POP_WIDTH, vw - VIEW_MARGIN * 2);
    const measured = panel.offsetHeight || 280;
    const maxHeight = Math.max(180, vh - VIEW_MARGIN * 2);
    const used = Math.min(measured, maxHeight);

    let left = rect.left;
    if (left + width > vw - VIEW_MARGIN) {
      left = Math.max(VIEW_MARGIN, vw - VIEW_MARGIN - width);
    }
    if (left < VIEW_MARGIN) left = VIEW_MARGIN;

    let top = rect.bottom + GAP;
    if (top + used > vh - VIEW_MARGIN) {
      top = Math.max(VIEW_MARGIN, rect.top - GAP - used);
    }

    setPos({
      position: "fixed",
      top,
      left,
      width,
      maxHeight,
      zIndex: 60,
      visibility: "visible",
      overflowY: measured > maxHeight ? "auto" : undefined,
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
  }, [open, mode, updatePosition]);

  useEffect(() => {
    if (!open) return;
    function onScrollOrResize() {
      updatePosition();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (btnRef.current?.contains(t)) return;
      if (panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    window.addEventListener("resize", onScrollOrResize);
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("resize", onScrollOrResize);
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open, updatePosition]);

  function apply() {
    if (mode === "single") {
      if (!single) {
        setError("Pilih satu tanggal kirim.");
        return;
      }
      onChange({ kind: "single", date: single });
      setOpen(false);
      return;
    }
    if (!start || !end) {
      setError("Lengkapi tanggal awal dan akhir.");
      return;
    }
    if (end < start) {
      setError("Tanggal akhir tidak boleh lebih awal dari tanggal awal.");
      return;
    }
    onChange({ kind: "range", start, end });
    setOpen(false);
  }

  function clearFilter() {
    onChange({ kind: "all" });
    setSingle("");
    setStart("");
    setEnd("");
    setError(null);
    setOpen(false);
  }

  const active = value.kind !== "all";

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={`ep-btn sm ep-shipdate-trigger${active ? " is-active" : ""}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
      >
        Tanggal Kirim
        <span className="ep-shipdate-summary">{summarize(value)}</span>
      </button>
      {open
        ? createPortal(
            <div
              ref={panelRef}
              className="ep-filter-pop ep-shipdate-pop"
              role="dialog"
              aria-label="Filter Tanggal Kirim"
              style={pos}
            >
              <div className="ep-shipdate-modes" role="tablist" aria-label="Mode tanggal">
                <button
                  type="button"
                  className={`ep-task-donechip${mode === "single" ? " is-active" : ""}`}
                  aria-pressed={mode === "single"}
                  onClick={() => {
                    setMode("single");
                    setError(null);
                  }}
                >
                  Satu Tanggal
                </button>
                <button
                  type="button"
                  className={`ep-task-donechip${mode === "range" ? " is-active" : ""}`}
                  aria-pressed={mode === "range"}
                  onClick={() => {
                    setMode("range");
                    setError(null);
                  }}
                >
                  Rentang Tanggal
                </button>
              </div>

              {mode === "single" ? (
                <label className="ep-alloc-field">
                  <span className="lbl">Tanggal Kirim</span>
                  <input
                    type="date"
                    value={single}
                    onChange={(e) => {
                      setSingle(e.target.value);
                      setError(null);
                    }}
                  />
                  {single ? (
                    <span className="ep-alloc-date-hint">
                      Tampil: {formatDateDisplay(single)}
                    </span>
                  ) : null}
                </label>
              ) : (
                <div className="ep-shipdate-range">
                  <label className="ep-alloc-field">
                    <span className="lbl">Dari</span>
                    <input
                      type="date"
                      value={start}
                      onChange={(e) => {
                        setStart(e.target.value);
                        setError(null);
                      }}
                    />
                    {start ? (
                      <span className="ep-alloc-date-hint">
                        Tampil: {formatDateDisplay(start)}
                      </span>
                    ) : null}
                  </label>
                  <label className="ep-alloc-field">
                    <span className="lbl">Sampai</span>
                    <input
                      type="date"
                      value={end}
                      min={start || undefined}
                      onChange={(e) => {
                        setEnd(e.target.value);
                        setError(null);
                      }}
                    />
                    {end ? (
                      <span className="ep-alloc-date-hint">
                        Tampil: {formatDateDisplay(end)}
                      </span>
                    ) : null}
                  </label>
                </div>
              )}

              {error ? (
                <div className="ep-alloc-error" role="alert">
                  {error}
                </div>
              ) : null}

              <div className="ep-filter-pop-foot">
                <button type="button" className="ep-btn sm" onClick={clearFilter}>
                  Hapus filter
                </button>
                <div className="ep-filter-pop-foot-right">
                  <button
                    type="button"
                    className="ep-btn sm"
                    onClick={() => setOpen(false)}
                  >
                    Batal
                  </button>
                  <button type="button" className="ep-btn sm primary" onClick={apply}>
                    Terapkan
                  </button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
