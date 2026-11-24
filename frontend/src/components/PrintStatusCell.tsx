import type { DeliveryPrintStatus } from "../api/delivery";
import { printStatusHasNumbers, qtyText } from "../lib/deliverySj";

const TONE: Record<DeliveryPrintStatus["state"], string> = {
  belum: "is-belum",
  proses: "is-proses",
  selesai: "is-selesai",
  non_cetak: "is-non",
  unavailable: "is-unavailable",
};

/** Status Cetak: angka dari payload jadwal, atau label kosong bila belum terpetakan. */
export function PrintStatusCell({
  status,
}: {
  status: DeliveryPrintStatus | null | undefined;
}) {
  if (!printStatusHasNumbers(status)) {
    const label = status?.label?.trim() || "Belum tersedia";
    const tone = status ? TONE[status.state] : "is-unavailable";
    return (
      <div className="ep-print-status">
        <span className={`ep-pill ep-print-status-pill ${tone}`}>{label}</span>
      </div>
    );
  }

  const pct = status.percent;
  const bar = Math.max(0, Math.min(100, pct));
  const meterMax = Math.max(100, pct);
  return (
    <div className="ep-print-status">
      <span className="ep-print-status-qty">
        {qtyText(status.qty_confirmed)} / {qtyText(status.qty_reference)} {status.unit}
      </span>
      <span className="ep-print-status-meter">
        <span
          className="ep-progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuenow={pct}
          aria-valuemax={meterMax}
          aria-label={`${status.label}, ${pct} persen`}
        >
          <span style={{ width: `${bar}%` }} />
        </span>
        <span>{pct}%</span>
      </span>
      <span className={`ep-pill ep-print-status-pill ${TONE[status.state]}`}>
        {status.label}
      </span>
    </div>
  );
}
