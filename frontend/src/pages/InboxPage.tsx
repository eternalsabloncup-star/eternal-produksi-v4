import { useEffect, useState } from "react";
import { DemoBanner } from "../components/DemoBanner";
import { KpiSummary, type KpiItem } from "../components/KpiSummary";
import { PageHeading } from "../components/PageHeading";
import { PoDemoTable } from "../components/PoDemoTable";
import { getDemoPoSource } from "../demo/DemoPoSource";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

export function InboxPage() {
  const [kpis, setKpis] = useState<KpiItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const m = await getDemoPoSource().metrics();
      if (cancelled) return;
      setKpis([
        { label: "Total PO", value: m.total, foot: "Semua status (DEMO)" },
        { label: "PO Baru", value: m.baru, foot: "Belum diproses" },
        {
          label: "Belum Terjadwal",
          value: m.unsched,
          foot: "Termasuk PO baru",
          kind: "warn",
        },
        { label: "Total Qty", value: formatQty(m.qty), foot: "pcs (DEMO)" },
        { label: "Sudah Dicetak", value: formatQty(m.printed), foot: "pcs (DEMO)" },
        {
          label: "Urgent",
          value: m.urgent,
          foot: "Periksa prioritas",
          kind: "danger",
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
        title="Inbox PO"
        subtitle="PO masuk dipantau sebelum dialokasikan ke penjadwalan (DEMO)."
      />
      <DemoBanner />
      <div className="ep-visualnotice">
        Aksi “Tambah PO / Jadwalkan / Approve” bisnis sengaja belum diaktifkan di Task
        #004 agar tidak menyerupai operasi backend nyata.
      </div>
      <KpiSummary items={kpis} />
      <PoDemoTable title="Daftar PO Masuk" showUrgentFilter />
    </div>
  );
}
