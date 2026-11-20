import { useMemo, useState } from "react";
import {
  ProductionFlowTrack,
  buildProductionFlowStepsWithSelesai,
  type ProductionFlowStep,
} from "../components/ProductionFlowTrack";
import { ProductionFlowApiPanel } from "../components/ProductionFlowApiPanel";

/**
 * Alur Produksi — monitoring PO per tahap (API Mongo V4).
 * Satu komponen production untuk preview sim maupun live; tanpa panel DEMO terpisah.
 */
export function ProductionFlowPage() {
  const steps = useMemo(() => buildProductionFlowStepsWithSelesai(), []);
  const [selected, setSelected] = useState<ProductionFlowStep>(steps[0]);

  return (
    <div className="ep-flow-page">
      <ProductionFlowTrack
        steps={steps}
        selectedId={selected.id}
        onSelect={setSelected}
      />
      <ProductionFlowApiPanel
        selectedStageId={selected.id}
        selectedStageLabel={selected.label}
        onStageFocus={setSelected}
      />
    </div>
  );
}
