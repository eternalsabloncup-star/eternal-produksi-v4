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
        if (sourceMode === "api") {
          // KPI jujur: tanpa klaim cetak / penjadwalan.
          setKpis([
            {
              label: "Total PO",
              value: m.total,
              foot: "Accurate · MongoDB V4",
            },
            {
              label: "PO di Inbox",
              value: m.baru ?? m.total,
              foot: "Status awal · belum dialirkan modul lain",
            },
            {
              label: "Total Qty",
              value: formatQty(m.qty),
              foot: "dari baris item Accurate",
            },
            {
              label: "Baris Item",
              value: m.lines ?? 0,
              foot: "jumlah line Accurate",
            },
            {
              label: "Urgent",
              value: m.urgent,
              foot: "flag header (jika ada)",
              kind: "danger",
            },
          ]);
        } else {
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
            {
              label: "Sudah Dicetak",
              value: formatQty(m.printed),
              foot: "pcs (DEMO)",
            },
            {
              label: "Urgent",
              value: m.urgent,
              foot: "Periksa prioritas",
              kind: "danger",
            },
          ]);
        }
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
            ? "PO Accurate dari MongoDB V4 — read-only, dipantau sebelum penjadwalan."
            : "PO masuk dipantau sebelum dialokasikan ke penjadwalan (DEMO)."
        }
      />
      {sourceMode === "demo" ? <DemoBanner /> : null}
      {sourceMode === "demo" ? (
        <div className="ep-visualnotice">
          Mode DEMO — aksi bisnis menulis Accurate/Mongo tidak aktif. Terima PO
          hanya untuk PO Accurate di mode API.
        </div>
      ) : (
        <div className="ep-visualnotice">
          Sumber Accurate · MongoDB V4 (read-only Accurate Online). Terima PO
          mengaktifkan workflow Eternal. Penjadwalan dan progres di modul terkait.
          Sync SJ live tetap OFF.
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
