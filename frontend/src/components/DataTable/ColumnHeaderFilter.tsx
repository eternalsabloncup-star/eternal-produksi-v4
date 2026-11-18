import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import { formatDateDisplay } from "../../lib/formatDate";
import type { ColumnFilterKind, ColumnHeaderFilterState } from "./types";

type Props = {
  columnId: string;
  label: string;
  filterType: ColumnFilterKind;
  uniqueValues: string[];
  state: ColumnHeaderFilterState;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApply: (next: ColumnHeaderFilterState) => void;
  onClear: () => void;
  onSort: (dir: "asc" | "desc") => void;
};

const POP_WIDTH = 260;
const GAP = 4;
const VIEW_MARGIN = 8;
const MIN_POP_HEIGHT = 160;

function sortLabels(kind: ColumnFilterKind): [string, string] {
  if (kind === "number") {
    return ["Urutkan terkecil → terbesar", "Urutkan terbesar → terkecil"];
  }
  if (kind === "date") {
    return ["Urutkan terlama → terbaru", "Urutkan terbaru → terlama"];
  }
  return ["Urutkan A → Z", "Urutkan Z → A"];
}

function anchorVisible(rect: DOMRect): boolean {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  if (rect.width <= 0 || rect.height <= 0) return false;
  if (rect.bottom < VIEW_MARGIN || rect.top > vh - VIEW_MARGIN) return false;
  if (rect.right < VIEW_MARGIN || rect.left > vw - VIEW_MARGIN) return false;
  return true;
}

export function ColumnHeaderFilter({
  columnId,
  label,
  filterType,
  uniqueValues,
  state,
  open,
  onOpenChange,
  onApply,
  onClear,
  onSort,
}: Props) {
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;

  const [pos, setPos] = useState<CSSProperties>({
    position: "fixed",
    top: 0,
    left: 0,
    width: POP_WIDTH,
    zIndex: 55,
    visibility: "hidden",
  });
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState<Set<string>>(() => new Set(uniqueValues));

  const active = state.selected !== null || state.sortDir !== null;
  const [ascLabel, descLabel] = sortLabels(filterType);

  const filteredList = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return uniqueValues;
    return uniqueValues.filter((v) => {
      if (v === "") return "(kosong)".includes(q);
      const iso = v.toLowerCase();
      if (iso.includes(q)) return true;
      if (filterType === "date") {
        const shown = formatDateDisplay(v, "").toLowerCase();
        return shown !== "" && shown.includes(q);
      }
      return false;
    });
  }, [uniqueValues, search, filterType]);

  const updatePosition = useCallback(() => {
    const btn = btnRef.current;
    const panel = panelRef.current;
    if (!btn || !panel) return;

    const rect = btn.getBoundingClientRect();
    if (!anchorVisible(rect)) {
      onOpenChangeRef.current(false);
      return;
    }

    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(POP_WIDTH, vw - VIEW_MARGIN * 2);

    // Prefer align ke kiri ikon; clamp ke viewport.
    let left = rect.left;
    if (left + width > vw - VIEW_MARGIN) {
      left = vw - VIEW_MARGIN - width;
    }
    if (left < VIEW_MARGIN) left = VIEW_MARGIN;

    const spaceBelow = vh - rect.bottom - VIEW_MARGIN;
    const spaceAbove = rect.top - VIEW_MARGIN;
    const measured = panel.offsetHeight || MIN_POP_HEIGHT;
    const placeBelow =
      spaceBelow >= Math.min(measured, MIN_POP_HEIGHT) ||
      spaceBelow >= spaceAbove;

    let top: number;
    let maxHeight: number;
    if (placeBelow) {
      top = rect.bottom + GAP;
      maxHeight = Math.max(120, spaceBelow - GAP);
    } else {
      maxHeight = Math.max(120, spaceAbove - GAP);
      const used = Math.min(measured, maxHeight);
      top = rect.top - GAP - used;
      if (top < VIEW_MARGIN) top = VIEW_MARGIN;
    }

    setPos({
      position: "fixed",
      top,
      left,
      width,
      maxHeight,
      zIndex: 55,
      visibility: "visible",
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    const selected =
      state.selected === null
        ? new Set(uniqueValues)
        : new Set(state.selected);
    setDraft(selected);
    setSearch("");
  }, [open, state.selected, uniqueValues]);

  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
    // Remeasure after paint (list content may change height).
    const raf = window.requestAnimationFrame(() => updatePosition());
    return () => window.cancelAnimationFrame(raf);
  }, [open, filteredList.length, updatePosition]);

  useEffect(() => {
    if (!open) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onOpenChangeRef.current(false);
    }
    function onPointer(e: MouseEvent) {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      onOpenChangeRef.current(false);
    }
    function onReposition() {
      updatePosition();
    }

    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    window.addEventListener("resize", onReposition);
    // Capture scroll dari container tabel, sidebar, window, dll.
    document.addEventListener("scroll", onReposition, true);

    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
      window.removeEventListener("resize", onReposition);
      document.removeEventListener("scroll", onReposition, true);
    };
  }, [open, updatePosition]);

  function toggleAll(checked: boolean) {
    setDraft(checked ? new Set(filteredList) : new Set());
  }

  function toggleOne(value: string, checked: boolean) {
    setDraft((prev) => {
      const next = new Set(prev);
      if (checked) next.add(value);
      else next.delete(value);
      return next;
    });
  }

  function apply() {
    const allSelected =
      uniqueValues.length > 0 && uniqueValues.every((v) => draft.has(v));
    onApply({
      selected: allSelected ? null : Array.from(draft),
      sortDir: state.sortDir,
    });
    onOpenChange(false);
  }

  const popover =
    open && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={panelRef}
            id={`ep-filter-pop-${columnId}`}
            className="ep-filter-pop"
            style={pos}
            role="dialog"
            aria-label={`Filter kolom ${label}`}
          >
            <button
              type="button"
              className="ep-filter-pop-item"
              onClick={() => {
                onSort("asc");
                onOpenChange(false);
              }}
            >
              {ascLabel}
            </button>
            <button
              type="button"
              className="ep-filter-pop-item"
              onClick={() => {
                onSort("desc");
                onOpenChange(false);
              }}
            >
              {descLabel}
            </button>
            <div className="ep-filter-pop-sep" />
            <input
              className="ep-filter-pop-search"
              type="search"
              placeholder="Cari nilai…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label={`Cari nilai ${label}`}
            />
            <div className="ep-filter-pop-actions">
              <button
                type="button"
                className="ep-btn sm"
                onClick={() => toggleAll(true)}
              >
                Pilih Semua
              </button>
              <button
                type="button"
                className="ep-btn sm"
                onClick={() => toggleAll(false)}
              >
                Kosongkan
              </button>
            </div>
            <div className="ep-filter-pop-list">
              {filteredList.length === 0 ? (
                <div className="ep-filter-pop-empty">Tidak ada nilai</div>
              ) : (
                filteredList.map((value) => {
                  const display =
                    value === ""
                      ? "(Kosong)"
                      : filterType === "date"
                        ? formatDateDisplay(value)
                        : value;
                  return (
                    <label
                      key={value === "" ? "__blank__" : value}
                      className="ep-filter-pop-check"
                    >
                      <input
                        type="checkbox"
                        className="ep-checkbox"
                        checked={draft.has(value)}
                        onChange={(e) => toggleOne(value, e.target.checked)}
                      />
                      <span title={display}>{display}</span>
                    </label>
                  );
                })
              )}
            </div>
            <div className="ep-filter-pop-foot">
              <button type="button" className="ep-btn sm" onClick={onClear}>
                Hapus filter
              </button>
              <div className="ep-filter-pop-foot-right">
                <button
                  type="button"
                  className="ep-btn sm"
                  onClick={() => onOpenChange(false)}
                >
                  Batal
                </button>
                <button
                  type="button"
                  className="ep-btn sm primary"
                  onClick={apply}
                >
                  Terapkan
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={`ep-th-filterbtn${active ? " is-active" : ""}`}
        aria-label={`Filter ${label}`}
        aria-expanded={open}
        aria-controls={`ep-filter-pop-${columnId}`}
        draggable={false}
        onDragStart={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onMouseDown={(e) => {
          e.stopPropagation();
        }}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          onOpenChange(!open);
        }}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
          <path
            d="M1.5 2h9L7 6.2V9.5L5 10.5V6.2L1.5 2z"
            fill="currentColor"
          />
        </svg>
      </button>
      {popover}
    </>
  );
}
