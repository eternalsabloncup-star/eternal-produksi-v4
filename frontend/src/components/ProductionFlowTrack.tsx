export type ProductionFlowStep = {
  id: string;
  label: string;
};

/** Urutan tahap Alur Produksi — nama & urutan wajib persis (Owner). */
export const PRODUCTION_FLOW_LABELS = [
  "Order Masuk",
  "Design",
  "Approval Design",
  "Payment",
  "Approval Payment",
  "Penjadwalan",
  "Screener",
  "Persiapan",
  "Operator",
  "Konfirmasi Sample",
  "Cetak",
  "Packing",
  "Surat Jalan",
  "Loading",
  "Pengiriman",
] as const;

/** ID UI stabil per label — tidak menyamakan dengan tahap lama. */
const FLOW_STEP_IDS = [
  "flow-order-masuk",
  "flow-design",
  "flow-approval-design",
  "flow-payment",
  "flow-approval-payment",
  "flow-penjadwalan",
  "flow-screener",
  "flow-persiapan",
  "flow-operator",
  "flow-konfirmasi-sample",
  "flow-cetak",
  "flow-packing",
  "flow-surat-jalan",
  "flow-loading",
  "flow-pengiriman",
] as const;

export const FLOW_ALL_STEP: ProductionFlowStep = {
  id: "flow-all",
  label: "Semua",
};

export const FLOW_SELESAI_STEP: ProductionFlowStep = {
  id: "flow-selesai",
  label: "Selesai",
};

/** Baris 1: 9 card (Semua + 8 tahap). Baris 2: 8 card (7 tahap + Selesai). */
export const FLOW_CHIP_ROW1_COUNT = 9;

export type StageCountsStatus = "loading" | "ready" | "error";

export function buildProductionFlowSteps(): ProductionFlowStep[] {
  return PRODUCTION_FLOW_LABELS.map((label, i) => ({
    id: FLOW_STEP_IDS[i],
    label,
  }));
}

export function buildProductionFlowStepsWithSelesai(): ProductionFlowStep[] {
  return [...buildProductionFlowSteps(), FLOW_SELESAI_STEP];
}

/** 17 card: Semua + 15 tahap + Selesai. */
export function buildProductionFlowCards(): ProductionFlowStep[] {
  return [FLOW_ALL_STEP, ...buildProductionFlowSteps(), FLOW_SELESAI_STEP];
}

export function splitProductionFlowCardRows(
  steps: ProductionFlowStep[],
): [ProductionFlowStep[], ProductionFlowStep[]] {
  const row1 = steps.slice(0, FLOW_CHIP_ROW1_COUNT);
  const row2 = steps.slice(FLOW_CHIP_ROW1_COUNT);
  return [row1, row2];
}

export function formatStageCountBadge(
  count: number | undefined,
  status: StageCountsStatus,
): string {
  if (status === "loading") return "…";
  if (status === "error") return "?";
  const n = Number(count);
  if (!Number.isFinite(n) || n <= 0) return "0";
  return String(Math.trunc(n));
}

type Props = {
  steps: ProductionFlowStep[];
  selectedId: string;
  onSelect: (step: ProductionFlowStep) => void;
  counts?: Record<string, number>;
  countsStatus?: StageCountsStatus;
  countsError?: string;
};

/** Card 2-baris — navigasi UI; badge = PO unik aktif (bukan task/role). */
export function ProductionFlowTrack({
  steps,
  selectedId,
  onSelect,
  counts,
  countsStatus = "ready",
  countsError,
}: Props) {
  const [row1, row2] = splitProductionFlowCardRows(steps);

  function renderChip(step: ProductionFlowStep) {
    const selected = step.id === selectedId;
    const count = counts?.[step.id] ?? 0;
    const badge = formatStageCountBadge(count, countsStatus);
    const isZero =
      countsStatus === "ready" && (!Number.isFinite(count) || count <= 0);
    return (
      <button
        key={step.id}
        type="button"
        role="listitem"
        className={`ep-flow-chip${selected ? " is-selected" : ""}`}
        aria-pressed={selected}
        onClick={() => onSelect(step)}
        title={step.label}
        data-testid={`flow-chip-${step.id}`}
      >
        <span className="ep-flow-chip-inner">
          <span className="ep-flow-chip-label">{step.label}</span>
          <span
            className={`ep-flow-chip-badge${isZero ? " is-zero" : ""}`}
            aria-label={
              countsStatus === "error"
                ? countsError || "Jumlah tidak tersedia"
                : countsStatus === "loading"
                  ? "Memuat…"
                  : `${badge} PO pada ${step.label}`
            }
            data-testid={`flow-badge-${step.id}`}
            data-status={countsStatus}
          >
            {badge}
          </span>
        </span>
      </button>
    );
  }

  return (
    <section className="ep-flow" aria-label="Tahapan alur produksi">
      <div className="ep-flow-edge" aria-hidden />
      {countsStatus === "error" ? (
        <p className="ep-error" role="alert" data-testid="flow-counts-error">
          {countsError || "Jumlah PO per tahap tidak dapat dimuat."}
        </p>
      ) : null}
      <div
        className="ep-flow-chipscroll"
        data-testid="flow-stage-scroll"
        tabIndex={0}
        aria-label="Gulir tahapan alur produksi"
      >
        <div className="ep-flow-chiprows" role="list">
          <div
            className="ep-flow-chiprow"
            data-cols={row1.length}
            data-testid="flow-stage-row-1"
          >
            {row1.map(renderChip)}
          </div>
          <div
            className="ep-flow-chiprow"
            data-cols={row2.length}
            data-testid="flow-stage-row-2"
          >
            {row2.map(renderChip)}
          </div>
        </div>
      </div>
    </section>
  );
}
