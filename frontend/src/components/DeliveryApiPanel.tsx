import { useCallback, useEffect, useMemo, useState } from "react";
import {
  cancelLoading,
  confirmLoading,
  fetchDeliveryDetail,
  fetchDeliverySchedule,
  fetchLoadingValidation,
  raiseDiscrepancy,
  type DeliveryDetail,
  type DeliveryScheduleRow,
  type LoadingValidation,
} from "../api/delivery";
import { newIdempotencyKey } from "../api/operational";
import type { ShipDateFilterValue } from "../demo/deliverySjDemo";
import {
  deliveryStatusLabel,
  display,
  displayBoolean,
  filterDeliveryRows,
  formatQtyByUnit,
  loadingStatusLabel,
  rowTotalQtyDisplay,
  scheduleMetrics,
} from "../lib/deliverySj";
import {
  formatDateDisplay,
  formatDateTimeDisplay,
  toIsoDateString,
} from "../lib/formatDate";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { DeliveryPeriodModal } from "./DeliveryPeriodModal";
import { DeliverySjDetailModal } from "./DeliverySjDetailModal";
import { KpiSummary, type KpiItem } from "./KpiSummary";
import { ShipDateFilter } from "./ShipDateFilter";
import { SjApiLineItems } from "./SjApiLineItems";

/** Kolom utama tetap ringkas; sisanya lewat Pengaturan Kolom. */
const DEFAULT_HIDDEN = [
  "customerNo",
  "customerPoNo",
  "destination",
  "contact",
  "branch",
  "createdBy",
  "description",
  "shipment",
  "packingNumber",
  "printedBy",
  "printedTime",
  "accurateStatus",
  "attachment",
  "sjDate",
  "driver",
];

export function DeliveryApiPanel() {
  const [rows, setRows] = useState<DeliveryScheduleRow[]>([]);
  const [dateFilter, setDateFilter] = useState<ShipDateFilterValue>({ kind: "all" });
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [periodOpen, setPeriodOpen] = useState(false);
  const [selected, setSelected] = useState<DeliveryScheduleRow | null>(null);
  const [detail, setDetail] = useState<DeliveryDetail | null>(null);
  const [validation, setValidation] = useState<LoadingValidation | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const schedule = await fetchDeliverySchedule({});
      setRows(schedule.items);
      return schedule.items;
    } catch (err) {
      setRows([]);
      setError(err instanceof Error ? err.message : "Gagal memuat jadwal pengiriman.");
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(
    () => filterDeliveryRows(rows, dateFilter, q),
    [rows, dateFilter, q],
  );

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageSize]);

  const metrics = useMemo(() => scheduleMetrics(filtered), [filtered]);

  const kpis = useMemo(
    (): KpiItem[] => [
      {
        label: "Jumlah SJ",
        value: metrics.sjCount,
        foot: "Surat Jalan Accurate",
      },
      {
        label: "Total Qty",
        value: formatQtyByUnit(metrics.qtyByUnit),
        foot: "per satuan, tanpa konversi",
      },
      {
        label: "Tujuan Unik",
        value: metrics.destinationCount,
        foot: "alamat berbeda",
      },
      {
        label: "Sudah Loading",
        value: metrics.loadedCount,
        foot:
          metrics.needsReloadingCount > 0
            ? `${metrics.needsReloadingCount} perlu Loading ulang`
            : "SJ siap dikirim",
      },
    ],
    [metrics],
  );

  const openDetail = useCallback(
    async (row: DeliveryScheduleRow, keepNotice = false) => {
      setSelected(row);
      setValidation(null);
      setDetail(null);
      setError(null);
      if (!keepNotice) setNotice(null);
      try {
        const [note, check] = await Promise.all([
          fetchDeliveryDetail(row.accurate_sj_id),
          fetchLoadingValidation(row.accurate_sj_id),
        ]);
        setDetail(note);
        setValidation(check);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Gagal memuat detail Surat Jalan.");
      }
    },
    [],
  );

  const reopenAfterAction = useCallback(
    async (sjId: string, message: string) => {
      const next = await load();
      const fresh = next.find((r) => r.accurate_sj_id === sjId);
      if (fresh) await openDetail(fresh, true);
      setNotice(message);
    },
    [load, openDetail],
  );

  async function onConfirmLoading() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const res = await confirmLoading(
        selected.accurate_sj_id,
        newIdempotencyKey(`loading:${selected.accurate_sj_id}`),
      );
      await reopenAfterAction(
        selected.accurate_sj_id,
        res.duplicate
          ? "Loading SJ ini sudah dikonfirmasi sebelumnya."
          : "Loading dikonfirmasi; tugas Driver dibuat.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Konfirmasi Loading gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onCancelLoading() {
    if (!selected) return;
    if (!cancelReason.trim()) {
      setError("Alasan pembatalan Loading wajib diisi.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await cancelLoading(selected.accurate_sj_id, cancelReason.trim());
      setCancelReason("");
      await reopenAfterAction(
        selected.accurate_sj_id,
        res.cancelled
          ? "Loading dibatalkan; Qty QC dikembalikan dan menunggu Loading ulang."
          : "Tidak ada Loading aktif untuk dibatalkan.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pembatalan Loading gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onDiscrepancy() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await raiseDiscrepancy(selected.accurate_sj_id);
      setNotice("Tugas Kepala Produksi “Qty Loading dan SJ Berbeda” dibuat.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal membuat tugas koordinasi.");
    } finally {
      setBusy(false);
    }
  }

  const columns: ColumnDef<DeliveryScheduleRow>[] = useMemo(
    () => [
      {
        id: "sjNo",
        label: "No. SJ",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 150,
        getFilterValue: (r) => r.sj_no,
        render: (r) => (
          <button
            type="button"
            className="key"
            onClick={(e) => {
              e.stopPropagation();
              void openDetail(r);
            }}
          >
            {r.sj_no}
          </button>
        ),
      },
      {
        id: "shipDate",
        label: "Tanggal Kirim",
        hideable: true,
        filterType: "date",
        defaultWidth: 116,
        getFilterValue: (r) => toIsoDateString(r.ship_date),
        getSortValue: (r) => toIsoDateString(r.ship_date),
        render: (r) => formatDateDisplay(r.ship_date),
      },
      {
        id: "sjDate",
        label: "Tanggal SJ",
        hideable: true,
        filterType: "date",
        defaultWidth: 116,
        getFilterValue: (r) => toIsoDateString(r.sj_date),
        getSortValue: (r) => toIsoDateString(r.sj_date),
        render: (r) => formatDateDisplay(r.sj_date),
      },
      {
        id: "customer",
        label: "Outlet / Pelanggan",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 180,
        getFilterValue: (r) => r.customer_name || "",
        render: (r) => display(r.customer_name),
      },
      {
        id: "customerNo",
        label: "ID Pelanggan",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (r) => r.customer_no || "",
        render: (r) => display(r.customer_no),
      },
      {
        id: "poNo",
        label: "No. PO",
        hideable: true,
        filterType: "text",
        defaultWidth: 150,
        getFilterValue: (r) => r.po_no,
        render: (r) => display(r.po_no),
      },
      {
        id: "customerPoNo",
        label: "No. PO Pelanggan",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (r) => r.customer_po_no || "",
        render: (r) => display(r.customer_po_no),
      },
      {
        id: "salesman",
        label: "Nama Penjual",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.salesman_name || "",
        render: (r) => display(r.salesman_name),
      },
      {
        id: "shipZone",
        label: "Zona Kirim",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (r) => r.ship_zone || "",
        render: (r) => display(r.ship_zone),
      },
      {
        id: "destination",
        label: "Alamat",
        hideable: true,
        filterType: "text",
        defaultWidth: 200,
        getFilterValue: (r) => r.destination || "",
        render: (r) => display(r.destination),
      },
      {
        id: "contact",
        label: "Kontak",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.contact_info || "",
        render: (r) => display(r.contact_info),
      },
      {
        id: "shipment",
        label: "Ekspedisi",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.shipment || "",
        render: (r) => display(r.shipment),
      },
      {
        id: "branch",
        label: "Cabang",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => r.branch_name || "",
        render: (r) => display(r.branch_name),
      },
      {
        id: "createdBy",
        label: "Pembuat Data",
        hideable: true,
        filterType: "text",
        defaultWidth: 130,
        getFilterValue: (r) => r.created_by_user_name || "",
        render: (r) => display(r.created_by_user_name),
      },
      {
        id: "printedBy",
        label: "Dicetak/Email",
        hideable: true,
        filterType: "text",
        defaultWidth: 130,
        getFilterValue: (r) => r.printed_by_user || "",
        render: (r) => display(r.printed_by_user),
      },
      {
        id: "printedTime",
        label: "Waktu Cetak/Email",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (r) => r.printed_time || "",
        render: (r) => formatDateTimeDisplay(r.printed_time),
      },
      {
        id: "packingNumber",
        label: "No. Pengepakan",
        hideable: true,
        filterType: "text",
        defaultWidth: 130,
        getFilterValue: (r) => r.packing_number || "",
        render: (r) => display(r.packing_number),
      },
      {
        id: "description",
        label: "Keterangan",
        hideable: true,
        filterType: "text",
        defaultWidth: 170,
        getFilterValue: (r) => r.description || "",
        render: (r) => display(r.description),
      },
      {
        id: "attachment",
        label: "Memiliki Dokumen",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (r) => displayBoolean(r.attachment_exist),
        render: (r) => displayBoolean(r.attachment_exist),
      },
      {
        id: "accurateStatus",
        label: "Status Accurate",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (r) => r.accurate_status_name || "",
        render: (r) => display(r.accurate_status_name),
      },
      {
        id: "itemCount",
        label: "Jumlah Barang",
        hideable: true,
        filterType: "number",
        defaultWidth: 110,
        getFilterValue: (r) => String(r.item_count ?? r.items.length),
        getSortValue: (r) => r.item_count ?? r.items.length,
        render: (r) => r.item_count ?? r.items.length,
      },
      {
        id: "totalQty",
        label: "Total Qty",
        hideable: true,
        filterType: "text",
        defaultWidth: 150,
        getFilterValue: (r) => rowTotalQtyDisplay(r),
        render: (r) => <b>{rowTotalQtyDisplay(r)}</b>,
      },
      {
        id: "loadingStatus",
        label: "Status Loading",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (r) => loadingStatusLabel(r),
        render: (r) => (
          <span className={r.needs_reloading ? "ep-row-danger" : undefined}>
            {loadingStatusLabel(r)}
          </span>
        ),
      },
      {
        id: "deliveryStatus",
        label: "Status Pengiriman",
        hideable: true,
        filterType: "text",
        defaultWidth: 150,
        getFilterValue: (r) => deliveryStatusLabel(r.delivery_status),
        render: (r) => deliveryStatusLabel(r.delivery_status),
      },
      {
        id: "driver",
        label: "Driver",
        hideable: true,
        filterType: "text",
        defaultWidth: 150,
        getFilterValue: (r) => r.driver_email || "",
        render: (r) => display(r.driver_email),
      },
    ],
    [openDetail],
  );

  function resetFilters() {
    setDateFilter({ kind: "all" });
    setQ("");
    setPage(1);
  }

  return (
    <div data-testid="delivery-api-panel">
      <KpiSummary items={kpis} />

      {error && !selected ? <p className="ep-error">{error}</p> : null}
      {notice && !selected ? <p className="ep-notice">{notice}</p> : null}

      <DataTable
        tableId="delivery:sj-accurate-v1"
        columns={columns}
        rows={loading ? [] : pageRows}
        filterSourceRows={filtered}
        rowKey={(r) => r.accurate_sj_id}
        enableColumnManager
        inlineColumnTools
        defaultHiddenColumns={DEFAULT_HIDDEN}
        page={page}
        pageSize={pageSize}
        total={filtered.length}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        onRowActivate={(row) => void openDetail(row)}
        isRowExpandable={(row) => (row.items?.length ?? 0) > 0}
        renderExpandedRow={(row) => <SjApiLineItems items={row.items} />}
        emptyMessage={
          loading
            ? "Memuat Surat Jalan dari MongoDB V4…"
            : rows.length === 0
              ? "Belum ada Surat Jalan Accurate di MongoDB V4."
              : "Tidak ada Surat Jalan pada filter aktif."
        }
        filterSlot={(tools) => (
          <div className="ep-filterbar is-compact">
            <div className="ep-search">
              <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
                ⌕
              </span>
              <input
                placeholder="Cari No. SJ, PO, outlet, penjual, barang…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
                aria-label="Cari Surat Jalan"
              />
            </div>
            <ShipDateFilter
              value={dateFilter}
              onChange={(next) => {
                setDateFilter(next);
                setPage(1);
              }}
            />
            <button type="button" className="ep-btn sm" onClick={resetFilters}>
              Reset filter
            </button>
            <button
              type="button"
              className="ep-btn sm primary"
              onClick={() => setPeriodOpen(true)}
            >
              Rincian Pengiriman
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
            <button type="button" className="ep-btn sm" onClick={() => void load()}>
              Muat ulang
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

      <DeliveryPeriodModal
        open={periodOpen}
        rows={filtered}
        dateFilter={dateFilter}
        onClose={() => setPeriodOpen(false)}
      />

      {selected ? (
        <DeliverySjDetailModal
          row={selected}
          detail={detail}
          validation={validation}
          busy={busy}
          error={error}
          notice={notice}
          cancelReason={cancelReason}
          onCancelReasonChange={setCancelReason}
          onConfirmLoading={() => void onConfirmLoading()}
          onCancelLoading={() => void onCancelLoading()}
          onDiscrepancy={() => void onDiscrepancy()}
          onClose={() => {
            setSelected(null);
            setDetail(null);
            setValidation(null);
          }}
        />
      ) : null}
    </div>
  );
}
