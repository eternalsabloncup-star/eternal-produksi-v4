import { useEffect, useState } from "react";
import { KpiSummary, type KpiItem } from "../components/KpiSummary";
import { ScheduleApiPanel } from "../components/ScheduleApiPanel";
import { ScheduleProductionTable } from "../components/ScheduleProductionTable";
import { getDemoPoSource } from "../demo/DemoPoSource";
import { resolveInboxPoSourceMode } from "../demo/resolveInboxMode";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

/** Halaman Jadwal Produksi (konten eks-Kontrol Produksi). */
export function ControlPage() {
  const apiMode =
    resolveInboxPoSourceMode(import.meta.env.VITE_INBOX_PO_SOURCE) === "api";
  const [kpis, setKpis] = useState<KpiItem[]>([]);

  useEffect(() => {
    if (apiMode) return;
    let cancelled = false;
    (async () => {
      const m = await getDemoPoSource().metrics();
      if (cancelled) return;
      setKpis([
        {
          label: "Total Qty SO",
          value: `${formatQty(m.qty)} pcs`,
          foot: "Akumulasi DEMO",
        },
        {
          label: "Qty Terjadwal",
          value: formatQty(m.scheduledQty),
          foot: "pcs teralokasi (DEMO)",
        },
        {
          label: "Sudah Dicetak",
          value: formatQty(m.printed),
          foot: "pcs aktual DEMO",
        },
        {
          label: "Belum Terjadwal",
          value: m.unsched,
          foot: "PO perlu alokasi",
          kind: "warn",
        },
        {
          label: "Dalam Produksi",
          value: m.produksi,
          foot: "PO sedang jalan",
        },
        {
          label: "Siap Kirim",
          value: m.ready,
          foot: "PO siap loading",
        },
      ]);
    })();
    return () => {
      cancelled = true;
    };
  }, [apiMode]);

  if (apiMode) {
    return <ScheduleApiPanel />;
  }

  return (
    <div>
      <KpiSummary items={kpis} />
      <ScheduleProductionTable />
    </div>
  );
}
