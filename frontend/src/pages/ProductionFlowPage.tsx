import { useMemo, useState } from "react";
import {
  ProductionFlowTrack,
  buildProductionFlowSteps,
  type ProductionFlowStep,
} from "../components/ProductionFlowTrack";
import { ProductionFlowApiPanel } from "../components/ProductionFlowApiPanel";
import { ProductionFlowStageTable } from "../components/ProductionFlowStageTable";
import { useProductionApiPanels } from "../sim/previewMode";

/** Alur Produksi — preview Cursor memakai ProductionFlowApiPanel. */
export function ProductionFlowPage() {
  const apiPanels = useProductionApiPanels();
  const steps = useMemo(() => buildProductionFlowSteps(), []);
  const [selected, setSelected] = useState<ProductionFlowStep>(steps[0]);

  if (apiPanels) {
    return (
      <div className="ep-flow-page">
        <ProductionFlowTrack
          steps={steps}
          selectedId={selected.id}
          onSelect={setSelected}
        />
        <ProductionFlowApiPanel
          selectedStageId={selected.id}
          onStageFocus={setSelected}
        />
      </div>
    );
  }

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
