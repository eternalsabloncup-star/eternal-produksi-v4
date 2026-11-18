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

export function buildProductionFlowSteps(): ProductionFlowStep[] {
  return PRODUCTION_FLOW_LABELS.map((label, i) => ({
    id: FLOW_STEP_IDS[i],
    label,
  }));
}

type Props = {
  steps: ProductionFlowStep[];
  selectedId: string;
  onSelect: (step: ProductionFlowStep) => void;
};

/** Visual tahapan horizontal — navigasi UI saja, tanpa status bisnis. */
export function ProductionFlowTrack({ steps, selectedId, onSelect }: Props) {
  return (
    <section className="ep-flow" aria-label="Tahapan alur produksi">
      <div className="ep-flow-edge" aria-hidden />

      <div className="ep-flow-rail">
        <div className="ep-flow-scroll">
          <ol className="ep-flow-track">
            {steps.map((step, index) => {
              const selected = step.id === selectedId;
              return (
                <li
                  key={step.id}
                  className={`ep-flow-step${selected ? " is-selected" : ""}`}
                >
                  <button
                    type="button"
                    className="ep-flow-step-btn"
                    aria-pressed={selected}
                    aria-current={selected ? "step" : undefined}
                    onClick={() => onSelect(step)}
                  >
                    <span className="ep-flow-node-wrap">
                      <span className="ep-flow-node" aria-hidden>
                        <span className="ep-flow-node-num">{index + 1}</span>
                      </span>
                    </span>
                    <span className="ep-flow-label">{step.label}</span>
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    </section>
  );
}
