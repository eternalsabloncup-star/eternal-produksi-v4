import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchProductionFlow,
  fetchProductionFlowByStage,
  type ProductionFlow,
  type ProductionFlowStageRow,
} from "../api/analytics";
import { mapHeaderToPoRow } from "../demo/ApiPoSource";
import type { PoFilters, PoRow } from "../demo/types";
import { formatDateDisplay } from "../lib/formatDate";
import { ColumnFilter } from "./DataTable/ColumnFilter";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { DesignWorkflowPanel } from "./DesignWorkflowPanel";
import { PaymentWorkflowPanel } from "./PaymentWorkflowPanel";
import type { ProductionFlowStep } from "./ProductionFlowTrack";

const DESIGN_NODE_IDS = new Set(["flow-design", "flow-approval-design"]);
const PAYMENT_NODE_IDS = new Set(["flow-payment", "flow-approval-payment"]);

const DEFAULT_HIDDEN = [
  "recipient",
  "address",
  "userName",
  "bufferLabel",
  "notes",
  "paymentTerms",
  "expedition",
];

/** Label kolom monitoring — prefer backend; jangan tampilkan raw SoT "Selesai" di Order Masuk. */
function monitoringDisplay(row: ProductionFlowStageRow): string {
  const fromApi = (row.monitoring_label || "").trim();
  if (fromApi) return fromApi;
  if (row.node_id === "flow-selesai") return "Selesai";
  if (row.stage_status === "berjalan") return "Dalam Proses";
  return "Aktif";
}

type StageTableRow = {
  key: string;
  po: PoRow;
  stage: ProductionFlowStageRow;
};

type Props = {
  selectedStageId: string;
  selectedStageLabel: string;
  onStageFocus?: (step: ProductionFlowStep) => void;
};

/** Alur Produksi — klik tahap → tabel PO aktif (accepted only). */
export function ProductionFlowApiPanel({
  selectedStageId,
  selectedStageLabel,
  onStageFocus,
}: Props) {
  const [flows, setFlows] = useState<ProductionFlow[]>([]);
  const [poFilterId, setPoFilterId] = useState<string>("");
  const [filters, setFilters] = useState<PoFilters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [rows, setRows] = useState<StageTableRow[]>([]);
  const [total, setTotal] = useState(0);
  const [scanCapped, setScanCapped] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<StageTableRow | null>(null);

  const loadFlows = useCallback(async () => {
    try {
      const res = await fetchProductionFlow();
      setFlows(res.items);
    } catch {
      setFlows([]);
    }
  }, []);

  const loadStage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchProductionFlowByStage({
        nodeId: selectedStageId,
        page,
        pageSize,
        q: filters.q,
        urgent: filters.urgent,
        accuratePoId: poFilterId || undefined,
      });
      const mapped: StageTableRow[] = [];
      for (const item of res.items) {
        const po = mapHeaderToPoRow(item.po);
        if (!po) continue;
        mapped.push({
          key: item.accurate_po_id,
          po,
          stage: item,
        });
      }
      setRows(mapped);
      setTotal(res.total);
      setScanCapped(Boolean(res.scan_capped));
      if (res.page !== page) setPage(res.page);
    } catch (err) {
      setRows([]);
      setTotal(0);
      setError(
        err instanceof Error ? err.message : "Gagal memuat PO tahap produksi.",
      );
    } finally {
      setLoading(false);
    }
  }, [selectedStageId, page, pageSize, filters.q, filters.urgent, poFilterId]);

  useEffect(() => {
    void loadFlows();
  }, [loadFlows]);

  useEffect(() => {
    setPage(1);
  }, [selectedStageId, poFilterId, filters.q, filters.urgent]);

  useEffect(() => {
    void loadStage();
  }, [loadStage]);

  const columns: ColumnDef<StageTableRow>[] = useMemo(
    () => [
      {
        id: "id",
        label: "No. PO",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 108,
        minWidth: 88,
        getFilterValue: (row) => row.po.id,
        render: (row) => (
          <button
            type="button"
            className="key"
            onClick={(e) => {
              e.stopPropagation();
              setDetail(row);
            }}
          >
            {row.po.id}
            {row.po.urgent ? (
              <span style={{ color: "#d45a59", fontSize: 9 }}> ●</span>
            ) : null}
          </button>
        ),
      },
      {
        id: "poDate",
        label: "Tanggal PO",
        hideable: true,
        filterType: "date",
        defaultWidth: 104,
        getFilterValue: (row) => row.po.poDate,
        getSortValue: (row) => row.po.poDate,
        render: (row) => formatDateDisplay(row.po.poDate),
      },
      {
        id: "customer",
        label: "Pelanggan / Kepada",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        minWidth: 110,
        getFilterValue: (row) => row.po.customer,
        render: (row) => row.po.customer || "—",
      },
      {
        id: "orderStatus",
        label: "Status Pesanan",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) => (row.po.urgent ? "Urgent" : "Normal"),
        render: (row) => (
          <span
            className={`ep-pill ep-pill-status ${row.po.urgent ? "order-urgent" : "order-normal"}`}
          >
            {row.po.urgent ? "Urgent" : "Normal"}
          </span>
        ),
      },
      {
        id: "paymentTerms",
        label: "Syarat Pembayaran",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (row) => row.po.paymentTerms,
        render: (row) => row.po.paymentTerms || "—",
      },
      {
        id: "etaShip",
        label: "Estimasi Tanggal Pengiriman",
        hideable: true,
        filterType: "date",
        defaultWidth: 150,
        getFilterValue: (row) => row.po.etaShip,
        render: (row) => formatDateDisplay(row.po.etaShip),
      },
      {
        id: "shipZone",
        label: "Zona Pengiriman",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (row) => row.po.shipZone,
        render: (row) => row.po.shipZone || "—",
      },
      {
        id: "expedition",
        label: "Ekspedisi",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (row) => row.po.expedition,
        render: (row) => row.po.expedition || "—",
      },
      {
        id: "seller",
        label: "Penjual",
        hideable: true,
        filterType: "text",
        defaultWidth: 96,
        getFilterValue: (row) => row.po.seller,
        render: (row) => row.po.seller || "—",
      },
      {
        id: "itemCount",
        label: "Jumlah Item",
        hideable: true,
        filterType: "number",
        defaultWidth: 96,
        getFilterValue: (row) => String(row.po.itemCount),
        getSortValue: (row) => row.po.itemCount,
        render: (row) => row.po.itemCount,
      },
      {
        id: "totalQtyDisplay",
        label: "Total Kuantitas",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (row) => row.po.totalQtyDisplay,
        render: (row) => <b>{row.po.totalQtyDisplay || "—"}</b>,
      },
      {
        id: "bufferLabel",
        label: "Buffer / Non-Buffer",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (row) => row.po.bufferLabel,
        render: (row) => row.po.bufferLabel || "—",
      },
      {
        id: "recipient",
        label: "Nama Penerima",
        hideable: true,
        filterType: "text",
        defaultWidth: 140,
        getFilterValue: (row) => row.po.recipient,
        render: (row) => row.po.recipient || "—",
      },
      {
        id: "address",
        label: "Alamat",
        hideable: true,
        filterType: "text",
        defaultWidth: 200,
        getFilterValue: (row) => row.po.address,
        render: (row) => row.po.address || "—",
      },
      {
        id: "userName",
        label: "User",
        hideable: true,
        filterType: "text",
        defaultWidth: 100,
        getFilterValue: (row) => row.po.userName,
        render: (row) => row.po.userName || "—",
      },
      {
        id: "notes",
        label: "Keterangan PO",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (row) => row.po.notes || "",
        render: (row) => row.po.notes || "—",
      },
      {
        id: "stageStatus",
        label: "Status tahap",
        hideable: true,
        filterType: "text",
        defaultWidth: 130,
        getFilterValue: (row) => monitoringDisplay(row.stage),
        render: (row) => monitoringDisplay(row.stage),
      },
      {
        id: "stageDetail",
        label: "Keterangan",
        hideable: true,
        filterType: "text",
        defaultWidth: 200,
        getFilterValue: (row) => row.stage.stage_detail,
        render: (row) => row.stage.stage_detail || "—",
      },
      {
        id: "action",
        label: "Aksi",
        hideable: false,
        filterType: "text",
        defaultWidth: 90,
        getFilterValue: () => "",
        render: (row) => (
          <button
            type="button"
            className="ep-btn sm"
            onClick={(e) => {
              e.stopPropagation();
              setDetail(row);
            }}
          >
            Rincian
          </button>
        ),
      },
    ],
    [],
  );

  function resetFilters() {
    setFilters({});
    setPoFilterId("");
    setPage(1);
  }

  const detailNodeId = detail?.stage.node_id || selectedStageId;

  return (
    <div data-testid="production-flow-api-panel">
      <div className="ep-flow-stage-title">
        {selectedStageLabel}
        <span className="ep-flow-meta-sep">·</span>
        <span className="ep-muted">{total} PO aktif</span>
        {scanCapped ? (
          <>
            <span className="ep-flow-meta-sep">·</span>
            <span className="ep-notice">Scan mencapai batas baca</span>
          </>
        ) : null}
      </div>

      <div className="ep-filterbar is-compact">
        <label htmlFor="flow-po-filter">PO (opsional)</label>
        <select
          id="flow-po-filter"
          value={poFilterId}
          onChange={(e) => {
            setPoFilterId(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Semua PO diterima</option>
          {flows.map((f) => (
            <option key={f.accurate_po_id} value={f.accurate_po_id}>
              {f.po_no}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="ep-btn sm"
          onClick={() => {
            void loadFlows();
            void loadStage();
          }}
        >
          Muat ulang
        </button>
      </div>

      {error ? <p className="ep-error">{error}</p> : null}

      <DataTable
        key={selectedStageId}
        tableId={`po-accurate:flow-stage:${selectedStageId}`}
        columns={columns}
        rows={loading ? [] : rows}
        rowKey={(r) => r.key}
        enableColumnManager
        inlineColumnTools
        defaultHiddenColumns={DEFAULT_HIDDEN}
        page={page}
        pageSize={pageSize}
        total={loading ? 0 : total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        onRowActivate={(row) => setDetail(row)}
        emptyMessage={
          loading
            ? "Memuat PO tahap dari MongoDB V4…"
            : `Belum ada PO diterima yang aktif pada “${selectedStageLabel}”.`
        }
        filterSlot={(tools) => (
          <ColumnFilter
            value={filters}
            onChange={(next) => {
              setFilters({
                q: next.q?.trim() ? next.q : undefined,
                urgent: next.urgent || undefined,
              });
              setPage(1);
            }}
            onReset={resetFilters}
            showUrgent
            compact
            sourceMode="api"
            showDemoHint={false}
            trailing={
              <>
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
              </>
            }
          />
        )}
      />

      {detail ? (
        <div
          className="ep-modalshade ep-modalshade-center"
          onClick={(e) => {
            if (e.target === e.currentTarget) setDetail(null);
          }}
        >
          <div className="ep-modal" role="dialog" aria-modal="true">
            <header className="ep-modalhead">
              <strong>
                {detail.stage.stage_label} · {detail.po.poNo}
              </strong>
              <button
                type="button"
                className="ep-btn"
                onClick={() => setDetail(null)}
              >
                Tutup
              </button>
            </header>
            <div className="ep-modalbody">
              <dl className="ep-kv">
                <dt>Status tahap</dt>
                <dd>{monitoringDisplay(detail.stage)}</dd>
                <dt>Keterangan</dt>
                <dd>{detail.stage.stage_detail || "—"}</dd>
                <dt>Penerimaan</dt>
                <dd>
                  {detail.stage.acceptance?.inbox_status ||
                    detail.stage.acceptance?.status ||
                    "accepted"}
                </dd>
                <dt>Sumber</dt>
                <dd>State operasional MongoDB V4 (accepted only)</dd>
                {!detail.stage.has_print_items ? (
                  <>
                    <dt>Jenis</dt>
                    <dd>
                      <span className="ep-pill gray">
                        PO seluruh item Non-Cetak
                      </span>
                    </dd>
                  </>
                ) : null}
              </dl>
              {DESIGN_NODE_IDS.has(detailNodeId) ? (
                <DesignWorkflowPanel
                  accuratePoId={
                    detail.stage.accurate_po_id || detail.po.accurateId || ""
                  }
                  poNo={detail.po.poNo}
                  onChanged={() => {
                    void loadFlows();
                    void loadStage();
                  }}
                />
              ) : null}
              {PAYMENT_NODE_IDS.has(detailNodeId) ? (
                <PaymentWorkflowPanel
                  accuratePoId={
                    detail.stage.accurate_po_id || detail.po.accurateId || ""
                  }
                  poNo={detail.po.poNo}
                  onChanged={() => {
                    void loadFlows();
                    void loadStage();
                  }}
                />
              ) : null}
              {onStageFocus ? (
                <p className="ep-muted" style={{ marginTop: 12 }}>
                  Tahap aktif lain:{" "}
                  {(detail.stage.active_stages || []).join(", ") || "—"}
                </p>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
