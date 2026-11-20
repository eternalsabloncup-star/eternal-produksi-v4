import {
  Fragment,
  useCallback,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { ColumnHeaderFilter } from "./ColumnHeaderFilter";
import { ColumnVisibility } from "./ColumnVisibility";
import { Pagination } from "./Pagination";
import type {
  ColumnDef,
  ColumnHeaderFilterState,
} from "./types";
import { useTableColumnPrefs } from "./useTableColumnPrefs";

export type ColumnManagerApi = {
  openColumns: (anchor?: HTMLElement | null) => void;
  resetPrefs: () => Promise<void>;
  prefsHint: string | null;
  saveState: "idle" | "saving" | "saved" | "error";
};

type Props<T> = {
  /** Kunci preferensi kolom stabil per tabel. */
  tableId: string;
  columns: ColumnDef<T>[];
  rows: T[];
  /** Sumber nilai unik filter; default = rows. */
  filterSourceRows?: T[];
  rowKey: (row: T) => string;
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  emptyMessage?: ReactNode;
  toolbar?: ReactNode;
  /**
   * Filter global. Bisa berupa node, atau fungsi yang menerima API
   * pengelola kolom agar tombol bisa digabung ke satu baris toolbar.
   */
  filterSlot?: ReactNode | ((api: ColumnManagerApi) => ReactNode);
  /** Tampilkan tombol atur kolom + reset preferensi. */
  enableColumnManager?: boolean;
  /**
   * true = tombol kolom digabung ke filterSlot (fungsi), tanpa baris terpisah.
   * false = baris ep-sheet-tools di bawah toolbar (default).
   */
  inlineColumnTools?: boolean;
  /** Render baris detail di bawah baris master (expandable). */
  renderExpandedRow?: (row: T) => ReactNode;
  /** Apakah baris boleh di-expand; default semua jika renderExpandedRow ada. */
  isRowExpandable?: (row: T) => boolean;
  /** Kolom tersembunyi default sebelum preferensi tersimpan. */
  defaultHiddenColumns?: string[];
  /**
   * Klik baris master (bukan kontrol expand/filter/resize/input).
   * Dipakai misalnya membuka popup rincian.
   */
  onRowActivate?: (row: T) => void;
  /**
   * Mode server-side: filter/sort header tidak diterapkan pada halaman lokal.
   * Parent wajib memuat ulang data; uniqueValues boleh dari filterFacets.
   */
  serverSide?: boolean;
  /** Nilai unik checklist header (server); mengalahkan derivasi dari rows. */
  filterFacets?: Record<string, string[]>;
  /** Dipanggil saat filter/sort header berubah (serverSide). */
  onServerHeaderChange?: (
    next: Record<string, ColumnHeaderFilterState>,
  ) => void;
};

function compareValues(
  a: string | number | Date | null | undefined,
  b: string | number | Date | null | undefined,
  dir: "asc" | "desc",
): number {
  const mul = dir === "asc" ? 1 : -1;
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") {
    return (a - b) * mul;
  }
  if (a instanceof Date && b instanceof Date) {
    return (a.getTime() - b.getTime()) * mul;
  }
  return String(a).localeCompare(String(b), "id", { numeric: true }) * mul;
}

export function DataTable<T>({
  tableId,
  columns,
  rows,
  filterSourceRows,
  rowKey,
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  emptyMessage,
  toolbar,
  filterSlot,
  enableColumnManager = false,
  inlineColumnTools = false,
  renderExpandedRow,
  isRowExpandable,
  defaultHiddenColumns,
  onRowActivate,
  serverSide = false,
  filterFacets,
  onServerHeaderChange,
}: Props<T>) {
  const columnIds = useMemo(() => columns.map((c) => c.id), [columns]);
  const {
    order,
    widths,
    hidden,
    loading: prefsLoading,
    saveError,
    saveState,
    setOrder,
    setWidth,
    setHidden,
    resetPrefs,
  } = useTableColumnPrefs(tableId, columnIds, defaultHiddenColumns);

  const [headerFilters, setHeaderFilters] = useState<
    Record<string, ColumnHeaderFilterState>
  >({});
  const [openFilterId, setOpenFilterId] = useState<string | null>(null);
  const [colsOpen, setColsOpen] = useState(false);
  const [colsAnchor, setColsAnchor] = useState<HTMLElement | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [resizingId, setResizingId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const canExpand = Boolean(renderExpandedRow);

  const dragCol = useRef<string | null>(null);
  const resizeRef = useRef<{
    id: string;
    startX: number;
    startW: number;
  } | null>(null);

  const byId = useMemo(() => {
    const map = new Map(columns.map((c) => [c.id, c]));
    return map;
  }, [columns]);

  const visible = useMemo(() => {
    return order
      .map((id) => byId.get(id))
      .filter((c): c is ColumnDef<T> => Boolean(c) && !hidden.has(c!.id));
  }, [order, byId, hidden]);

  const masterColSpan = visible.length + (canExpand ? 1 : 0);

  const toggleExpand = useCallback((key: string) => {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const handleRowActivate = useCallback(
    (row: T, e: ReactMouseEvent) => {
      if (!onRowActivate) return;
      const t = e.target as HTMLElement | null;
      if (!t) return;
      if (
        t.closest(
          "button, a, input, select, textarea, label, .ep-expand-btn, .ep-th-resize, .ep-th-filterbtn, .ep-filter-pop",
        )
      ) {
        return;
      }
      onRowActivate(row);
    },
    [onRowActivate],
  );

  const sourceForUniques = filterSourceRows ?? rows;

  const uniqueByCol = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const col of columns) {
      if (col.filterable === false || !col.getFilterValue) continue;
      if (filterFacets && Object.prototype.hasOwnProperty.call(filterFacets, col.id)) {
        map.set(col.id, filterFacets[col.id] ?? []);
        continue;
      }
      const set = new Set<string>();
      for (const row of sourceForUniques) {
        set.add(col.getFilterValue(row) ?? "");
      }
      map.set(
        col.id,
        Array.from(set).sort((a, b) =>
          a.localeCompare(b, "id", { numeric: true }),
        ),
      );
    }
    return map;
  }, [columns, sourceForUniques, filterFacets]);

  const displayRows = useMemo(() => {
    // Server-side: rows sudah difilter/disort + dipaginasi oleh API.
    if (serverSide) return rows;

    let list = rows;

    const activeFilters = Object.entries(headerFilters).filter(
      ([, st]) => st.selected !== null,
    );
    if (activeFilters.length > 0) {
      list = list.filter((row) =>
        activeFilters.every(([colId, st]) => {
          const col = byId.get(colId);
          if (!col?.getFilterValue || st.selected === null) return true;
          const val = col.getFilterValue(row) ?? "";
          return st.selected.includes(val);
        }),
      );
    }

    const sortEntry = Object.entries(headerFilters).find(
      ([, st]) => st.sortDir != null,
    );
    if (sortEntry) {
      const [colId, st] = sortEntry;
      const col = byId.get(colId);
      const dir = st.sortDir!;
      list = [...list].sort((ra, rb) => {
        const va = col?.getSortValue
          ? col.getSortValue(ra)
          : col?.getFilterValue?.(ra);
        const vb = col?.getSortValue
          ? col.getSortValue(rb)
          : col?.getFilterValue?.(rb);
        return compareValues(va, vb, dir);
      });
    }

    return list;
  }, [rows, headerFilters, byId, serverSide]);

  const onResizeMove = useCallback(
    (e: MouseEvent) => {
      const s = resizeRef.current;
      if (!s) return;
      const col = byId.get(s.id);
      const min = col?.minWidth ?? 72;
      const next = Math.max(min, Math.min(800, s.startW + (e.clientX - s.startX)));
      setWidth(s.id, next);
    },
    [byId, setWidth],
  );

  const onResizeUp = useCallback(() => {
    resizeRef.current = null;
    setResizingId(null);
    document.removeEventListener("mousemove", onResizeMove);
    document.removeEventListener("mouseup", onResizeUp);
  }, [onResizeMove]);

  function startResize(columnId: string, startX: number, startW: number) {
    resizeRef.current = { id: columnId, startX, startW };
    setResizingId(columnId);
    document.addEventListener("mousemove", onResizeMove);
    document.addEventListener("mouseup", onResizeUp);
  }

  function onDragStart(colId: string) {
    dragCol.current = colId;
    setDraggingId(colId);
  }

  function onDragOver(e: DragEvent, _overId: string) {
    e.preventDefault();
  }

  function onDrop(overId: string) {
    const from = dragCol.current;
    dragCol.current = null;
    setDraggingId(null);
    if (!from || from === overId) return;
    const next = [...order];
    const fromIdx = next.indexOf(from);
    const toIdx = next.indexOf(overId);
    if (fromIdx < 0 || toIdx < 0) return;
    next.splice(fromIdx, 1);
    next.splice(toIdx, 0, from);
    setOrder(next);
  }

  function colWidth(col: ColumnDef<T>): number {
    return widths[col.id] ?? col.defaultWidth ?? 120;
  }

  function stickyClass(col: ColumnDef<T>): string | undefined {
    if (col.sticky === "po") return "sticky1";
    if (col.sticky === "customer") return "sticky2";
    return undefined;
  }

  function emitServerHeader(
    next: Record<string, ColumnHeaderFilterState>,
  ) {
    if (serverSide && onServerHeaderChange) onServerHeaderChange(next);
  }

  function patchFilter(colId: string, patch: Partial<ColumnHeaderFilterState>) {
    setHeaderFilters((prev) => {
      const cur = prev[colId] ?? { selected: null, sortDir: null };
      const next = { ...prev, [colId]: { ...cur, ...patch } };
      queueMicrotask(() => emitServerHeader(next));
      return next;
    });
  }

  function setSortOnly(colId: string, dir: "asc" | "desc") {
    setHeaderFilters((prev) => {
      const next: Record<string, ColumnHeaderFilterState> = {};
      for (const [id, st] of Object.entries(prev)) {
        next[id] = { ...st, sortDir: id === colId ? dir : null };
      }
      if (!next[colId]) next[colId] = { selected: null, sortDir: dir };
      else next[colId] = { ...next[colId], sortDir: dir };
      queueMicrotask(() => emitServerHeader(next));
      return next;
    });
  }

  function clearColumnFilter(colId: string) {
    setHeaderFilters((prev) => {
      const next = { ...prev };
      delete next[colId];
      queueMicrotask(() => emitServerHeader(next));
      return next;
    });
    setOpenFilterId(null);
  }

  const prefsHint =
    saveState === "saving"
      ? "Menyimpan tampilan…"
      : saveState === "saved"
        ? "Tampilan tersimpan"
        : saveState === "error"
          ? saveError || "Gagal menyimpan tampilan"
          : prefsLoading
            ? "Memuat tampilan…"
            : null;

  const columnApi: ColumnManagerApi = {
    openColumns: (anchor) => {
      setColsAnchor(anchor ?? null);
      setColsOpen(true);
    },
    resetPrefs,
    prefsHint,
    saveState,
  };

  const filterNode =
    typeof filterSlot === "function" ? filterSlot(columnApi) : filterSlot;

  const showSeparateColumnTools =
    enableColumnManager && !inlineColumnTools;

  return (
    <div className="ep-panel ep-spreadsheet">
      {toolbar}
      {showSeparateColumnTools ? (
        <div className="ep-sheet-tools">
          <button
            type="button"
            className="ep-btn sm"
            onClick={() => setColsOpen(true)}
          >
            ▥ Kolom Tabel
          </button>
          <button type="button" className="ep-btn sm" onClick={() => void resetPrefs()}>
            Reset tampilan
          </button>
          {prefsHint ? (
            <span
              className={`ep-prefs-hint${saveState === "error" ? " is-error" : ""}`}
              role={saveState === "error" ? "alert" : undefined}
            >
              {prefsHint}
            </span>
          ) : null}
        </div>
      ) : !inlineColumnTools && prefsHint && saveState === "error" ? (
        <div className="ep-sheet-tools">
          <span className="ep-prefs-hint is-error" role="alert">
            {prefsHint}
          </span>
        </div>
      ) : null}
      {filterNode}
      <div className="ep-tblscroll">
        <table
          className={`ep-tbl ep-tbl-sheet${canExpand ? " is-expandable" : ""}`}
        >
          <thead>
            <tr className="ep-tbl-headrow">
              {canExpand ? (
                <th
                  className="ep-th-expand sticky-expand"
                  style={{ width: 40, minWidth: 40 }}
                  aria-label="Perluas baris"
                >
                  <div className="ep-th-inner ep-th-inner-expand">
                    <span className="ep-th-label" aria-hidden>
                      {" "}
                    </span>
                  </div>
                </th>
              ) : null}
              {visible.map((col) => (
                <th
                  key={col.id}
                  className={[
                    stickyClass(col),
                    draggingId === col.id ? "is-dragging" : "",
                    resizingId === col.id ? "is-resizing" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  style={{ width: colWidth(col), minWidth: col.minWidth ?? 72 }}
                  onDragOver={(e) => onDragOver(e, col.id)}
                  onDrop={() => onDrop(col.id)}
                >
                  <div className="ep-th-inner">
                    <span
                      className="ep-th-label"
                      title="Seret untuk memindah kolom"
                      draggable
                      onDragStart={() => onDragStart(col.id)}
                      onDragEnd={() => {
                        dragCol.current = null;
                        setDraggingId(null);
                      }}
                    >
                      {col.label}
                    </span>
                    {col.filterable !== false && col.getFilterValue ? (
                      <ColumnHeaderFilter
                        columnId={col.id}
                        label={col.label}
                        filterType={col.filterType ?? "text"}
                        uniqueValues={uniqueByCol.get(col.id) ?? []}
                        state={
                          headerFilters[col.id] ?? {
                            selected: null,
                            sortDir: null,
                          }
                        }
                        open={openFilterId === col.id}
                        onOpenChange={(open) =>
                          setOpenFilterId(open ? col.id : null)
                        }
                        onApply={(next) => patchFilter(col.id, next)}
                        onClear={() => clearColumnFilter(col.id)}
                        onSort={(dir) => setSortOnly(col.id, dir)}
                      />
                    ) : null}
                    <span
                      className="ep-th-resize"
                      title="Seret untuk mengubah lebar"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        startResize(col.id, e.clientX, colWidth(col));
                      }}
                    />
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayRows.length === 0 ? (
              <tr>
                <td colSpan={Math.max(masterColSpan, 1)}>
                  <div className="ep-empty">
                    {emptyMessage ?? (
                      <>
                        Tidak ada data sesuai filter.
                        <br />
                        <small>Coba ubah filter kolom atau filter global.</small>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ) : (
              displayRows.map((row) => {
                const key = rowKey(row);
                const rowExpandable =
                  canExpand &&
                  (isRowExpandable ? isRowExpandable(row) : true);
                const isOpen = Boolean(expanded[key]);
                return (
                  <Fragment key={key}>
                    <tr
                      className={[
                        isOpen ? "ep-tbl-row-open" : "",
                        onRowActivate ? "ep-tbl-row-clickable" : "",
                      ]
                        .filter(Boolean)
                        .join(" ") || undefined}
                      onClick={
                        onRowActivate
                          ? (e) => handleRowActivate(row, e)
                          : undefined
                      }
                    >
                      {canExpand ? (
                        <td className="ep-td-expand sticky-expand">
                          {rowExpandable ? (
                            <button
                              type="button"
                              className={`ep-expand-btn${isOpen ? " is-open" : ""}`}
                              aria-expanded={isOpen}
                              aria-label={
                                isOpen
                                  ? "Tutup detail item"
                                  : "Buka detail item"
                              }
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleExpand(key);
                              }}
                            >
                              {isOpen ? "▾" : "▸"}
                            </button>
                          ) : (
                            <span className="ep-expand-spacer" aria-hidden />
                          )}
                        </td>
                      ) : null}
                      {visible.map((col) => (
                        <td
                          key={col.id}
                          className={stickyClass(col)}
                          style={{
                            width: colWidth(col),
                            minWidth: col.minWidth ?? 72,
                          }}
                        >
                          {col.render(row)}
                        </td>
                      ))}
                    </tr>
                    {rowExpandable && isOpen && renderExpandedRow ? (
                      <tr className="ep-tbl-expandrow">
                        <td
                          className="ep-td-expand-panel"
                          colSpan={Math.max(masterColSpan, 1)}
                        >
                          {renderExpandedRow(row)}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <Pagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
      {enableColumnManager ? (
        <ColumnVisibility
          columns={columns}
          hidden={hidden}
          onChange={setHidden}
          open={colsOpen}
          onClose={() => {
            setColsOpen(false);
            setColsAnchor(null);
          }}
          anchorEl={colsAnchor}
          defaultHidden={defaultHiddenColumns}
          onResetDefault={() => void resetPrefs()}
        />
      ) : null}
    </div>
  );
}
