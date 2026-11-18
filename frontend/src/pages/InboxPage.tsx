import { useEffect, useState } from "react";
import { DemoBanner } from "../components/DemoBanner";
import { InboxPoTable } from "../components/InboxPoTable";
import { KpiSummary, type KpiItem } from "../components/KpiSummary";
import { PageHeading } from "../components/PageHeading";
import { PoApiError } from "../demo/ApiPoSource";
import {
  getInboxPoSource,
  resolveInboxPoSourceMode,
} from "../demo/DemoPoSource";
import "../styles/inbox.css";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

export function InboxPage() {
  const sourceMode = resolveInboxPoSourceMode(
    import.meta.env.VITE_INBOX_PO_SOURCE,
  );
  const [kpis, setKpis] = useState<KpiItem[]>([]);
  const [kpiError, setKpiError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setKpiError(null);
      try {
        const m = await getInboxPoSource().metrics();
        if (cancelled) return;
        const footTotal =
          sourceMode === "api" ? "Accurate · Mongo V4" : "Semua status (DEMO)";
        const footQty = sourceMode === "api" ? "dari item Accurate" : "pcs (DEMO)";
        setKpis([
          { label: "Total PO", value: m.total, foot: footTotal },
          { label: "PO Baru", value: m.baru, foot: "Belum diproses" },
          {
            label: "Belum Terjadwal",
            value: m.unsched,
            foot: "Termasuk PO baru",
            kind: "warn",
          },
          { label: "Total Qty", value: formatQty(m.qty), foot: footQty },
          {
            label: "Sudah Dicetak",
            value: formatQty(m.printed),
            foot: sourceMode === "api" ? "belum dari Accurate" : "pcs (DEMO)",
          },
          {
            label: "Urgent",
            value: m.urgent,
            foot: "Periksa prioritas",
            kind: "danger",
          },
        ]);
      } catch (err) {
        if (cancelled) return;
        setKpis([]);
        if (err instanceof PoApiError) setKpiError(err.message);
        else if (err instanceof Error) setKpiError(err.message);
        else setKpiError("Gagal memuat ringkasan Inbox.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceMode]);

  return (
    <div>
      <PageHeading
        title="Inbox PO"
        subtitle={
          sourceMode === "api"
            ? "PO Accurate dari Mongo V4 — read-only, dipantau sebelum penjadwalan."
            : "PO masuk dipantau sebelum dialokasikan ke penjadwalan (DEMO)."
        }
      />
      {sourceMode === "demo" ? <DemoBanner /> : null}
      {sourceMode === "demo" ? (
        <div className="ep-visualnotice">
          Aksi “Tambah PO / Jadwalkan / Approve” bisnis sengaja belum diaktifkan di
          Task #004 agar tidak menyerupai operasi backend nyata.
        </div>
      ) : (
        <div className="ep-visualnotice">
          Sumber Accurate · Inbox tidak menulis Accurate Online. Sync hanya lewat
          prosedur manual Owner.
        </div>
      )}
      {kpiError ? (
        <div className="ep-inbox-error" role="alert">
          {kpiError}
        </div>
      ) : (
        <KpiSummary items={kpis} />
      )}
      <InboxPoTable />
    </div>
  );
}
