import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DemoBanner } from "../components/DemoBanner";
import { KpiSummary, type KpiItem } from "../components/KpiSummary";
import { PageHeading } from "../components/PageHeading";
import { PoDemoTable } from "../components/PoDemoTable";
import { getDemoPoSource } from "../demo/DemoPoSource";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

export function ControlPage() {
  const [kpis, setKpis] = useState<KpiItem[]>([]);

  useEffect(() => {
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
  }, []);

  return (
    <div>
      <PageHeading
        title="Kontrol Produksi"
        subtitle="Satu layar untuk memantau progres, antrean, dan hasil produksi (DEMO)."
        actions={
          <Link className="ep-btn primary" to="/app/schedule">
            Jadwal Produksi
          </Link>
        }
      />
      <DemoBanner />
      <KpiSummary items={kpis} />
      <PoDemoTable
        title="Monitoring Seluruh PO"
        showMachineFilter
        showUrgentFilter
      />
    </div>
  );
}
