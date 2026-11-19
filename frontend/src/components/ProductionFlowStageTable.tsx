import { useEffect, useMemo, useState } from "react";
import {
  getStageTableConfig,
  loadStageTableRows,
  type StageColumnSpec,
  type StageTableRow,
} from "../app/productionFlowStageTables";
import type { PoFilters } from "../demo/types";
import { formatDateDisplay } from "../lib/formatDate";
import { ApprovalLinkDemoModal } from "./ApprovalLinkDemoModal";
import { ColumnFilter } from "./DataTable/ColumnFilter";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { PdfViewerModal } from "./PdfViewerModal";
import { StatusBadge } from "./StatusBadge";

type Props = {
  stageId: string;
  stageLabel: string;
};

const ORDER_STATUS_IDS = new Set(["orderStatus"]);
const BADGE_STATUS_IDS = new Set([
  "designStatus",
  "approvalStatus",
  "paymentStatus",
  "verifyStatus",
  "screenStatus",
  "prepStatus",
  "confirmStatus",
  "loadingStatus",
  "shipStatus",
]);

type PdfTarget = {
  src: string | null;
  title: string;
  subtitle: string;
};

function cellValue(row: StageTableRow, colId: string): string {
  return row.values[colId] ?? "";
}

/**
 * Satu shared DataTable; kolom mengikuti workspace tahap terpilih.
 */
export function ProductionFlowStageTable({ stageId, stageLabel }: Props) {
  const config = getStageTableConfig(stageId);
  const [filters, setFilters] = useState<PoFilters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [pdf, setPdf] = useState<PdfTarget | null>(null);
  const [approvalRow, setApprovalRow] = useState<StageTableRow | null>(null);

  const workspaceId = config?.workspaceId ?? stageId;

  useEffect(() => {
    setPage(1);
    setFilters({});
    setPdf(null);
    setApprovalRow(null);
  }, [workspaceId]);

  const columns: ColumnDef<StageTableRow>[] = useMemo(() => {
    if (!config) return [];
    return config.columns.map((spec) => buildColumnDef(spec, setPdf, setApprovalRow));
  }, [config]);

  const allRows = useMemo(() => loadStageTableRows(stageId), [stageId]);
  const total = allRows.length;
  const rows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return allRows.slice(start, start + pageSize);
  }, [allRows, page, pageSize]);

  const tableId = `po-demo:flow-stage:${workspaceId}`;
  const title = config?.label ?? stageLabel;

  function resetFilters() {
    setFilters({});
    setPage(1);
  }

  if (!config) {
    return (
      <div className="ep-flow-stage-table">
        <div className="ep-flow-stage-title">{stageLabel}</div>
        <div className="ep-empty">
          Konfigurasi kolom untuk tahap ini belum tersedia.
        </div>
      </div>
    );
  }

  return (
    <div className="ep-flow-stage-table">
      <div className="ep-flow-stage-title">{title}</div>
      <DataTable
        key={workspaceId}
        tableId={tableId}
        columns={columns}
        rows={rows}
        rowKey={(r) => r.key}
        enableColumnManager
        inlineColumnTools
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        emptyMessage={`Belum ada data untuk “${title}” (DEMO).`}
        filterSlot={(tools) => (
          <ColumnFilter
            value={filters}
            onChange={(next) => {
              setFilters({
                ...next,
                q: next.q?.trim() ? next.q : undefined,
              });
              setPage(1);
            }}
            onReset={resetFilters}
            compact
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

      <PdfViewerModal
        open={Boolean(pdf)}
        title={pdf?.title ?? "Preview PDF"}
        subtitle={pdf?.subtitle}
        src={pdf?.src ?? null}
        onClose={() => setPdf(null)}
      />

      <ApprovalLinkDemoModal
        open={Boolean(approvalRow)}
        poNo={approvalRow ? cellValue(approvalRow, "poNo") : ""}
        itemName={approvalRow ? cellValue(approvalRow, "itemName") : undefined}
        onClose={() => setApprovalRow(null)}
      />
    </div>
  );
}

function buildColumnDef(
  spec: StageColumnSpec,
  setPdf: (t: PdfTarget) => void,
  setApprovalRow: (row: StageTableRow) => void,
): ColumnDef<StageTableRow> {
  const isPoKey = spec.id === "poNo" || spec.id === "sjNo";

  if (spec.kind === "action-open-pdf") {
    return {
      id: spec.id,
      label: spec.label,
      hideable: true,
      filterType: "text",
      defaultWidth: spec.defaultWidth,
      getFilterValue: (row) => (cellValue(row, "pdfUrl") ? "Ada file" : "Tanpa file"),
      render: (row) => {
        const url = cellValue(row, "pdfUrl");
        const kind = cellValue(row, "pdfKind") || "dokumen";
        const po = cellValue(row, "poNo");
        const label = spec.id === "openPaymentFile" ? "Buka File" : "Buka Design";
        return (
          <button
            type="button"
            className="ep-btn sm"
            onClick={(e) => {
              e.stopPropagation();
              setPdf({
                src: url || null,
                title: kind === "payment" ? `File Pembayaran · ${po}` : `Design · ${po}`,
                subtitle: cellValue(row, "itemName") || "Dokumen DEMO",
              });
            }}
          >
            {label}
          </button>
        );
      },
    };
  }

  if (spec.kind === "action-approval-link") {
    return {
      id: spec.id,
      label: spec.label,
      hideable: true,
      filterType: "text",
      defaultWidth: spec.defaultWidth,
      getFilterValue: (row) => (cellValue(row, "pdfUrl") ? "Siap simulasi" : "Perlu file"),
      render: (row) => {
        const hasPdf = Boolean(cellValue(row, "pdfUrl"));
        return (
          <button
            type="button"
            className="ep-btn sm"
            disabled={!hasPdf}
            title={
              hasPdf
                ? "Simulasi buat link approval (DEMO)"
                : "Butuh file desain DEMO terlebih dahulu"
            }
            onClick={(e) => {
              e.stopPropagation();
              if (!hasPdf) return;
              setApprovalRow(row);
            }}
          >
            Buat Link Approval
          </button>
        );
      },
    };
  }

  if (spec.kind === "accurate-placeholder") {
    return {
      id: spec.id,
      label: spec.label,
      hideable: true,
      filterType: "text",
      defaultWidth: spec.defaultWidth,
      getFilterValue: () => "Belum terintegrasi",
      render: () => (
        <span className="ep-pill gray" title="Integrasi Accurate belum diimplementasikan">
          Belum terintegrasi
        </span>
      ),
    };
  }

  return {
    id: spec.id,
    label: spec.label,
    sticky: spec.sticky,
    hideable: true,
    filterType: spec.filterType,
    defaultWidth: spec.defaultWidth,
    minWidth: spec.minWidth,
    getFilterValue: (row) => cellValue(row, spec.id),
    getSortValue: (row) => {
      const raw = cellValue(row, spec.id);
      if (spec.filterType === "number") {
        const n = Number(raw.replace(/\./g, "").replace(",", "."));
        return Number.isFinite(n) ? n : raw;
      }
      return raw || null;
    },
    render: (row) => {
      const raw = cellValue(row, spec.id);
      if (!raw || raw === "—") return "—";
      if (spec.filterType === "date") return formatDateDisplay(raw);
      if (isPoKey) return <span className="key">{raw}</span>;
      if (ORDER_STATUS_IDS.has(spec.id)) {
        const urgent = raw === "Urgent";
        return (
          <span
            className={`ep-pill ep-pill-status ${urgent ? "order-urgent" : "order-normal"}`}
          >
            {raw}
          </span>
        );
      }
      if (BADGE_STATUS_IDS.has(spec.id)) {
        return <StatusBadge status={raw} />;
      }
      if (spec.filterType === "number" || spec.id === "totalQty") {
        return <b>{raw}</b>;
      }
      return raw;
    },
  };
}
