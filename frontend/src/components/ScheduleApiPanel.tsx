import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createAllocations,
  fetchPoSchedule,
  fetchScheduleBoard,
  reviseAllocationQty,
  SchedulingApiError,
  type Allocation,
  type LineSchedule,
  type ScheduleBoardColumnFilters,
  type ScheduleBoardKpi,
} from "../api/scheduling";
import { newIdempotencyKey } from "../api/operational";
import { useMobileOpsActive } from "../mobile/MobileOpsContext";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef, ColumnHeaderFilterState } from "./DataTable/types";
import { KpiSummary, type KpiItem } from "./KpiSummary";
import { formatDateDisplay } from "../lib/formatDate";
import {
  allocationCanReviseQty,
  allocationReviseBounds,
} from "./teamTaskUi";

const SERVER_FILTER_COLS = new Set([
  "outletName",
  "paintColor",
  "poNo",
  "itemName",
  "lineId",
  "shipDates",
]);

function headerToColumnFilters(
  header: Record<string, ColumnHeaderFilterState>,
): ScheduleBoardColumnFilters {
  const out: ScheduleBoardColumnFilters = {};
  for (const [colId, st] of Object.entries(header)) {
    if (!SERVER_FILTER_COLS.has(colId)) continue;
    if (st.selected === null) continue;
    (out as Record<string, string[] | null>)[colId] = st.selected;
  }
  return out;
}

function headerSort(
  header: Record<string, ColumnHeaderFilterState>,
): { sortBy?: string; sortDir?: "asc" | "desc" } {
  for (const [colId, st] of Object.entries(header)) {
    if (st.sortDir) return { sortBy: colId, sortDir: st.sortDir };
  }
  return {};
}

function qty(n: number): string {
  return n.toLocaleString("id-ID");
}

function shipDatesCell(dates: string[] | undefined): string {
  if (!dates?.length) return "—";
  const shown = dates
    .map((d) => formatDateDisplay(d, ""))
    .filter(Boolean);
  return shown.length ? shown.join(", ") : "—";
}

function gateBadge(state: string, reason?: string | null): string {
  if (state === "open") return "Terbuka";
  if (state === "review") return "Perlu Review";
  if ((reason || "").trim() === "Perlu Sinkronisasi") return "Perlu Sinkronisasi";
  return "Tertutup";
}

type Filters = { q?: string };

/** Jadwal Produksi — DataTable presentasional + alokasi MongoDB V4. */
export function ScheduleApiPanel() {
  const pwaOps = useMobileOpsActive();
  const [lines, setLines] = useState<LineSchedule[]>([]);
  const [totalRows, setTotalRows] = useState(0);
  const [boardKpi, setBoardKpi] = useState<ScheduleBoardKpi | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<LineSchedule | null>(null);
  const [operator, setOperator] = useState("");
  const [allocQty, setAllocQty] = useState("");
  const [scheduleDate, setScheduleDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [filters, setFilters] = useState<Filters>({});
  const [headerFilters, setHeaderFilters] = useState<
    Record<string, ColumnHeaderFilterState>
  >({});
  const [filterFacets, setFilterFacets] = useState<Record<string, string[]>>(
    {},
  );
  const [tableEpoch, setTableEpoch] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [reviseTarget, setReviseTarget] = useState<Allocation | null>(null);
  const [reviseQty, setReviseQty] = useState("");
  const [reviseReason, setReviseReason] = useState("");
  const [reviseError, setReviseError] = useState<string | null>(null);
  const loadSeq = useRef(0);

  const columnFilters = useMemo(
    () => headerToColumnFilters(headerFilters),
    [headerFilters],
  );
  const { sortBy, sortDir } = useMemo(
    () => headerSort(headerFilters),
    [headerFilters],
  );

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const board = await fetchScheduleBoard({
        page,
        pageSize,
        q: filters.q,
        columnFilters,
        sortBy,
        sortDir,
      });
      if (seq !== loadSeq.current) return;
      setLines(board.items);
      setTotalRows(board.total_rows ?? board.total);
      setBoardKpi(board.kpi);
      setFilterFacets(board.filter_facets || {});
    } catch (err) {
      if (seq !== loadSeq.current) return;
      setLines([]);
      setTotalRows(0);
      setBoardKpi(null);
      setFilterFacets({});
      setError(err instanceof Error ? err.message : "Gagal memuat jadwal.");
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [page, pageSize, filters.q, columnFilters, sortBy, sortDir]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    function onPoAccepted() {
      void load();
    }
    window.addEventListener("eternal:po-accepted", onPoAccepted);
    return () => window.removeEventListener("eternal:po-accepted", onPoAccepted);
  }, [load]);

  function onServerHeaderChange(
    next: Record<string, ColumnHeaderFilterState>,
  ) {
    setHeaderFilters(next);
    setPage(1);
  }

  function resetAllFilters() {
    setFilters({});
    setHeaderFilters({});
    setTableEpoch((n) => n + 1);
    setPage(1);
  }

  const kpis = useMemo((): KpiItem[] => {
    // KPI global dari server — tidak dihitung dari halaman aktif.
    const totalQty = boardKpi?.total_qty_so ?? 0;
    const scheduled = boardKpi?.qty_allocated ?? 0;
    const printed = boardKpi?.qty_printed ?? 0;
    const unsched = boardKpi?.rows_unscheduled ?? 0;
    const unlocked = boardKpi?.rows_unlocked ?? 0;
    const printLines = boardKpi?.print_line_count ?? totalRows;
    return [
      {
        label: "Total Qty SO",
        value: `${qty(totalQty)} pcs`,
        foot: "Item Cetak · PO diterima",
      },
      {
        label: "Qty Terjadwal",
        value: qty(scheduled),
        foot: "pcs teralokasi",
      },
      {
        label: "Sudah Dicetak",
        value: qty(printed),
        foot: "pcs ledger cetak",
      },
      {
        label: "Belum Terjadwal",
        value: unsched,
        foot: "baris masih punya sisa",
        kind: "warn",
      },
      {
        label: "Siap Dijadwalkan",
        value: unlocked,
        foot: "gate design+payment terbuka",
      },
      {
        label: "Baris Item Cetak",
        value: printLines,
        foot: "PO diterima",
      },
    ];
  }, [boardKpi, totalRows]);

  async function refreshSelected(line: LineSchedule) {
    const res = await fetchPoSchedule(line.accurate_po_id);
    const fresh =
      res.lines.find((l) => l.accurate_line_id === line.accurate_line_id) || null;
    setSelected(fresh);
    setLines((prev) =>
      prev.map((l) =>
        l.accurate_line_id === line.accurate_line_id &&
        l.accurate_po_id === line.accurate_po_id &&
        fresh
          ? fresh
          : l,
      ),
    );
    // Muat ulang board agar KPI global sinkron setelah mutasi.
    void load();
  }

  async function onAllocate() {
    if (!selected) return;
    const value = Number(allocQty);
    if (!operator.trim() || !Number.isFinite(value) || value <= 0) {
      setError("Operator dan Qty alokasi wajib diisi.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await createAllocations(
        selected.accurate_po_id,
        selected.accurate_line_id,
        [{ operator_email: operator.trim(), qty: value }],
        newIdempotencyKey(`alloc:${selected.accurate_line_id}`),
        scheduleDate.trim() || undefined,
      );
      setNotice(
        res.duplicate
          ? "Perintah sama sudah tercatat — jadwal tidak digandakan."
          : `Alokasi ${res.allocations[0].allocation_id} tersimpan.`,
      );
      setOperator("");
      setAllocQty("");
      await refreshSelected(selected);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Alokasi gagal.");
    } finally {
      setBusy(false);
    }
  }

  function openRevise(alloc: Allocation) {
    setReviseTarget(alloc);
    setReviseQty(String(alloc.qty));
    setReviseReason("");
    setReviseError(null);
    setError(null);
  }

  function closeRevise() {
    if (busy) return;
    setReviseTarget(null);
    setReviseQty("");
    setReviseReason("");
    setReviseError(null);
  }

  async function submitRevise() {
    if (!reviseTarget || !selected) return;
    const bounds = allocationReviseBounds({
      qty: reviseTarget.qty,
      qty_printed: reviseTarget.qty_printed,
      qty_revise_min: reviseTarget.qty_revise_min,
      qty_revise_max: reviseTarget.qty_revise_max,
      lineQtyTarget: selected.qty_target,
      otherActiveQty: Math.max(
        selected.qty_allocated -
          (reviseTarget.status === "active" ? reviseTarget.qty : 0),
        0,
      ),
    });
    const value = Number(reviseQty);
    if (!Number.isFinite(value) || value < 0 || value !== Math.trunc(value)) {
      setReviseError("Qty baru harus bilangan bulat nonnegatif.");
      return;
    }
    if (value < bounds.min) {
      setReviseError(
        `Qty baru tidak boleh lebih kecil dari minimum revisi (${qty(bounds.min)}).`,
      );
      return;
    }
    if (value > bounds.max) {
      setReviseError(
        `Qty baru tidak boleh melebihi maksimum revisi (${qty(bounds.max)}).`,
      );
      return;
    }
    if (!reviseReason.trim()) {
      setReviseError("Alasan revisi wajib diisi.");
      return;
    }
    setBusy(true);
    setReviseError(null);
    setError(null);
    try {
      await reviseAllocationQty(
        reviseTarget.allocation_id,
        value,
        reviseReason.trim(),
      );
      setNotice(
        `Alokasi ${reviseTarget.allocation_id} direvisi menjadi ${qty(value)}.`,
      );
      setReviseTarget(null);
      setReviseQty("");
      setReviseReason("");
      await refreshSelected(selected);
      await load();
    } catch (err) {
      const msg =
        err instanceof SchedulingApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Revisi gagal.";
      setReviseError(msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  const columns: ColumnDef<LineSchedule>[] = useMemo(
    () => [
      {
        id: "poNo",
        label: "No. PO",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        minWidth: 90,
        getFilterValue: (row) => row.po_no,
        render: (row) => <span className="key">{row.po_no}</span>,
      },
      {
        id: "outletName",
        label: "Nama Outlet",
        // PWA: freeze dua kolom kiri (PO + Outlet). Desktop tetap satu sticky PO.
        sticky: pwaOps ? "customer" : undefined,
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (row) => row.outlet_name || "",
        render: (row) => row.outlet_name?.trim() || "—",
      },
      {
        id: "lineId",
        label: "Item Line",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (row) => row.accurate_line_id,
        render: (row) => row.accurate_line_id,
      },
      {
        id: "itemName",
        label: "Nama Barang",
        hideable: true,
        filterType: "text",
        defaultWidth: 180,
        getFilterValue: (row) => row.item_name,
        render: (row) => row.item_name,
      },
      {
        id: "paintColor",
        label: "Warna Cat",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) => row.paint_color || "",
        render: (row) => row.paint_color?.trim() || "—",
      },
      {
        id: "qtyTarget",
        label: "Qty Item",
        hideable: true,
        filterType: "number",
        defaultWidth: 100,
        getFilterValue: (row) => String(row.qty_target),
        getSortValue: (row) => row.qty_target,
        render: (row) => <b>{qty(row.qty_target)}</b>,
      },
      {
        id: "qtyAllocated",
        label: "Qty Teralokasi",
        hideable: true,
        filterType: "number",
        defaultWidth: 110,
        getFilterValue: (row) => String(row.qty_allocated),
        getSortValue: (row) => row.qty_allocated,
        render: (row) => qty(row.qty_allocated),
      },
      {
        id: "qtyUnallocated",
        label: "Belum Dialokasikan",
        hideable: true,
        filterType: "number",
        defaultWidth: 120,
        getFilterValue: (row) => String(row.qty_unallocated),
        getSortValue: (row) => row.qty_unallocated,
        render: (row) => qty(row.qty_unallocated),
      },
      {
        id: "qtyPrinted",
        label: "Qty Cetak",
        hideable: true,
        filterType: "number",
        defaultWidth: 100,
        getFilterValue: (row) => String(row.qty_printed),
        getSortValue: (row) => row.qty_printed,
        render: (row) => qty(row.qty_printed),
      },
      {
        id: "operators",
        label: "Operator",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (row) =>
          row.allocations
            .map(
              (a) =>
                a.operator_display_name ||
                a.operator_email ||
                "",
            )
            .join(" "),
        render: (row) =>
          row.allocations.length
            ? row.allocations
                .map((a) =>
                  (a.operator_display_name || "").trim() ||
                  "Nama belum tersedia",
                )
                .join(", ")
            : "—",
      },
      {
        id: "scheduleDates",
        label: "Tanggal Jadwal",
        hideable: true,
        filterType: "date",
        defaultWidth: 120,
        getFilterValue: (row) =>
          row.allocations.map((a) => a.schedule_date).join(" "),
        render: (row) => {
          const dates = Array.from(
            new Set(row.allocations.map((a) => a.schedule_date).filter(Boolean)),
          );
          return dates.length ? dates.join(", ") : "—";
        },
      },
      {
        id: "shipDates",
        label: "Tanggal Pengiriman",
        hideable: true,
        filterType: "date",
        defaultWidth: 150,
        // Facet server memakai tanggal unik per nilai; join hanya untuk tampilan.
        getFilterValue: (row) => (row.ship_dates || [])[0] || "",
        getSortValue: (row) => (row.ship_dates && row.ship_dates[0]) || "",
        render: (row) => {
          const text = shipDatesCell(row.ship_dates);
          return (
            <span title={text === "—" ? undefined : text}>{text}</span>
          );
        },
      },
      {
        id: "screenSize",
        label: "Ukuran Screen",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (row) => row.screen_size || "Belum diatur",
        render: (row) => {
          const value = row.screen_size || "Belum diatur";
          const unset = value === "Belum diatur";
          return (
            <span
              className={unset ? "ep-muted" : undefined}
              title="Dari Master Produk (bukan DEMO)"
            >
              {value}
            </span>
          );
        },
      },
      {
        id: "gateDesign",
        label: "Gate Design",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) =>
          gateBadge(row.gates.design.state, row.gates.design.reason),
        render: (row) =>
          gateBadge(row.gates.design.state, row.gates.design.reason),
      },
      {
        id: "gatePayment",
        label: "Gate Payment",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) => gateBadge(row.gates.payment.state),
        render: (row) => gateBadge(row.gates.payment.state),
      },
      {
        id: "action",
        label: "Aksi",
        hideable: false,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: () => "",
        render: (row) => (
          <button
            type="button"
            className="ep-btn sm"
            disabled={!row.scheduling_unlocked}
            title={
              row.scheduling_unlocked
                ? "Atur alokasi operator"
                : "Gate Design/Payment belum terpenuhi"
            }
            onClick={(e) => {
              e.stopPropagation();
              setSelected(row);
              setNotice(null);
              setError(null);
            }}
          >
            Penjadwalan
          </button>
        ),
      },
    ],
    [pwaOps],
  );

  return (
    <div data-testid="schedule-api-panel">
      {!loading && !pwaOps ? <KpiSummary items={kpis} /> : null}
      {error && !selected ? <p className="ep-error">{error}</p> : null}
      {notice && !selected ? <p className="ep-notice">{notice}</p> : null}
      <DataTable
        key={`schedule-board-${tableEpoch}`}
        tableId="po-accurate:schedule-v1"
        columns={columns}
        rows={loading ? [] : lines}
        filterSourceRows={lines}
        serverSide
        filterFacets={filterFacets}
        onServerHeaderChange={onServerHeaderChange}
        dynamicStickyOffsets={pwaOps}
        rowKey={(r) => `${r.accurate_po_id}:${r.accurate_line_id}`}
        enableColumnManager
        inlineColumnTools
        page={page}
        pageSize={pageSize}
        total={loading ? 0 : totalRows}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        emptyMessage={
          loading
            ? "Memuat jadwal…"
            : "Belum ada item Cetak Accurate."
        }
        filterSlot={(tools) => (
          <div className="ep-filterbar is-compact">
            <div className="ep-search">
              <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
                ⌕
              </span>
              <input
                placeholder="Cari PO, outlet, warna, barang…"
                value={filters.q || ""}
                onChange={(e) => {
                  setFilters({ q: e.target.value });
                  setPage(1);
                }}
                aria-label="Cari jadwal (teks bebas, server)"
                title="Pencarian teks bebas di server (q). Filter header kolom terpisah."
              />
            </div>
            <button
              type="button"
              className="ep-btn sm"
              onClick={resetAllFilters}
            >
              Reset filter
            </button>
            <button type="button" className="ep-btn sm" onClick={() => void load()}>
              Muat ulang
            </button>
            <button
              type="button"
              className="ep-btn sm"
              onClick={(e) => tools.openColumns(e.currentTarget)}
            >
              ▥ Kolom Tabel
            </button>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => void tools.resetPrefs()}
            >
              Reset tampilan
            </button>
            {tools.prefsHint ? (
              <span
                className={`ep-prefs-hint${tools.saveState === "error" ? " is-error" : ""}`}
                role={tools.saveState === "error" ? "alert" : undefined}
              >
                {tools.prefsHint}
              </span>
            ) : null}
          </div>
        )}
      />

      {selected ? (
        <div
          className="ep-modalshade ep-modalshade-center"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelected(null);
          }}
        >
          <div className="ep-modal" role="dialog" aria-modal="true">
            <header className="ep-modalhead">
              <strong>
                Penjadwalan · {selected.po_no} · line {selected.accurate_line_id}
              </strong>
              <button type="button" className="ep-btn" onClick={() => setSelected(null)}>
                Tutup
              </button>
            </header>
            <div className="ep-modalbody">
              <dl className="ep-kv">
                <dt>Qty Item</dt>
                <dd>{qty(selected.qty_target)}</dd>
                <dt>Terjadwal (aktif)</dt>
                <dd>{qty(selected.qty_allocated)}</dd>
                <dt>Sisa menunggu jadwal</dt>
                <dd>{qty(selected.qty_unallocated)}</dd>
                <dt>Ukuran Screen</dt>
                <dd>{selected.screen_size || "Belum diatur"}</dd>
                <dt>Qty Cetak Terkonfirmasi</dt>
                <dd>{qty(selected.qty_printed)}</dd>
              </dl>

              <p className="ep-sectionlabel">Alokasi Operator</p>
              {selected.allocations.length === 0 ? (
                <p className="ep-empty">Belum ada alokasi.</p>
              ) : (
                <div className="ep-tblscroll">
                  <table className="ep-table">
                    <thead>
                      <tr>
                        <th>Allocation ID</th>
                        <th>Operator</th>
                        <th>Qty</th>
                        <th>Tanggal</th>
                        <th>Qty Cetak</th>
                        <th>Status</th>
                        <th>Aksi</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.allocations.map((a) => (
                        <tr key={a.allocation_id}>
                          <td>{a.allocation_id}</td>
                          <td>
                            {(a.operator_display_name || "").trim() ||
                              "Nama belum tersedia"}
                          </td>
                          <td>{qty(a.qty)}</td>
                          <td>{a.schedule_date || "—"}</td>
                          <td>{qty(a.qty_printed || 0)}</td>
                          <td>{a.status}</td>
                          <td>
                            <button
                              type="button"
                              className="ep-btn"
                              disabled={busy || !allocationCanReviseQty(a)}
                              title={
                                allocationCanReviseQty(a)
                                  ? "Ubah Qty alokasi"
                                  : "Alokasi tidak dapat direvisi"
                              }
                              onClick={() => openRevise(a)}
                            >
                              Ubah Qty
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <p className="ep-sectionlabel">Tambah Alokasi</p>
              <div className="ep-filterbar">
                <input
                  aria-label="Operator"
                  placeholder="email operator"
                  value={operator}
                  onChange={(e) => setOperator(e.target.value)}
                />
                <input
                  aria-label="Qty alokasi"
                  type="number"
                  min={1}
                  value={allocQty}
                  onChange={(e) => setAllocQty(e.target.value)}
                />
                <input
                  aria-label="Tanggal jadwal"
                  type="date"
                  value={scheduleDate}
                  onChange={(e) => setScheduleDate(e.target.value)}
                />
                <button
                  type="button"
                  className="ep-btn primary"
                  disabled={busy}
                  onClick={() => void onAllocate()}
                >
                  {busy ? "Menyimpan…" : "Konfirmasi Jadwal"}
                </button>
              </div>
              {error ? <p className="ep-error">{error}</p> : null}
              {notice ? <p className="ep-notice">{notice}</p> : null}
            </div>
          </div>
        </div>
      ) : null}

      {reviseTarget && selected ? (
        <div
          className="ep-modalshade ep-modalshade-center ep-sched-revise-shade"
          data-testid="schedule-revise-modal"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeRevise();
          }}
        >
          <div className="ep-modal ep-sched-revise-modal" role="dialog" aria-modal="true">
            <header className="ep-modal-head">
              <div>
                <h2>Ubah Qty Alokasi</h2>
                <div className="ep-modal-submeta">
                  {reviseTarget.allocation_id}
                </div>
              </div>
              <button
                type="button"
                className="ep-btn sm"
                disabled={busy}
                onClick={closeRevise}
              >
                Tutup
              </button>
            </header>
            <div className="ep-modal-body">
              {(() => {
                const bounds = allocationReviseBounds({
                  qty: reviseTarget.qty,
                  qty_printed: reviseTarget.qty_printed,
                  qty_revise_min: reviseTarget.qty_revise_min,
                  qty_revise_max: reviseTarget.qty_revise_max,
                  lineQtyTarget: selected.qty_target,
                  otherActiveQty: Math.max(
                    selected.qty_allocated -
                      (reviseTarget.status === "active" ? reviseTarget.qty : 0),
                    0,
                  ),
                });
                return (
                  <>
                    <dl className="ep-kv ep-kv-sched ep-sched-revise-kv">
                      <dt>Operator</dt>
                      <dd>
                        {(reviseTarget.operator_display_name || "").trim() ||
                          "Nama belum tersedia"}
                      </dd>
                      <dt>Nama barang</dt>
                      <dd>{selected.item_name}</dd>
                      <dt>Qty alokasi saat ini</dt>
                      <dd className="ep-num">{qty(reviseTarget.qty)}</dd>
                      <dt>Qty cetak terkonfirmasi</dt>
                      <dd className="ep-num">
                        {qty(reviseTarget.qty_printed || 0)}
                      </dd>
                      <dt>Qty minimum revisi</dt>
                      <dd className="ep-num">{qty(bounds.min)}</dd>
                      <dt>Qty maksimum revisi</dt>
                      <dd className="ep-num">{qty(bounds.max)}</dd>
                    </dl>
                    <div className="ep-sched-revise-fields">
                      <label className="ep-sched-field">
                        <span>Qty baru</span>
                        <input
                          type="number"
                          inputMode="numeric"
                          min={bounds.min}
                          max={bounds.max}
                          step={1}
                          aria-label="Qty baru"
                          className="ep-num"
                          value={reviseQty}
                          disabled={busy}
                          onChange={(e) => setReviseQty(e.target.value)}
                        />
                      </label>
                      <label className="ep-sched-field">
                        <span>Alasan revisi</span>
                        <input
                          type="text"
                          aria-label="Alasan revisi"
                          value={reviseReason}
                          disabled={busy}
                          onChange={(e) => setReviseReason(e.target.value)}
                        />
                      </label>
                    </div>
                    {reviseError ? (
                      <p className="ep-error" role="alert">
                        {reviseError}
                      </p>
                    ) : null}
                  </>
                );
              })()}
            </div>
            <footer className="ep-modal-foot">
              <div className="ep-modal-foot-actions">
                <button
                  type="button"
                  className="ep-btn"
                  disabled={busy}
                  onClick={closeRevise}
                >
                  Batal
                </button>
                <button
                  type="button"
                  className="ep-btn primary"
                  disabled={busy}
                  onClick={() => void submitRevise()}
                >
                  {busy ? "Menyimpan…" : "Simpan Revisi"}
                </button>
              </div>
            </footer>
          </div>
        </div>
      ) : null}
    </div>
  );
}
