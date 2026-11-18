import { useMemo, useState } from "react";
import {
  ProductionFlowTrack,
  buildProductionFlowSteps,
  type ProductionFlowStep,
} from "../components/ProductionFlowTrack";
import { ProductionFlowStageTable } from "../components/ProductionFlowStageTable";

/**
 * Halaman Alur Produksi — navigasi tahap + tabel PO (frontend DEMO).
 * Tanpa aturan bisnis, approval, atau write MongoDB.
 */
export function ProductionFlowPage() {
  const steps = useMemo(() => buildProductionFlowSteps(), []);
  const [selected, setSelected] = useState<ProductionFlowStep>(steps[0]);

  return (
    <div className="ep-flow-page">
      <ProductionFlowTrack
        steps={steps}
        selectedId={selected.id}
        onSelect={setSelected}
      />
      <ProductionFlowStageTable
        stageId={selected.id}
        stageLabel={selected.label}
      />
    </div>
  );
}
