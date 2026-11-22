import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchProductionFlowStageCounts,
} from "../api/analytics";
import {
  ProductionFlowTrack,
  buildProductionFlowCards,
  type ProductionFlowStep,
  type StageCountsStatus,
} from "../components/ProductionFlowTrack";
import { ProductionFlowApiPanel } from "../components/ProductionFlowApiPanel";
import type { PoFilters } from "../demo/types";

/**
 * Alur Produksi — monitoring PO per tahap (API Mongo V4).
 * Satu komponen production untuk preview sim maupun live; tanpa panel DEMO terpisah.
 */
export function ProductionFlowPage() {
  const steps = useMemo(() => buildProductionFlowCards(), []);
  const [selected, setSelected] = useState<ProductionFlowStep>(steps[0]);
  const [filters, setFilters] = useState<PoFilters>({});
  const [poFilterId, setPoFilterId] = useState("");
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [countsStatus, setCountsStatus] = useState<StageCountsStatus>("loading");
  const [countsError, setCountsError] = useState<string | undefined>();
  const [countsTick, setCountsTick] = useState(0);

  const refreshCounts = useCallback(() => {
    setCountsTick((n) => n + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setCountsStatus("loading");
    setCountsError(undefined);
    void (async () => {
      try {
        const res = await fetchProductionFlowStageCounts({
          q: filters.q,
          urgent: filters.urgent,
          accuratePoId: poFilterId || undefined,
        });
        if (cancelled) return;
        setCounts(res.counts || {});
        setCountsStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setCounts({});
        setCountsStatus("error");
        setCountsError(
          err instanceof Error ? err.message : "Gagal memuat jumlah PO per tahap.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [filters.q, filters.urgent, poFilterId, countsTick]);

  return (
    <div className="ep-flow-page">
      <ProductionFlowTrack
        steps={steps}
        selectedId={selected.id}
        onSelect={setSelected}
        counts={counts}
        countsStatus={countsStatus}
        countsError={countsError}
      />
      <ProductionFlowApiPanel
        selectedStageId={selected.id}
        selectedStageLabel={selected.label}
        onStageFocus={setSelected}
        filters={filters}
        onFiltersChange={setFilters}
        poFilterId={poFilterId}
        onPoFilterIdChange={setPoFilterId}
        onDataChanged={refreshCounts}
      />
    </div>
  );
}
