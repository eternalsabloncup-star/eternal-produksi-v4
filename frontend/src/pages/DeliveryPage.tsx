import { DeliveryApiPanel } from "../components/DeliveryApiPanel";
import { DeliveryScheduleTable } from "../components/DeliveryScheduleTable";
import { resolveInboxPoSourceMode } from "../demo/resolveInboxMode";

/** Jadwal Pengiriman: SJ Accurate nyata di mode API, DEMO lokal di mode demo. */
export function DeliveryPage() {
  const apiMode =
    resolveInboxPoSourceMode(import.meta.env.VITE_INBOX_PO_SOURCE) === "api";
  return <div>{apiMode ? <DeliveryApiPanel /> : <DeliveryScheduleTable />}</div>;
}
