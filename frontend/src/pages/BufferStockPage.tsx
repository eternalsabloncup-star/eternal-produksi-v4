import { useMemo } from "react";
import { BufferStockApiPanel } from "../components/BufferStockApiPanel";
import { BufferStockTable } from "../components/BufferStockTable";
import { KpiSummary, type KpiItem } from "../components/KpiSummary";
import { getBufferStockMetrics } from "../demo/bufferStockDemo";
import { resolveInboxPoSourceMode } from "../demo/resolveInboxMode";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

/** Buffer Stock: atribut Accurate nyata di mode API, DEMO lokal di mode demo. */
export function BufferStockPage() {
  const apiMode =
    resolveInboxPoSourceMode(import.meta.env.VITE_INBOX_PO_SOURCE) === "api";

  const kpis = useMemo((): KpiItem[] => {
    if (apiMode) return [];
    const m = getBufferStockMetrics();
    return [
      {
        label: "Total Buffer Dipesan",
        value: formatQty(m.totalOrdered),
        foot: `${m.poCount} PO · ${m.itemCount} item (DEMO)`,
      },
      {
        label: "Sedang Dikerjakan (Mesin)",
        value: formatQty(m.inMachine),
        foot: "Sisa belum dicetak (DEMO)",
      },
      {
        label: "Ready di Rak Gudang",
        value: formatQty(m.readyWarehouse),
        foot: "Siap ambil (DEMO)",
      },
      {
        label: "Sudah Diambil / Terkirim",
        value: formatQty(m.takenShipped),
        foot: "Keluar gudang (DEMO)",
      },
    ];
  }, [apiMode]);

  if (apiMode) {
    return <BufferStockApiPanel />;
  }

  return (
    <div>
      <KpiSummary items={kpis} />
      <BufferStockTable />
    </div>
  );
}
