import { useMemo } from "react";
import { BufferStockTable } from "../components/BufferStockTable";
import { KpiSummary, type KpiItem } from "../components/KpiSummary";
import { getBufferStockMetrics } from "../demo/bufferStockDemo";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

/** Halaman Buffer Stock — frontend DEMO saja. */
export function BufferStockPage() {
  const kpis = useMemo((): KpiItem[] => {
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
  }, []);

  return (
    <div>
      <KpiSummary items={kpis} />
      <BufferStockTable />
    </div>
  );
}
