import { useMemo, useState } from "react";
import {
  ProductionFlowTrack,
  buildProductionFlowSteps,
  type ProductionFlowStep,
} from "../components/ProductionFlowTrack";
import { ProductionFlowApiPanel } from "../components/ProductionFlowApiPanel";
import { ProductionFlowStageTable } from "../components/ProductionFlowStageTable";
import { resolveInboxPoSourceMode } from "../demo/resolveInboxMode";

/** Alur Produksi: state backend nyata di mode API, DEMO lokal di mode demo. */
export function ProductionFlowPage() {
  const apiMode =
    resolveInboxPoSourceMode(import.meta.env.VITE_INBOX_PO_SOURCE) === "api";
  const steps = useMemo(() => buildProductionFlowSteps(), []);
  const [selected, setSelected] = useState<ProductionFlowStep>(steps[0]);

  if (apiMode) {
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
